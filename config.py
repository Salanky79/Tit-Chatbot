"""Hằng số và giá trị mặc định của Tit Chatbot.

Các tùy chọn đọc theo request (model, OCR, ngân sách summary, SMTP) dùng các
mặc định ở đây khi biến môi trường vắng mặt. FRONTEND chỉ chứa dữ liệu công khai.
"""

import math
import os
from datetime import timedelta
from pathlib import Path
from string import ascii_letters, digits

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env", override=False)


def _env_int(name: str, default: int, minimum: int, *, clamp: bool = False) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    if clamp:
        return max(minimum, value)
    return value if value >= minimum else default


def _env_float(name: str, default: float, minimum: float, *, finite: bool = False) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    if finite and not math.isfinite(value):
        return default
    return value if value >= minimum else default


# Máy chủ, thư mục và kết nối MongoDB
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8080
TEMPLATES_DIR = "templates"
STATIC_DIR = "static"
UPLOAD_DIR = "uploads"
AVATAR_DIR = "uploads/avatars"
MONGO_DATABASE = "tit_chatbot"
CHECKPOINT_DATABASE = "tit_chatbot_checkpoints"
MONGO_SERVER_TIMEOUT_MS = 10_000
MONGO_DNS_SERVERS = ("8.8.8.8", "8.8.4.4")


# Model và hội thoại. Thứ tự MODEL_OPTIONS cũng là thứ tự hiển thị trên giao diện.
CHAT_MODEL = "openai/gpt-oss-20b"
VISION_MODEL = "qwen/qwen3.8-27b"
MODEL_OPTIONS = {
    CHAT_MODEL: "GPT-OSS 20B (Default)",
    VISION_MODEL: "Qwen 3.8 27B",
    "openai/gpt-oss-120b": "GPT-OSS 120B",
}
ALLOWED_MODELS = frozenset(MODEL_OPTIONS)
DEFAULT_MODEL = os.getenv("GROQ_MODEL", CHAT_MODEL)
CHAT_TEMPERATURE = 0.3
DEFAULT_HISTORY_TURNS = 10
DEFAULT_THREAD_ID = "default"
DEFAULT_LANGUAGE = "vi"
DEFAULT_REASONING_FORMAT = "parsed"
CHAT_RATE_LIMIT_MAX_RETRIES = _env_int("CHAT_RATE_LIMIT_MAX_RETRIES", 3, 0, clamp=True)
CONTEXT_TOKENS = 128_000
TOOL_RESULT_TOKENS = 8_000
EXPECTED_REPLY_TOKENS = 2_048


# Ước lượng token, cắt ngữ cảnh và xử lý rate limit
CHARS_PER_TOKEN = _env_float("TOKEN_ESTIMATE_CHARS_PER_TOKEN", 2.5, 0.5)
PROMPT_CHARS_PER_TOKEN = _env_float("TOKEN_ESTIMATE_PROMPT_CHARS_PER_TOKEN", 4.0, 0.5)
MESSAGE_OVERHEAD_TOKENS = 4
TOOL_MESSAGE_OVERHEAD_TOKENS = 2
TOOL_SCHEMA_OVERHEAD_TOKENS = 12
TRUNCATION_HEAD_RATIO = 0.7
TRUNCATION_MARKER = "\n[…đã rút gọn…]\n"
MAX_RATE_LIMIT_WAIT_SECONDS = _env_float("MAX_RATE_LIMIT_WAIT_SECONDS", 60.0, 1.0, finite=True)
RATE_LIMIT_BACKOFF_BASE_SECONDS = _env_float("RATE_LIMIT_BACKOFF_BASE_SECONDS", 1.0, 0.1, finite=True)
RATE_LIMIT_BACKOFF_JITTER_SECONDS = _env_float("RATE_LIMIT_BACKOFF_JITTER_SECONDS", 0.5, 0.0, finite=True)
MIN_RETRY_WAIT_SECONDS = 0.25
MAX_BACKOFF_EXPONENT = 20


