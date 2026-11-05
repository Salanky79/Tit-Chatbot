"""database.py – Lớp truy cập dữ liệu (DAL) dùng MongoDB thay cho SQLite/SQLAlchemy.

Các hàm public giữ nguyên tên và chữ ký để các module khác (app.py, tools.py, …)
không cần thay đổi logic gọi hàm.

Collections:
    - conversations   : metadata của từng luồng chat
    - chat_messages   : nội dung từng tin nhắn
    - long_term_memory: bộ nhớ dài hạn do user yêu cầu lưu
"""

from datetime import datetime, timezone
from typing import Any

from mongo_client import get_collection, ensure_indexes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Trả về thời điểm hiện tại theo UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


def _conv_to_obj(doc: dict) -> Any:
    """Chuyển dict MongoDB thành object-like để tương thích với code cũ dùng
    attribute access (``item.thread_id``, ``item.title``, …).
    """
    if doc is None:
        return None

    class _Row:
        pass

    obj = _Row()
    for key, value in doc.items():
        setattr(obj, key, value)
    return obj


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Khởi tạo kết nối và tạo indexes MongoDB.

    Thay thế ``Base.metadata.create_all(bind=engine)`` của SQLAlchemy.
    Gọi một lần khi ứng dụng khởi động (trong app.py).
    """
    ensure_indexes()


# ---------------------------------------------------------------------------
# Conversation APIs
# ---------------------------------------------------------------------------

def create_or_update_conversation(
    thread_id: str,
    first_message: str | None = None
) -> Any:
    """Tạo mới hoặc cập nhật conversation theo ``thread_id``.

    - Nếu chưa tồn tại: insert với ``title`` lấy từ ``first_message`` (40 ký tự đầu).
    - Nếu đã tồn tại: cập nhật ``updated_at``.

    Returns:
        Object-like với các thuộc tính ``thread_id``, ``title``,
        ``created_at``, ``updated_at``.
    """
    coll = get_collection("conversations")
    now = _now()

    # Sinh title từ first_message
    title = "New Chat"
    if first_message:
        stripped = first_message.strip()
        title = stripped[:40] + ("..." if len(stripped) > 40 else "")

    result = coll.update_one(
        {"thread_id": thread_id},
        {
            "$set": {"updated_at": now},
            "$setOnInsert": {
                "thread_id": thread_id,
                "title": title,
                "created_at": now,
            },
        },
        upsert=True,
    )

    # Nếu là insert mới, trả về doc vừa tạo
    doc = coll.find_one({"thread_id": thread_id}, {"_id": 0})
    return _conv_to_obj(doc)


def list_conversations(limit: int = 100) -> list[Any]:
    """Lấy danh sách conversations, sắp xếp mới nhất trước.

    Returns:
        List các object-like với thuộc tính ``thread_id``, ``title``,
        ``created_at``, ``updated_at``.
    """
    coll = get_collection("conversations")
    cursor = (
        coll.find({}, {"_id": 0})
        .sort("updated_at", -1)
        .limit(limit)
    )
    return [_conv_to_obj(doc) for doc in cursor]


# ---------------------------------------------------------------------------
# Chat message APIs
# ---------------------------------------------------------------------------

def save_chat_message(thread_id: str, role: str, content: str) -> Any:
    """Lưu một tin nhắn vào collection ``chat_messages``.

    Đồng thời cập nhật ``updated_at`` của conversation tương ứng.

    Args:
        thread_id: ID luồng chat.
        role: ``"user"`` hoặc ``"assistant"``.
        content: Nội dung tin nhắn.

    Returns:
        Object-like đại diện cho document vừa insert.
    """
    now = _now()

    # Insert tin nhắn
    msg_coll = get_collection("chat_messages")
    doc = {
        "thread_id": thread_id,
        "role": role,
        "content": content,
        "created_at": now,
    }
    msg_coll.insert_one(doc)

    # Cập nhật updated_at của conversation
    conv_coll = get_collection("conversations")
    conv_coll.update_one(
        {"thread_id": thread_id},
        {"$set": {"updated_at": now}},
    )

    doc.pop("_id", None)
    return _conv_to_obj(doc)


def get_chat_history(thread_id: str, limit: int = 200) -> list[Any]:
    """Lấy lịch sử chat của một thread, sắp xếp cũ nhất trước.

    Returns:
        List các object-like với thuộc tính ``role``, ``content``, ``created_at``.
    """
    coll = get_collection("chat_messages")
    cursor = (
        coll.find({"thread_id": thread_id}, {"_id": 0})
        .sort("created_at", 1)
        .limit(limit)
    )
    return [_conv_to_obj(doc) for doc in cursor]


# ---------------------------------------------------------------------------
# Long-term memory APIs
# ---------------------------------------------------------------------------

def save_memory(thread_id: str, memory: str) -> str:
    """Lưu một đoạn bộ nhớ dài hạn vào collection ``long_term_memory``.

    Args:
        thread_id: ID luồng chat.
        memory: Nội dung cần ghi nhớ.

    Returns:
        Chuỗi xác nhận thành công.
    """
    coll = get_collection("long_term_memory")
    coll.insert_one({
        "thread_id": thread_id,
        "memory": memory,
        "created_at": _now(),
    })
    return "Memory saved successfully."


def search_memory(thread_id: str, query: str) -> str:
    """Tìm kiếm bộ nhớ dài hạn theo ``query`` (regex, case-insensitive).

    Args:
        thread_id: ID luồng chat.
        query: Từ khóa tìm kiếm.

    Returns:
        Chuỗi kết quả, mỗi dòng là một mục nhớ, hoặc thông báo không tìm thấy.
    """
    coll = get_collection("long_term_memory")
    cursor = (
        coll.find(
            {
                "thread_id": thread_id,
                "memory": {"$regex": query, "$options": "i"},
            },
            {"_id": 0, "memory": 1},
        )
        .sort("created_at", -1)
        .limit(20)
    )

    memories = [doc["memory"] for doc in cursor]

    if not memories:
        return "No saved memory found."

    return "\n".join(f"- {m}" for m in memories)
