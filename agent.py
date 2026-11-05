import os
from pathlib import Path

from dotenv import load_dotenv
import certifi                  # Cung cấp chứng chỉ SSL/TLS chuẩn để verify HTTPS an toàn

load_dotenv()

# Trỏ chứng chỉ SSL đến file chứng chỉ của certifi để tránh lỗi SSL khi gọi API bên ngoài
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from langchain_groq import ChatGroq
# Định dạng tin nhắn hệ thống (System Prompt) cho mô hình
from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, START, MessagesState
# ToolNode: Node chạy các công cụ; tools_condition: Điều kiện rẽ nhánh kiểm tra xem LLM có gọi tool không
from langgraph.prebuilt import ToolNode, tools_condition
# MongoDBSaver: Lưu trạng thái và lịch sử hội thoại vào MongoDB để duy trì ngữ cảnh nhiều lượt chat
from langgraph.checkpoint.mongodb import MongoDBSaver
from mongo_client import get_client
from tools import tools


# Update default and allowed models to use Gemini 2.5
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

ALLOWED_MODELS = {
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b"
}


SYSTEM_PROMPT = """
You are a helpful Agentic AI assistant named Tit, similar to ChatGPT.

You have access to a set of tools. Each tool's description explains exactly
when and how to use it — always read the tool description before deciding
whether to call it.

General rules:
- Use tools whenever they would produce a more accurate or up-to-date answer.
- Do NOT fabricate information; prefer tool results over guesses.
- After using a tool, summarize the result clearly and concisely.
- Be friendly, structured, and easy to understand.
- When code or math is involved, use proper formatting.
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

    def chatbot_node(state: MessagesState):
        """
        Node xử lý hội thoại của Chatbot:
        - Ghép SYSTEM_PROMPT vào đầu danh sách tin nhắn để luôn duy trì vai trò và chỉ dẫn hệ thống.
        - Gọi LLM kèm theo các công cụ đã bind để sinh ra câu trả lời hoặc lệnh gọi tool.
        - Trả về tin nhắn mới để cập nhật vào trạng thái (state).
        """
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]

        response = llm_with_tools.invoke(messages)

        return {
            "messages": [response]
        }

    # Node phụ trách thực thi các tool khi mô hình yêu cầu
    tool_node = ToolNode(tools)

    # Khởi tạo đồ thị trạng thái quản lý chuỗi tin nhắn MessagesState
    workflow = StateGraph(MessagesState)

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
