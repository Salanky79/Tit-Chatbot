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
from langchain_core.messages import SystemMessage, BaseMessage
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
# ToolNode: Node chạy các công cụ; tools_condition: Điều kiện rẽ nhánh kiểm tra xem LLM có gọi tool không
from langgraph.prebuilt import ToolNode, tools_condition
# MongoDBSaver: Lưu trạng thái và lịch sử hội thoại vào MongoDB để duy trì ngữ cảnh nhiều lượt chat
from langgraph.checkpoint.mongodb import MongoDBSaver
from mongo_client import get_client
from tools import tools
from context_manager import (
    prepare_context_for_llm,
    DEFAULT_MAX_HISTORY_TURNS,
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_EXPECTED_OUTPUT_TOKENS,
)


class AgentState(TypedDict, total=False):
    """Trạng thái hội thoại mở rộng cho Agentic Chatbot:

    - messages: Danh sách tin nhắn trò chuyện được cộng dồn (add_messages).
    - conversation_summary: Bản tóm tắt các lượt hội thoại cũ đã được trim,
      giúp duy trì ngữ cảnh dài hạn mà không làm tràn context window.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    conversation_summary: str


# Update default and allowed models to use Groq models
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

ALLOWED_MODELS = {
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-safeguard-20b",
    "meta-models/muse-spark-open",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
}


SYSTEM_PROMPT = """
You are a helpful Agentic AI assistant named Tit, similar to ChatGPT.

You have access to a set of tools. Each tool's description explains exactly
when and how to use it — always read the tool description before deciding
whether to call it.

General rules:
- Use tools whenever they would produce a more accurate or up-to-date answer.
- When asked to summarize, overview, or outline an uploaded file, ALWAYS call the `summarize_uploaded_document` tool. The tool uses recursive Map-Reduce to handle documents of any length and page count. NEVER refuse or say a document is too large.
- For questions about an uploaded image, use the image analysis tool and name the image when several are available.
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

    # Initialize ChatGroq
    llm = ChatGroq(
        model=selected_model,
        temperature=0.3,    # Giảm tính sáng tạo ngẫu nhiên, ưu tiên câu trả lời chính xác, bám sát sự thật
        streaming=True       # Hỗ trợ stream phản hồi theo thời gian thực về giao diện
    )

    # Khai báo các công cụ (tools) cho mô hình để LLM có thể chủ động kích hoạt Function Calling
    llm_with_tools = llm.bind_tools(tools)

    def chatbot_node(state: AgentState):
        """
        Node xử lý hội thoại của Chatbot:
        Thực hiện tuần tự 4 bước tối ưu ngữ cảnh & token trước khi gọi LLM:
        1. Trimming conversation history: Giới hạn số lượt chat đưa vào LLM, tóm tắt các lượt cũ.
        2. Loại bỏ tool results cũ: Loại bỏ kết quả web search, RAG hoặc nội dung tool dài
           không liên quan từ các lượt đã hoàn thành.
        3. Tách context theo tác vụ: Đảm bảo prompt đưa vào LLM hoàn toàn tập trung vào hội thoại.
        4. Giới hạn token trước mỗi lần gọi LLM: Tính cả system prompt, lịch sử, tool schemas,
           tài liệu và output dự kiến; tự động trim / chia nhỏ nếu quá giới hạn.
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

    # Thiết lập luồng di chuyển giữa các node:
    # 1. Bắt đầu từ START -> đi vào node 'chatbot'
    workflow.add_edge(START, "chatbot")
    # 2. Điều kiện rẽ nhánh: nếu LLM yêu cầu gọi tool -> chuyển sang node 'tools', nếu không -> kết thúc lượt
    workflow.add_conditional_edges("chatbot", tools_condition) # mac dinh tim node ten la tools
    # 3. Sau khi node 'tools' chạy xong -> trả kết quả về lại node 'chatbot' để LLM đọc và trả lời người dùng
    workflow.add_edge("tools", "chatbot")

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