# Map–reduce. Ngân sách tính theo token, khoảng cách gửi tác vụ tính theo giây.
DEFAULT_INPUT_TOKEN_BUDGET = 3200
REDUCE_INPUT_TOKEN_BUDGET = 5500
MAP_SUMMARY_OUTPUT_TOKENS = 350
FINAL_SUMMARY_OUTPUT_TOKENS = 900
MIN_SUMMARY_BUDGET = 256
SUMMARY_FRAMING_TOKENS = 32
SUMMARY_WORKERS = 2
SUMMARY_STAGGER_SECONDS = 0.3
SUMMARY_TEMPERATURE = 0.1
SUMMARY_LANGUAGE = "auto"
SUMMARY_FALLBACK_MODELS = ",".join(MODEL_OPTIONS)
SUMMARY_DIRECT_RETURN = os.getenv("SUMMARY_DIRECT_RETURN", "1").lower() not in ("0", "false", "no")
SUMMARY_TOOL_NAME = "summarize_uploaded_document"
SUMMARY_RESULT_PREFIX = "Summary of "
SUMMARY_RESULT_PREFIX_VI = "Bản tóm tắt của "
SUMMARY_ERROR_PREFIX = "Không thể tóm tắt tài liệu "
JOBS = "summary_jobs"
CHECKPOINTS = "summary_job_checkpoints"
SCOPE_FIELDS = ("thread_id", "user_id", "project_id")
JOB_FIELDS = tuple(f"context.{name}" for name in SCOPE_FIELDS) + ("document_id", "target_language")


# Tài liệu, ChromaDB, OCR và tra cứu
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DEVICE = "cpu"
CHROMA_DIR = "chroma_db_hf"
CHROMA_COLLECTION = "agentic_chatbot_docs_hf"
DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".py", ".csv")
TEXT_EXTENSIONS = (".txt", ".md", ".py", ".csv")
RAG_CHUNK_CHARS = 900
RAG_OVERLAP_CHARS = 150
RAG_TOP_K = 4
OCR_MIN_TEXT_CHARS = 40
OCR_DPI = 300
OCR_LANGUAGES = "vie+eng"
WEB_SEARCH_RESULTS = 5
WEB_SEARCH_TOPIC = "general"
WEB_SEARCH_DEPTH = "advanced"


# Ảnh tài liệu và ảnh đại diện
SUPPORTED_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
AVATAR_TYPES = {**SUPPORTED_IMAGE_TYPES, ".gif": "image/gif"}
MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 14 * 1024 * 1024
MAX_IMAGE_QUESTION_TOKENS = 350
VISION_OUTPUT_TOKENS = 800
VISION_TEMPERATURE = 0.2
VISION_RATE_LIMIT_MAX_RETRIES = _env_int("VISION_RATE_LIMIT_MAX_RETRIES", 3, 0, clamp=True)


