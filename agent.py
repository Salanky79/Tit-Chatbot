import os
from collections.abc import Iterable
from typing import Annotated, Any
from typing_extensions import TypedDict

from dotenv import load_dotenv
import certifi                  # Cung cấp chứng chỉ SSL/TLS chuẩn để verify HTTPS an toàn

load_dotenv()

# Trỏ chứng chỉ SSL đến file chứng chỉ của certifi để tránh lỗi SSL khi gọi API bên ngoài
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from langchain_groq import ChatGroq
from langchain_core.callbacks import BaseCallbackHandler
# Định dạng tin nhắn hệ thống (System Prompt) cho mô hình
from langchain_core.messages import SystemMessage, BaseMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
# ToolNode: Node chạy các công cụ; tools_condition: Điều kiện rẽ nhánh kiểm tra xem LLM có gọi tool không
from langgraph.prebuilt import ToolNode, tools_condition
# MongoDBSaver: Lưu trạng thái và lịch sử hội thoại vào MongoDB để duy trì ngữ cảnh nhiều lượt chat
from langgraph.checkpoint.mongodb import MongoDBSaver
from storage.mongo_client import get_client
from prompts.agent import SYSTEM_PROMPT
from prompts.personalization import BASE_STYLE_PROMPTS, build_personalization_prompt_suffix
from tools import tools, SUMMARY_RESULT_PREFIX, SUMMARY_RESULT_PREFIX_VI
from agent_tools.context import (
    SUMMARY_ERROR_PREFIX,
    get_current_personalization,
    get_current_project_context,
    get_current_project_instructions,
    is_project_chat,
)
from services.context_manager import prepare_context_for_llm
from services.token_limit_handler import invoke_with_rate_limit_retry
from utils.usage_logging import log_token_usage



# =============================================================================
# 01. Trạng thái đồ thị và theo dõi output
# =============================================================================

class AgentState(TypedDict, total=False):
    """Trạng thái hội thoại mở rộng cho Agentic Chatbot:

    - messages: Danh sách tin nhắn trò chuyện được cộng dồn (add_messages).
    - conversation_summary: Ghi chú cũ trong checkpoint, được giữ để tương thích.
    """
    # Lịch sử được lưu đầy đủ; context_manager chọn lượt cần gửi ở mỗi lần gọi model.
    messages: Annotated[list[BaseMessage], add_messages]
    conversation_summary: str

# kiểm tra nếu chưa in ra chữ nào thì mới chạy lại tư đầu
class _OutputStartedTracker(BaseCallbackHandler):
    """Track streamed model output (text or reasoning) so a late failure cannot replay visible tokens."""

    def __init__(self) -> None:
        self.output_started = False

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        if token:
            self.output_started = True
            return
        chunk = kwargs.get("chunk")
        msg = getattr(chunk, "message", None)
        additional_kwargs = getattr(msg, "additional_kwargs", None) or {}
        if additional_kwargs.get("reasoning_content") or additional_kwargs.get("reasoning"):
            self.output_started = True



# =============================================================================
# 02. Dọn checkpoint theo cuộc trò chuyện
# =============================================================================

def delete_user_checkpoints(thread_ids: Iterable[str]) -> int:
    """Xóa checkpoint LangGraph thuộc danh sách thread của user."""
    unique_thread_ids = dict.fromkeys(thread_id for thread_id in thread_ids if thread_id)
    if not unique_thread_ids:
        return 0

    checkpointer = MongoDBSaver(
        client=get_client(),
        db_name=os.getenv("MONGODB_CHECKPOINT_DB", "tit_chatbot_checkpoints"),
    )
    for thread_id in unique_thread_ids:
        checkpointer.delete_thread(thread_id)
    return len(unique_thread_ids)


