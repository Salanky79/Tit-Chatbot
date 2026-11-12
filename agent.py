import os
from pathlib import Path
from typing import Annotated
from typing_extensions import TypedDict

from dotenv import load_dotenv
import certifi                  # Cung cấp chứng chỉ SSL/TLS chuẩn để verify HTTPS an toàn

load_dotenv()

# Trỏ chứng chỉ SSL đến file chứng chỉ của certifi để tránh lỗi SSL khi gọi API bên ngoài
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from langchain_groq import ChatGroq
# Định dạng tin nhắn hệ thống (System Prompt) cho mô hình
from langchain_core.messages import SystemMessage, BaseMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
# ToolNode: Node chạy các công cụ; tools_condition: Điều kiện rẽ nhánh kiểm tra xem LLM có gọi tool không
from langgraph.prebuilt import ToolNode, tools_condition
# MongoDBSaver: Lưu trạng thái và lịch sử hội thoại vào MongoDB để duy trì ngữ cảnh nhiều lượt chat
from langgraph.checkpoint.mongodb import MongoDBSaver
from mongo_client import get_client
from tools import tools, SUMMARY_RESULT_PREFIX
from context_manager import prepare_context_for_llm
from usage_logging import log_token_usage


class AgentState(TypedDict, total=False):
    """Trạng thái hội thoại mở rộng cho Agentic Chatbot:

    - messages: Danh sách tin nhắn trò chuyện được cộng dồn (add_messages).
    - conversation_summary: Ghi chú cũ trong checkpoint, được giữ để tương thích.
    """
    # Lịch sử được lưu đầy đủ; context_manager chọn lượt cần gửi ở mỗi lần gọi model.
    messages: Annotated[list[BaseMessage], add_messages]
    conversation_summary: str


# Trả thẳng bản tóm tắt tài liệu cho người dùng, bỏ lượt gọi LLM thứ 2 (vốn chỉ để chép lại
# đúng bản tóm tắt đó: tốn ~5k token TPM + vài giây). Đặt SUMMARY_DIRECT_RETURN=0 để tắt.
SUMMARY_DIRECT_RETURN = os.getenv("SUMMARY_DIRECT_RETURN", "1").lower() not in ("0", "false", "no")
SUMMARY_TOOL_NAME = "summarize_uploaded_document"


def route_after_tools(state: AgentState) -> str:
    """Sau node 'tools': nếu vừa chạy đúng 1 tool tóm tắt và nó thành công thì kết thúc luôn
    (qua node 'emit_summary'); còn lại quay về 'chatbot' như cũ."""
    if not SUMMARY_DIRECT_RETURN:
        return "chatbot"
    recent: list[ToolMessage] = []
    for msg in reversed(state.get("messages", [])):
        if not isinstance(msg, ToolMessage):
            break
        recent.append(msg)
    if (
        len(recent) == 1
        and getattr(recent[0], "name", None) == SUMMARY_TOOL_NAME
        and isinstance(recent[0].content, str)
        and recent[0].content.startswith(SUMMARY_RESULT_PREFIX)
    ):
        return "emit_summary"
    return "chatbot"


def emit_summary_node(state: AgentState):
    """Biến kết quả tool thành AIMessage (không gọi LLM) để:
    - frontend nhận được qua should_stream_chunk như một câu trả lời bình thường,
    - lượt sau vẫn còn bản tóm tắt trong lịch sử hội thoại để hỏi tiếp."""
    return {"messages": [AIMessage(content=str(state["messages"][-1].content))]}


# Update default and allowed models to use Groq models
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

ALLOWED_MODELS = {
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
}


