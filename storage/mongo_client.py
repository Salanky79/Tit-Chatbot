"""mongo_client.py – Singleton MongoDB client dùng chung cho toàn bộ Tit‑Chatbot.

Cách dùng:
    from storage.mongo_client import get_collection, get_db

    coll = get_collection("conversations")
    db   = get_db()
"""

import os
import logging
from pathlib import Path
from typing import Optional

import certifi
import dns.resolver
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database

logger = logging.getLogger(__name__)

# Đọc .env từ thư mục gốc của project
PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

# Dùng file chứng chỉ SSL hợp lệ
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

# Dùng Google DNS cho các truy vấn SRV của MongoDB Atlas
resolver = dns.resolver.get_default_resolver()
resolver.nameservers = ["8.8.8.8", "8.8.4.4"]

# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------
_client: Optional[MongoClient] = None



# =============================================================================
# 01. Kết nối MongoDB dùng chung
# =============================================================================

def get_client() -> MongoClient:
    """Trả về MongoClient singleton. Tạo mới nếu chưa có.

    Đọc URI kết nối từ biến môi trường ``MONGODB_URI``.
    Raises:
        EnvironmentError: nếu ``MONGODB_URI`` chưa được đặt.
    """
    global _client
    if _client is None:
        uri = os.getenv("MONGODB_URI")
        if not uri:
            raise EnvironmentError(
                "Biến môi trường MONGODB_URI chưa được đặt. "
                "Vui lòng thêm vào file .env: "
                "MONGODB_URI=mongodb+srv://<user>:<pass>@<host>/<dbname>"
            )
        _client = MongoClient(
            uri,
            serverSelectionTimeoutMS=10000,
            tlsCAFile=certifi.where(),
        )
        logger.info("Đã kết nối MongoDB thành công.")
    return _client


def get_db() -> Database:
    """Trả về database MongoDB.

    Tên database được lấy từ biến môi trường ``MONGODB_DB_NAME``
    (mặc định: ``tit_chatbot``).
    """
    db_name = os.getenv("MONGODB_DB_NAME", "tit_chatbot")
    return get_client()[db_name]


def get_collection(name: str) -> Collection:
    """Trả về Collection theo tên.

    Args:
        name: Tên collection (vd: ``"conversations"``, ``"chat_messages"``).

    Returns:
        pymongo ``Collection`` object.
    """
    return get_db()[name]


# ---------------------------------------------------------------------------
# Index setup – gọi một lần khi ứng dụng khởi động
# ---------------------------------------------------------------------------


# =============================================================================
# 02. Index, ràng buộc duy nhất và TTL
# =============================================================================

def ensure_indexes() -> None:
    try:
        # users
        users = get_collection("users")
        users.create_index([("username", ASCENDING)], unique=True, background=True)
        users.create_index([("email", ASCENDING)], unique=True, background=True)

        # sessions (với TTL index tự động dọn dẹp phiên hết hạn)
        sessions = get_collection("sessions")
        sessions.create_index([("session_id", ASCENDING)], unique=True, background=True)
        sessions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, background=True)

        # Authentication challenges are short-lived and are restored through an HttpOnly cookie.
        challenges = get_collection("auth_challenges")
        challenges.create_index([("challenge_key", ASCENDING)], unique=True, background=True)
        challenges.create_index([("expires_at", ASCENDING)], expireAfterSeconds=3600, background=True)

        # conversations
        conv = get_collection("conversations")
        conv.create_index([("thread_id", ASCENDING)], unique=True, background=True)
        conv.create_index([("updated_at", DESCENDING)], background=True)
        conv.create_index(
            [("user_id", ASCENDING), ("updated_at", DESCENDING)],
            background=True
        )
        # TTL index tự động xóa guest conversations sau 24h (expires_at được set khi tạo)
        conv.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
            background=True,
        )

        # chat_messages
        msgs = get_collection("chat_messages")
        msgs.create_index(
            [("thread_id", ASCENDING), ("created_at", ASCENDING)],
            background=True
        )
        # TTL index tự động xóa tin nhắn của các cuộc trò chuyện tạm thời / hết hạn
        msgs.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
            background=True,
        )

        # Projects and their shared resources.
        projects = get_collection("projects")
        projects.create_index([("owner_id", ASCENDING), ("updated_at", DESCENDING)], background=True)
        members = get_collection("project_members")
        members.create_index([("project_id", ASCENDING), ("user_id", ASCENDING)], unique=True, background=True)
        members.create_index([("user_id", ASCENDING), ("status", ASCENDING)], background=True)
        members.create_index([("invitation_token_hash", ASCENDING)], unique=True, sparse=True, background=True)
        # Do not use a TTL index: accepted memberships must survive invitation expiry.
        members.create_index([("user_id", ASCENDING), ("status", ASCENDING), ("expires_at", ASCENDING)], background=True)
        project_files = get_collection("project_files")
        project_files.create_index([("project_id", ASCENDING), ("document_id", ASCENDING)], unique=True, background=True)
        project_files.create_index([("project_id", ASCENDING), ("created_at", DESCENDING)], background=True)
        conv.create_index([("project_id", ASCENDING), ("updated_at", DESCENDING)], background=True)
        msgs.create_index([("project_id", ASCENDING), ("created_at", ASCENDING)], background=True)

        # Resumable document summary checkpoints
        summary_jobs = get_collection("summary_jobs")
        summary_jobs.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, background=True)
        checkpoints = get_collection("summary_job_checkpoints")
        checkpoints.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, background=True)
        checkpoints.create_index(
            [("job_id", ASCENDING), ("stage", ASCENDING)],
            background=True,
        )

        # long_term_memory indexes both personal and project-scoped memory records.
        mem = get_collection("long_term_memory")
        mem.create_index(
            [("user_id", ASCENDING), ("is_deleted", ASCENDING), ("created_at", DESCENDING)],
            background=True
        )
        mem.create_index(
            [("project_id", ASCENDING), ("is_deleted", ASCENDING), ("created_at", DESCENDING)],
            background=True
        )
        mem.create_index([("thread_id", ASCENDING)], background=True)
        # Text index để hỗ trợ tìm kiếm full-text
        try:
            mem.create_index([("memory", "text")], background=True)
        except Exception:
            # Text index có thể đã tồn tại, bỏ qua lỗi trùng lặp
            pass

        logger.info("MongoDB indexes đã được tạo/xác nhận.")

    except Exception as e:
        logger.warning("Không thể tạo MongoDB indexes: %s", e)



# =============================================================================
# 03. Đóng kết nối
# =============================================================================

def close_client() -> None:
    """Đóng kết nối MongoDB (gọi khi ứng dụng tắt nếu cần)."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("Đã đóng kết nối MongoDB.")