# Trả thẳng bản tóm tắt tài liệu cho người dùng, bỏ lượt gọi LLM thứ 2 (vốn chỉ để chép lại
# đúng bản tóm tắt đó: tốn ~5k token TPM + vài giây). Đặt SUMMARY_DIRECT_RETURN=0 để tắt.
SUMMARY_DIRECT_RETURN = os.getenv("SUMMARY_DIRECT_RETURN", "1").lower() not in ("0", "false", "no")
SUMMARY_TOOL_NAME = "summarize_uploaded_document"



# =============================================================================
# 03. Định tuyến và trả kết quả tóm tắt
# =============================================================================

def _summary_tool_results(state: AgentState) -> list[ToolMessage]:
    """Read summary outcomes only from the most recent tool batch."""
    results = []
    for message in reversed(state.get("messages", [])):
        if not isinstance(message, ToolMessage):
            break
        if message.name == SUMMARY_TOOL_NAME:
            results.append(message)
    return list(reversed(results))


def _summary_status(message: ToolMessage) -> str | None:
    artifact = message.artifact
    if isinstance(artifact, dict) and artifact.get("kind") == "document_summary":
        return artifact.get("status")
    # Compatibility with older persisted tool messages.
    if isinstance(message.content, str):
        if message.content.startswith(SUMMARY_ERROR_PREFIX):
            return "error"
        if message.content.startswith((SUMMARY_RESULT_PREFIX, SUMMARY_RESULT_PREFIX_VI)):
            return "completed"
    return None


def route_after_tools(state: AgentState) -> str:
    results = _summary_tool_results(state)
    # Failures and clarification must not start an automatic retry loop.
    if any(_summary_status(message) in {"error", "clarification"} for message in results):
        return "emit_summary"
    if SUMMARY_DIRECT_RETURN and results and all(
        _summary_status(message) in {"completed", "partial"} for message in results
    ):
        return "emit_summary"
    return "chatbot"


def emit_summary_node(state: AgentState) -> dict[str, list[AIMessage]]:
    """Deliver tool outcomes verbatim and keep them available for follow-up turns."""
    results = _summary_tool_results(state)
    return {"messages": [AIMessage(content="\n\n".join(str(message.content) for message in results))]}


# Update default and allowed models to use Groq models
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
try:
    CHAT_RATE_LIMIT_MAX_RETRIES = max(0, int(os.getenv("CHAT_RATE_LIMIT_MAX_RETRIES", "3")))
except ValueError:
    CHAT_RATE_LIMIT_MAX_RETRIES = 3

ALLOWED_MODELS = {
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
}






# =============================================================================
# 04. Lựa chọn và cấu hình model
# =============================================================================

def normalize_model_name(model_name: str | None) -> str:
    """
    HÀM 1: CHUẨN HÓA VÀ KIỂM TRA TÊN MODEL (Validation & Fallback)
    - Tác dụng:
      + Nhận tên model mà người dùng / frontend truyền lên.
      + Loại bỏ khoảng trắng thừa.
      + Kiểm tra xem model có nằm trong danh sách cho phép (ALLOWED_MODELS) hay không.
      + Nếu không truyền hoặc tên model không hợp lệ, tự động fallback về DEFAULT_MODEL để tránh gây lỗi hệ thống.
    """

    if not model_name:
        return DEFAULT_MODEL

    model_name = model_name.strip()

    if model_name not in ALLOWED_MODELS:
        return DEFAULT_MODEL

    return model_name



# xây dựng format reasoning kwargs cho từng model Groq
def _build_reasoning_kwargs(model_name: str) -> dict[str, Any]:
    """Tạo tham số reasoning phù hợp với từng dòng model Groq (tránh lỗi 400)."""
    kwargs: dict[str, Any] = {}
    effort = os.getenv("GROQ_REASONING_EFFORT", "").strip().lower()
    if model_name.startswith("openai/gpt-oss"):
        # Nhóm gpt-oss tự stream delta.reasoning và nhận tham số reasoning_effort.
        if effort in {"low", "medium", "high"}:
            kwargs["reasoning_effort"] = effort
    elif model_name.startswith("qwen/"):
        # Nhóm Qwen tách reasoning sang additional_kwargs.reasoning_content khi đặt parsed.
        kwargs["reasoning_format"] = os.getenv("GROQ_REASONING_FORMAT", "parsed").strip() or "parsed"
    return kwargs