# Gợi ý sau câu trả lời
FOLLOWUPS_ENABLED = os.getenv("FOLLOWUPS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
FOLLOWUP_MODEL = (os.getenv("FOLLOWUP_MODEL") or CHAT_MODEL).strip() or CHAT_MODEL
FOLLOWUP_TIMEOUT_SECONDS = _env_float("FOLLOWUP_TIMEOUT_SECONDS", 5.0, 0.1)
FOLLOWUP_MAX_USER_TOKENS = _env_int("FOLLOWUP_MAX_USER_TOKENS", 150, 1)
FOLLOWUP_MAX_ANSWER_TOKENS = _env_int("FOLLOWUP_MAX_ANSWER_TOKENS", 300, 1)
FOLLOWUP_OUTPUT_TOKENS = _env_int("FOLLOWUP_OUTPUT_TOKENS", 500, 1)
FOLLOWUP_RATE_LIMIT_MAX_RETRIES = _env_int("FOLLOWUP_RATE_LIMIT_MAX_RETRIES", 2, 0)
FOLLOWUP_MAX_PROMPT_CHARS = _env_int("FOLLOWUP_MAX_PROMPT_CHARS", 180, 1)
FOLLOWUP_TEMPERATURE = 0.2
FOLLOWUP_COUNT = 2


# Tài khoản, xác thực, cookie và email
DEFAULT_BASE_STYLE = "default"
VALID_BASE_STYLES = (DEFAULT_BASE_STYLE, "professional", "friendly", "efficient", "creative")
MAX_CUSTOM_INSTRUCTIONS_LEN = 1500
MIN_USERNAME_CHARS = 3
MIN_PASSWORD_CHARS = 6
ALLOWED_USERNAME_CHARS = frozenset(ascii_letters + digits + "_.-")
SESSION_COOKIE = "tit_session"
AUTH_CHALLENGE_COOKIE = "tit_auth_challenge"
SESSION_DAYS = 7
CHALLENGE_COOKIE_SECONDS = 15 * 60
CHALLENGE_RETENTION_SECONDS = 3600
SECRET_TOKEN_BYTES = 32
OTP_DIGITS = 6
OTP_TTL = timedelta(minutes=5)
RESEND_INTERVAL = timedelta(seconds=60)
MAX_ATTEMPTS = 5
MAX_RESENDS = 5
SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 15
SMTP_SSL_DEFAULT = "false"


# Lịch sử chat, tìm kiếm và phân trang quản trị
TEMPORARY_CHAT_DAYS = 30
GUEST_CHAT_HOURS = 24
AUTO_TITLE_CHARS = 40
MAX_TITLE_CHARS = 100
CONVERSATION_LIST_LIMIT = 100
CHAT_HISTORY_LIMIT = 200
FIRST_PAGE = 1
ADMIN_PAGE_SIZE = 25
CONVERSATION_PAGE_SIZE = 20
MESSAGE_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
ADMIN_SEARCH_CHARS = 100
SEARCH_LIMIT = 25
MAX_SEARCH_LIMIT = 50
SEARCH_QUERY_CHARS = 120
SEARCH_SNIPPET_CHARS = 120
SEARCH_SNIPPET_MARGIN = 20
SEARCH_CANDIDATES = 250
SEARCH_OVERFETCH = 4


# Project và bộ nhớ
VALID_MEMBER_ROLES = frozenset({"editor", "chat_only"})
DEFAULT_INVITE_ROLE = "chat_only"
INVITATION_DURATIONS = (1, 3, 7, 14, 30)
DEFAULT_INVITATION_DAYS = 7
DEFAULT_MEMORY_MODE = "project_only"
PROJECT_MEMORY_OPTIONS = {
    DEFAULT_MEMORY_MODE: (
        "Chỉ Project",
        "Dùng chat gần đây, tài liệu và bộ nhớ của Project.",
    ),
    "project_and_personal": (
        "Project và cá nhân",
        "Dùng ngữ cảnh Project và bộ nhớ cá nhân của người đang chat.",
    ),
    "current_chat_only": (
        "Chỉ chat hiện tại",
        "Chỉ dùng cuộc trò chuyện và tài liệu trong chat hiện tại.",
    ),
}
PROJECT_MEMORY_MODES = tuple(PROJECT_MEMORY_OPTIONS)
PROJECT_NAME_CHARS = 100
PROJECT_DESCRIPTION_CHARS = 500
PROJECT_INSTRUCTION_CHARS = 8000
PROJECT_MEMORY_CHARS = 2000
MAX_PROJECT_CONTEXT_CHARS = 9000
PROJECT_CONTEXT_THREADS = 12
PROJECT_CONTEXT_MESSAGES = 24
MEMORY_TOP_K = 5
MEMORY_SIMILARITY_THRESHOLD = 0.38
MEMORY_TEMPERATURE = 0.2
MEMORY_SUMMARY_TOKENS = 700
MEMORY_UPDATE_TOKENS = 800
POLL_SECONDS = 2
STREAM_SECONDS = 300
PROJECT_HEARTBEAT_SECONDS = 15


# Đánh giá RAG. Prompt của từng metric và các case mẫu nằm trong evaluation/rag_eval.
DATASET_NAME = "tit-rag-small-v1"
DATASET_DESCRIPTION = "Small, hand-authored Vietnamese RAG evaluation set (v1)."
DATASET_VERSION = "v1"
MAX_CASES_PER_RUN = 20
EVAL_JUDGE_TOKENS = 700
EVAL_GENERATION_TOKENS = 512
EVAL_TEMPERATURE = 0
EVAL_ATTEMPTS = 8
EVAL_MAX_WAIT_SECONDS = 65.0
EVAL_RETRY_MARGIN_SECONDS = 0.5
EVAL_MAX_SCORE = 4
EVAL_SCORE_PRECISION = 4
