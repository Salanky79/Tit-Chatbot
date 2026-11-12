"""mongo_client.py – Singleton MongoDB client dùng chung cho toàn bộ Tit‑Chatbot.

Cách dùng:
    from mongo_client import get_collection, get_db

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
PROJECT_DIR = Path(__file__).resolve().parent
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

def ensure_indexes() -> None:
    """Tạo các index cần thiết cho hiệu năng truy vấn.

    - ``users``:            ``username`` (unique) + ``email`` (unique)
    - ``sessions``:         ``session_id`` (unique) + TTL index trên ``expires_at``
    - ``conversations``:    ``thread_id`` (unique) + ``[user_id, updated_at]`` + TTL cho guest
    - ``chat_messages``:    ``thread_id`` + ``created_at`` (asc)
    - ``long_term_memory``: ``user_id`` (primary) + text index trên ``memory``
    """
    try:
        # users
        users = get_collection("users")
        users.create_index([("username", ASCENDING)], unique=True, background=True)
        users.create_index([("email", ASCENDING)], unique=True, background=True)

        # sessions (với TTL index tự động dọn dẹp phiên hết hạn)
        sessions = get_collection("sessions")
        sessions.create_index([("session_id", ASCENDING)], unique=True, background=True)
        sessions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, background=True)

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

        # long_term_memory — compound index: user_id + is_deleted + created_at
        mem = get_collection("long_term_memory")
        mem.create_index(
            [("user_id", ASCENDING), ("is_deleted", ASCENDING), ("created_at", DESCENDING)],
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


def close_client() -> None:
    """Đóng kết nối MongoDB (gọi khi ứng dụng tắt nếu cần)."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("Đã đóng kết nối MongoDB.")