# =============================================================================
# 05. Xây dựng đồ thị agent
# =============================================================================

def build_agent(model_name: str) -> Any:
    """
    HÀM 2: KHỞI TẠO VÀ BIÊN DỊCH AGENT CHO MỘT MODEL CỤ THỂ (LangGraph Agent Builder)
    - Tác dụng:
      + Tạo kết nối với mô hình GROQ đã chọn thông qua ChatGroq.
      + Gắn (bind) danh sách các công cụ (tools) vào model để mô hình biết nó có thể gọi tool nào.
      + Xây dựng đồ thị trạng thái hội thoại (StateGraph):
        * chatbot_node: Node tiếp nhận tin nhắn, ghép System Prompt và gọi LLM trả lời hoặc yêu cầu gọi tool.
        * tool_node: Node tiếp nhận lệnh và thực thi các tool khi chatbot_node yêu cầu.
        * Luồng chạy: START -> chatbot -> kiểm tra tools_condition:
          - Nếu cần gọi tool -> chuyển sang node 'tools' -> chạy xong quay về 'chatbot' để tổng hợp câu trả lời.
          - Nếu không cần gọi tool -> kết thúc lượt xử lý.
      + Thiết lập MongoDBSaver để lưu trạng thái phiên chat (checkpoint/memory) vào MongoDB.
      + Trả về workflow đã được compile sẵn sàng hoạt động.
    """

    # Đảm bảo tên model luôn hợp lệ
    selected_model = normalize_model_name(model_name)

    llm = ChatGroq(
        model=selected_model,
        temperature=0.3,    # Giảm tính sáng tạo ngẫu nhiên, ưu tiên câu trả lời chính xác, bám sát sự thật
        streaming=True,     # Hỗ trợ stream phản hồi theo thời gian thực về giao diện
        **_build_reasoning_kwargs(selected_model),
    )

    # Khai báo các công cụ (tools) cho mô hình để LLM có thể chủ động kích hoạt Function Calling
    llm_with_tools = llm.bind_tools(tools)

    def chatbot_node(state: AgentState) -> dict[str, Any]:
        """
        Chọn các lượt hội thoại hoàn chỉnh cần thiết, bỏ tool results cũ,
        rồi giới hạn prompt theo ngân sách cấu hình trước khi gọi LLM.
        """
        existing_summary = state.get("conversation_summary", "")

        dynamic_system_prompt = SYSTEM_PROMPT
        if is_project_chat():
            # Trong Project: chỉ áp dụng Project Instructions & Context, cách ly hoàn toàn với Personalization cá nhân.
            instructions = get_current_project_instructions().strip()
            project_context = get_current_project_context().strip()
            if instructions:
                dynamic_system_prompt += (
                    "\n\n[Instructions for this project]\n"
                    f"{instructions}\n"
                    "[End project instructions]"
                )
            if project_context:
                dynamic_system_prompt += (
                    "\n\n[Relevant context from this project only]\n"
                    f"{project_context}\n"
                    "[End project context]"
                )
        else:
            # Ngoài Project (chat cá nhân): áp dụng Base style & tone (~12-20 tokens) và Custom Instructions của user.
            personalization = get_current_personalization()
            dynamic_system_prompt += build_personalization_prompt_suffix(
                personalization.get("base_style"),
                personalization.get("custom_instructions"),
            )

        final_messages, updated_summary = prepare_context_for_llm(
            messages=state.get("messages", []),
            system_prompt=dynamic_system_prompt,
            tools=tools,
            existing_summary=existing_summary,
            model_name=selected_model,
        )

        output_tracker = _OutputStartedTracker()
        response = invoke_with_rate_limit_retry(
            lambda: llm_with_tools.invoke(
                final_messages,
                config={"callbacks": [output_tracker]},
            ),
            max_retries=CHAT_RATE_LIMIT_MAX_RETRIES,
            operation=f"Chatbot ({selected_model})",
            can_retry=lambda: not output_tracker.output_started,
        )

        task_desc = "Tool Call Request" if getattr(response, "tool_calls", None) else "Chatbot Answer"
        log_token_usage(selected_model, task_desc, response)

        return {
            "messages": [response],
            "conversation_summary": updated_summary,
        }

    # Node phụ trách thực thi các tool khi mô hình yêu cầu
    tool_node = ToolNode(tools)

    # Khởi tạo đồ thị trạng thái quản lý AgentState
    workflow = StateGraph(AgentState)

    # Đăng ký các node vào đồ thị
    workflow.add_node("chatbot", chatbot_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("emit_summary", emit_summary_node)

    # Thiết lập luồng di chuyển giữa các node:
    # 1. Bắt đầu từ START -> đi vào node 'chatbot'
    workflow.add_edge(START, "chatbot")
    # 2. Điều kiện rẽ nhánh: nếu LLM yêu cầu gọi tool -> chuyển sang node 'tools', nếu không -> kết thúc lượt
    workflow.add_conditional_edges("chatbot", tools_condition) # mac dinh tim node ten la tools
    # 3. Sau khi node 'tools' chạy xong -> trả kết quả về lại node 'chatbot' để LLM đọc và trả lời người dùng.
    #    Riêng tool tóm tắt tài liệu: trả thẳng bản tóm tắt (emit_summary -> END), khỏi gọi LLM lần nữa.
    workflow.add_conditional_edges(
        "tools", route_after_tools, {"chatbot": "chatbot", "emit_summary": "emit_summary"}
    )
    workflow.add_edge("emit_summary", END)

    # Lấy MongoDB client singleton từ mongo_client để tái sử dụng kết nối
    mongo_client = get_client()

    # Tên database riêng cho LangGraph checkpoints (tách biệt với data chính)
    checkpoint_db_name = os.getenv("MONGODB_CHECKPOINT_DB", "tit_chatbot_checkpoints")

    # Tạo checkpointer dùng MongoDB để lưu và khôi phục trạng thái cuộc trò chuyện theo session
    checkpointer = MongoDBSaver(
        client=mongo_client,
        db_name=checkpoint_db_name,
    )

    # Biên dịch đồ thị luồng làm việc thành runnable agent có gắn bộ nhớ checkpoint
    return workflow.compile(checkpointer=checkpointer)


# Bộ nhớ đệm (Cache) lưu trữ các agent đã được khởi tạo để tái sử dụng, tránh tạo lại nhiều lần gây tốn tài nguyên
_AGENT_CACHE: dict[str, Any] = {}



# =============================================================================
# 06. Cache và cung cấp agent
# =============================================================================

def get_agent(model_name: str | None = None) -> Any:
    """
    HÀM 3: LẤY HOẶC TẠO MỚI AGENT THEO CƠ CHẾ CACHING (Agent Factory / Singleton Pattern)
    - Tác dụng:
      + Kiểm tra xem Agent ứng với `selected_model` đã từng được khởi tạo trong `_AGENT_CACHE` chưa.
      + Nếu ĐÃ CÓ: Lấy ngay từ cache ra dùng (không mất thời gian kết nối và biên dịch lại).
      + Nếu CHƯA CÓ: Gọi hàm `build_agent(selected_model)` để tạo mới, lưu vào cache rồi trả về.
      + Đảm bảo mỗi loại model chỉ cần compile đồ thị 1 lần duy nhất trong suốt vòng đời ứng dụng.
    """

    selected_model = normalize_model_name(model_name)

    # Nếu model này chưa từng được tạo agent thì khởi tạo và đưa vào cache
    if selected_model not in _AGENT_CACHE:
        _AGENT_CACHE[selected_model] = build_agent(selected_model)

    # Trả về instance agent từ bộ nhớ cache
    return _AGENT_CACHE[selected_model]