SYSTEM_PROMPT = """
You are a helpful Agentic AI assistant named Tit, similar to ChatGPT.

You have access to a set of tools. Each tool's description explains exactly
when and how to use it — always read the tool description before deciding
whether to call it.

General rules:
- Use tools whenever they would produce a more accurate or up-to-date answer.
- When asked to summarize, overview, or outline an uploaded file:
  * By default, summarize in the ORIGINAL LANGUAGE of the uploaded document (e.g. English file -> English summary, Vietnamese file -> Vietnamese summary). Only pass a different target_language (such as 'Vietnamese') if the user explicitly requested it in that prompt.
  * CRITICAL FOR TRANSLATION / RE-SUMMARIZING FOLLOW-UPS: If a summary of that document ALREADY EXISTS in the recent conversation and the user asks to translate it, rewrite it, or summarize it in another language (e.g., "hãy tóm tắt bằng tiếng Việt", "dịch sang tiếng Việt", "bản tiếng Việt", "translate to English"):
    DO NOT call `summarize_uploaded_document` again! Instead, directly translate and reformat the existing summary from the conversation history into the requested language. This provides an instantaneous, zero-overhead response without redundant processing.
- For questions about an uploaded image, use the image analysis tool and name the image when several are available.
- Cross-chat Long-term memory for each user:
  * To remember a new detail/preference (e.g. "Nhớ rằng tôi tên là...", "Lưu lại điều này...", "Remember that..."): call `remember_this`.
  * To recall personal details/preferences:
    - If the user asks general questions about themselves or asks what you remember ("Bạn nhớ gì về tôi?", "Tôi là ai?", "Liệt kê thông tin của tôi", "Tôi học ở đâu?", "What do you remember about me?"):
      Call `recall_memory(query="")` with an empty string or use a broad root keyword (e.g. query="học", query="tên", query="việc", query="thích").
    - If the user asks about a specific fact or topic:
      Extract and pass ONLY a broad core root keyword (e.g. query="tên", query="học", query="việc", query="thích"). Never pass full sentences or narrow synonyms.
    - If unsure of the exact wording saved, call `recall_memory(query="")` to inspect all stored facts.
  * To update or change an existing memory (e.g. "Tôi đổi tên thành...", "Tôi chuyển nghề sang..."):
    Step 1: ALWAYS call `search_memory_candidates` with the relevant keyword (e.g. "tên", "nghề nghiệp") to inspect candidate entries, their memory_id, and version.
    Step 2: If exactly 1 matching candidate is found, immediately call `update_memory_entry` with its memory_id, new statement, and version.
    Step 3: If multiple candidates match, present the choices naturally to the user and ask which one they wish to update.
  * To delete/forget an existing memory (e.g. "Quên tên tôi đi", "Xóa sở thích của tôi..."):
    Step 1: ALWAYS call `search_memory_candidates` first to find candidate entries.
    Step 2: If exactly 1 candidate matches, call `forget_memory_entry` with its memory_id and version.
    Step 3: If multiple candidates match, ask the user which one to forget.
  * NEVER invent or guess a memory_id. Only use IDs returned by `search_memory_candidates`.
  * Memories are permanently shared across all conversations belonging to that user, but strictly isolated from other users.
- Do NOT fabricate information; prefer tool results over guesses.
- After using a tool, present the findings clearly, structured, and helpful to the user. For complete document summaries from summarize_uploaded_document, deliver the comprehensive summary faithfully with a brief opening sentence — do NOT re-summarize, truncate, or duplicate the content.
- Be friendly, structured, and easy to understand.
- When code or math is involved, use proper formatting.
- Answer the user's latest question directly. Never repeat, quote, or output internal conversation summaries or background context unless explicitly asked by the user.
"""



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




def build_agent(model_name: str):
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
        streaming=True       # Hỗ trợ stream phản hồi theo thời gian thực về giao diện
    )

    # Khai báo các công cụ (tools) cho mô hình để LLM có thể chủ động kích hoạt Function Calling
    llm_with_tools = llm.bind_tools(tools)

    def chatbot_node(state: AgentState):
        """
        Chọn các lượt hội thoại hoàn chỉnh cần thiết, bỏ tool results cũ,
        rồi giới hạn prompt theo ngân sách cấu hình trước khi gọi LLM.
        """
        existing_summary = state.get("conversation_summary", "")

        final_messages, updated_summary = prepare_context_for_llm(
            messages=state.get("messages", []),
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            existing_summary=existing_summary,
            model_name=selected_model,
        )

        response = llm_with_tools.invoke(final_messages)

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
_AGENT_CACHE = {}


def get_agent(model_name: str | None = None):
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
