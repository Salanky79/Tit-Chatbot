"""
Evaluation Config - Cấu hình cho hệ thống đánh giá Tit Chatbot.

Sử dụng Groq LLM (ChatGroq) làm Judge thay cho OpenAI,
tương thích hoàn toàn với stack hiện tại của Tit Chatbot.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import certifi

# ---------------------------------------------------------------------------
# 1. Environment Setup
# ---------------------------------------------------------------------------

# Thêm project root vào sys.path để import agent, rag, tools, database
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

# ---------------------------------------------------------------------------
# 2. LangSmith Config
# ---------------------------------------------------------------------------

LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "")
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "true")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "tit")

os.environ["LANGSMITH_TRACING"] = LANGSMITH_TRACING
os.environ["LANGSMITH_PROJECT"] = LANGSMITH_PROJECT

# ---------------------------------------------------------------------------
# 3. Groq Judge LLM (dùng để đánh giá - LLM-as-a-Judge)
# ---------------------------------------------------------------------------

# Model dùng để đánh giá (judge) - nên dùng model mạnh nhất có sẵn
JUDGE_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

# Model chính của chatbot - dùng để so sánh giữa các models
CHATBOT_MODELS = [
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
]

# ---------------------------------------------------------------------------
# 4. RAG Config cho evaluation
# ---------------------------------------------------------------------------

RAG_THREAD_ID = "eval-rag-test"       # Thread ID riêng cho evaluation
RAG_COLLECTION = "agentic_chatbot_docs_hf"

# ---------------------------------------------------------------------------
# 5. Evaluation Thresholds (ngưỡng pass/fail)
# ---------------------------------------------------------------------------

THRESHOLDS = {
    "correctness": 0.7,          # 70% câu trả lời phải đúng
    "relevance": 0.8,            # 80% câu trả lời phải relevant
    "groundedness": 0.7,         # 70% câu trả lời phải grounded
    "retrieval_relevance": 0.7,  # 70% documents retrieved phải relevant
    "concision": 0.6,            # 60% câu trả lời phải ngắn gọn
    "helpfulness": 0.7,          # 70% câu trả lời phải helpful
    "coherence": 0.8,            # 80% câu trả lời phải mạch lạc
    "tool_selection": 0.8,       # 80% phải chọn đúng tool
}

