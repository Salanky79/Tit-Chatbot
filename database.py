"""database.py – Lớp truy cập dữ liệu (DAL) dùng MongoDB thay cho SQLite/SQLAlchemy.

Các hàm public giữ nguyên tên và chữ ký để các module khác (app.py, tools.py, …)
tương thích hoàn toàn, đồng thời mở rộng hỗ trợ Quản lý User và Cách ly hội thoại.

Collections:
    - users           : thông tin tài khoản người dùng và mật khẩu băm
    - sessions        : phiên đăng nhập của người dùng (TTL tự động dọn dẹp)
    - conversations   : metadata của từng luồng chat (kèm user_id)
    - chat_messages   : nội dung từng tin nhắn (kèm user_id)
    - long_term_memory: bộ nhớ dài hạn do user yêu cầu lưu
"""

from datetime import datetime, timezone, timedelta
import re
import secrets
from typing import Any

import bcrypt
from bson import ObjectId

from mongo_client import get_collection, ensure_indexes


# ---------------------------------------------------------------------------
# Internal helpers & Security
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Trả về thời điểm hiện tại theo UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    """Băm mật khẩu an toàn sử dụng bcrypt với salt 12 rounds."""
    if not password:
        raise ValueError("Mật khẩu không được để trống.")
    salt = bcrypt.gensalt(rounds=12) # tránh mã băm cùng một mật khẩu giống nhau
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Xác thực mật khẩu thô với chuỗi băm bcrypt."""
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


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
# User Management APIs
# ---------------------------------------------------------------------------

def create_user(
    username: str,
    email: str,
    password: str,
    full_name: str = ""
) -> dict:
    """Đăng ký tài khoản người dùng mới.

    Args:
        username: Tên tài khoản (tối thiểu 3 ký tự, không dấu cách).
        email: Email hợp lệ.
        password: Mật khẩu (tối thiểu 6 ký tự).
        full_name: Tên hiển thị người dùng.

    Returns:
        dict thông tin user (đã loại bỏ hashed_password).
    """
    username = (username or "").strip().lower()
    email = (email or "").strip().lower()
    full_name = (full_name or "").strip()

    if not username or len(username) < 3:
        raise ValueError("Tên đăng nhập phải có ít nhất 3 ký tự.")
    if not re.match(r"^[a-zA-Z0-9_.-]+$", username):
        raise ValueError("Tên đăng nhập chỉ được chứa chữ cái, chữ số, dấu gạch dưới, gạch ngang và dấu chấm.")
    if not email or "@" not in email or "." not in email:
        raise ValueError("Định dạng email không hợp lệ.")
    if not password or len(password) < 6:
        raise ValueError("Mật khẩu phải có ít nhất 6 ký tự.")

    users = get_collection("users")
    if users.find_one({"username": username}):
        raise ValueError("Tên đăng nhập này đã được sử dụng.")
    if users.find_one({"email": email}):
        raise ValueError("Địa chỉ email này đã được đăng ký.")

    now = _now()
    hashed_pw = hash_password(password)
    user_doc = {
        "username": username,
        "email": email,
        "hashed_password": hashed_pw,
        "full_name": full_name or username,
        "avatar_url": "",
        "created_at": now,
        "updated_at": now,
    }

    result = users.insert_one(user_doc)
    user_id = str(result.inserted_id)

    return {
        "id": user_id,
        "username": username,
        "email": email,
        "full_name": user_doc["full_name"],
        "avatar_url": "",
        "created_at": now,
        "updated_at": now,
    }


def authenticate_user(username_or_email: str, password: str) -> dict | None:
    """Xác thực người dùng khi đăng nhập bằng username hoặc email.

    Returns:
        dict thông tin user (đã loại bỏ hashed_password) nếu đúng, ngược lại None.
    """
    identifier = (username_or_email or "").strip().lower()
    if not identifier or not password:
        return None

    users = get_collection("users")
    user = users.find_one({
        "$or": [
            {"username": identifier},
            {"email": identifier},
        ]
    })
    if not user:
        return None

    if not verify_password(password, user.get("hashed_password", "")):
        return None

    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "email": user["email"],
        "full_name": user.get("full_name", user["username"]),
        "avatar_url": user.get("avatar_url", ""),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


def get_user_by_id(user_id: str) -> dict | None:
    """Lấy thông tin user theo ID."""
    if not user_id:
        return None
    users = get_collection("users")
    try:
        query_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
        user = users.find_one({"_id": query_id})
    except Exception:
        return None

    if not user:
        return None

    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "email": user["email"],
        "full_name": user.get("full_name", user["username"]),
        "avatar_url": user.get("avatar_url", ""),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


def get_user_by_username(username: str) -> dict | None:
    """Lấy thông tin user theo username."""
    username = (username or "").strip().lower()
    if not username:
        return None
    users = get_collection("users")
    user = users.find_one({"username": username})
    if not user:
        return None

    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "email": user["email"],
        "full_name": user.get("full_name", user["username"]),
        "avatar_url": user.get("avatar_url", ""),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


def update_user_profile(
    user_id: str,
    *,
    full_name: str | None = None,
    avatar_url: str | None = None,
    email: str | None = None,
    new_password: str | None = None,
    current_password: str | None = None,
) -> dict:
    """Cập nhật thông tin cá nhân của người dùng (tên hiển thị, avatar, email, mật khẩu)."""
    if not user_id:
        raise ValueError("ID người dùng không hợp lệ.")

    users = get_collection("users")
    query_id = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
    user = users.find_one({"_id": query_id})
    if not user:
        raise ValueError("Không tìm thấy thông tin người dùng.")

    set_fields = {}
    now = _now()

    if full_name is not None:
        clean_name = full_name.strip()
        if clean_name:
            set_fields["full_name"] = clean_name

    if avatar_url is not None:
        set_fields["avatar_url"] = avatar_url.strip()

    if email is not None:
        clean_email = email.strip().lower()
        if not clean_email or "@" not in clean_email or "." not in clean_email:
            raise ValueError("Định dạng email không hợp lệ.")
        existing = users.find_one({"email": clean_email})
        if existing and str(existing.get("_id")) != str(user.get("_id")):
            raise ValueError("Địa chỉ email này đã được sử dụng bởi tài khoản khác.")
        set_fields["email"] = clean_email

    if new_password:
        if not current_password:
            raise ValueError("Vui lòng nhập mật khẩu hiện tại để đổi mật khẩu mới.")
        if not verify_password(current_password, user.get("hashed_password", "")):
            raise ValueError("Mật khẩu hiện tại không chính xác.")
        if len(new_password) < 6:
            raise ValueError("Mật khẩu mới phải có ít nhất 6 ký tự.")
        set_fields["hashed_password"] = hash_password(new_password)

    if set_fields:
        set_fields["updated_at"] = now
        users.update_one({"_id": query_id}, {"$set": set_fields})
        user = users.find_one({"_id": query_id})

    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "email": user["email"],
        "full_name": user.get("full_name", user["username"]),
        "avatar_url": user.get("avatar_url", ""),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Session Management APIs
# ---------------------------------------------------------------------------

def create_session(user_id: str, duration_days: int = 7) -> str:
    """Tạo phiên đăng nhập an toàn mới lưu vào MongoDB."""
    session_id = secrets.token_urlsafe(32)
    now = _now()
    expires_at = now + timedelta(days=duration_days) # thời hạn mặc định 7 ngày

    sessions = get_collection("sessions")
    sessions.insert_one({
        "session_id": session_id,
        "user_id": str(user_id),
        "created_at": now,
        "expires_at": expires_at,
    })
    return session_id


def get_session(session_id: str) -> dict | None:
    """Kiểm tra và lấy thông tin phiên đăng nhập còn hiệu lực."""
    if not session_id:
        return None
    sessions = get_collection("sessions")
    session = sessions.find_one({"session_id": session_id})
    if not session:
        return None

    # Kiểm tra hết hạn
    now = _now()
    expires_at = session.get("expires_at")
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            sessions.delete_one({"session_id": session_id})
            return None

    session["id"] = str(session["_id"])
    session.pop("_id", None)
    return session


def delete_session(session_id: str) -> bool:
    """Xóa phiên đăng nhập (khi người dùng đăng xuất)."""
    if not session_id:
        return False
    sessions = get_collection("sessions")
    res = sessions.delete_one({"session_id": session_id})
    return res.deleted_count > 0


# ---------------------------------------------------------------------------
# Conversation APIs
# ---------------------------------------------------------------------------

def create_or_update_conversation(
    thread_id: str,
    first_message: str | None = None,
    user_id: str | None = None,
) -> Any:
    """Tạo mới hoặc cập nhật conversation theo ``thread_id`` và ``user_id``.

    - Nếu chưa tồn tại: insert với ``title`` lấy từ ``first_message`` (40 ký tự đầu).
    - Nếu đã tồn tại: cập nhật ``updated_at`` (và gắn ``user_id`` nếu chưa có).

    Returns:
        Object-like với các thuộc tính ``thread_id``, ``title``,
        ``created_at``, ``updated_at``, ``user_id``.
    """
    coll = get_collection("conversations")
    now = _now()

    # Sinh title từ first_message
    title = "New Chat"
    if first_message:
        stripped = first_message.strip()
        title = stripped[:40] + ("..." if len(stripped) > 40 else "")

    set_fields: dict[str, Any] = {"updated_at": now}
    set_on_insert: dict[str, Any] = {
        "thread_id": thread_id,
        "title": title,
        "created_at": now,
        "is_pinned": False,
    }
    if user_id:
        set_fields["user_id"] = str(user_id)

    coll.update_one(
        {"thread_id": thread_id},
        {
            "$set": set_fields,
            "$setOnInsert": set_on_insert,
        },
        upsert=True,
    )

    doc = coll.find_one({"thread_id": thread_id}, {"_id": 0})
    return _conv_to_obj(doc)


def get_conversation(thread_id: str) -> dict | None:
    """Lấy thông tin chi tiết một cuộc trò chuyện để kiểm tra quyền sở hữu."""
    if not thread_id:
        return None
    coll = get_collection("conversations")
    return coll.find_one({"thread_id": thread_id}, {"_id": 0})


def list_conversations(
    user_id: str | None = None,
    limit: int = 100
) -> list[Any]:
    """Lấy danh sách conversations theo user_id, ưu tiên các cuộc trò chuyện được ghim lên đầu.

    Nếu user_id được chỉ định, chỉ lấy các cuộc trò chuyện của user đó.
    Nếu user_id là None, trả về các conversation công khai/mặc định.
    """
    coll = get_collection("conversations")
    query = {}
    if user_id is not None:
        query["user_id"] = str(user_id)

    try:
        cursor = (
            coll.find(query, {"_id": 0})
            .sort([("is_pinned", -1), ("updated_at", -1)])
            .limit(limit)
        )
        items = [_conv_to_obj(doc) for doc in cursor]
    except TypeError:
        cursor = (
            coll.find(query, {"_id": 0})
            .sort("updated_at", -1)
            .limit(limit)
        )
        items = [_conv_to_obj(doc) for doc in cursor]

    def _sort_key(item):
        pinned = bool(getattr(item, "is_pinned", False))
        ts = getattr(item, "pinned_at", None) or getattr(item, "updated_at", None) or getattr(item, "created_at", None)
        val = ts.timestamp() if hasattr(ts, "timestamp") else 0.0
        return (1 if pinned else 0, val)

    items.sort(key=_sort_key, reverse=True)
    return items


def toggle_pin_conversation(thread_id: str, user_id: str | None = None) -> bool | None:
    """Bật/tắt trạng thái ghim (is_pinned) của một cuộc trò chuyện."""
    if not thread_id:
        return None
    coll = get_collection("conversations")
    query: dict[str, Any] = {"thread_id": thread_id}
    if user_id is not None:
        query["user_id"] = str(user_id)

    doc = coll.find_one(query)
    if not doc:
        return None

    new_pinned = not bool(doc.get("is_pinned", False))
    now = _now()
    coll.update_one(
        {"thread_id": thread_id},
        {"$set": {"is_pinned": new_pinned, "pinned_at": now if new_pinned else None}}
    )
    return new_pinned


def delete_conversation(thread_id: str, user_id: str | None = None) -> bool:
    """Xóa cuộc trò chuyện và toàn bộ tin nhắn liên quan.
    Nếu truyền user_id, chỉ xóa nếu cuộc trò chuyện thuộc về user_id đó.
    """
    if not thread_id:
        return False
    conv_coll = get_collection("conversations")
    query: dict[str, Any] = {"thread_id": thread_id}
    if user_id is not None:
        query["user_id"] = str(user_id)

    res = conv_coll.delete_one(query)
    if res.deleted_count > 0:
        get_collection("chat_messages").delete_many({"thread_id": thread_id})
        get_collection("long_term_memory").delete_many({"thread_id": thread_id})
        return True
    return False


# ---------------------------------------------------------------------------
# Chat message APIs
# ---------------------------------------------------------------------------

def save_chat_message(
    thread_id: str,
    role: str,
    content: str,
    user_id: str | None = None,
) -> Any:
    """Lưu một tin nhắn vào collection ``chat_messages``.

    Đồng thời cập nhật ``updated_at`` của conversation tương ứng.

    Args:
        thread_id: ID luồng chat.
        role: ``"user"`` hoặc ``"assistant"``.
        content: Nội dung tin nhắn.
        user_id: ID người dùng gửi tin nhắn (nếu có).

    Returns:
        Object-like đại diện cho document vừa insert.
    """
    now = _now()

    # Insert tin nhắn
    msg_coll = get_collection("chat_messages")
    doc: dict[str, Any] = {
        "thread_id": thread_id,
        "role": role,
        "content": content,
        "created_at": now,
    }
    if user_id:
        doc["user_id"] = str(user_id)
    msg_coll.insert_one(doc)

    # Cập nhật updated_at của conversation
    conv_coll = get_collection("conversations")
    update_fields: dict[str, Any] = {"updated_at": now}
    if user_id:
        update_fields["user_id"] = str(user_id)
    conv_coll.update_one(
        {"thread_id": thread_id},
        {"$set": update_fields},
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

def save_memory(thread_id: str, memory: str, user_id: str | None = None) -> str:
    """Lưu một đoạn bộ nhớ dài hạn vào collection ``long_term_memory``.

    Bộ nhớ dài hạn được liên kết trực tiếp với ``user_id`` để dùng chung xuyên suốt
    cho TẤT CẢ các cuộc trò chuyện của người dùng đó (Cross-chat memory).

    Args:
        thread_id: ID luồng chat.
        memory: Nội dung cần ghi nhớ.
        user_id: ID người dùng (nếu có) để gắn kết và cách ly bộ nhớ.

    Returns:
        Chuỗi xác nhận thành công.
    """
    if not user_id and thread_id:
        conv = get_conversation(thread_id)
        if conv and conv.get("user_id"):
            user_id = str(conv["user_id"])

    coll = get_collection("long_term_memory")
    doc: dict[str, Any] = {
        "thread_id": thread_id,
        "memory": memory,
        "created_at": _now(),
    }
    if user_id:
        doc["user_id"] = str(user_id)
    coll.insert_one(doc)
    return "Memory saved successfully."


def search_memory(thread_id: str, query: str = "", user_id: str | None = None) -> str:
    """Tìm kiếm bộ nhớ dài hạn của người dùng trên TẤT CẢ các cuộc trò chuyện.

    - Nếu user_id được xác định (trực tiếp hoặc qua thread_id), tìm kiếm trên TOÀN BỘ
      các ký ức của user_id đó bất kể được lưu từ cuộc trò chuyện nào.
    - Đảm bảo cô lập tuyệt đối: Chỉ lấy ký ức của đúng user đó, không lẫn sang user khác.
    - Hỗ trợ câu hỏi tự nhiên và tìm kiếm từ khóa linh hoạt:
      + Nếu query chung chung (ví dụ: 'tất cả', 'về tôi', 'nhớ gì', 'bạn nhớ gì về tôi', ''): lấy toàn bộ ký ức của user.
      + Nếu query cụ thể (ví dụ: 'tôi tên là gì', 'sở thích'): trích xuất từ khóa chính để tìm kiếm chính xác.

    Args:
        thread_id: ID luồng chat hiện tại.
        query: Từ khóa hoặc câu hỏi tìm kiếm.
        user_id: ID người dùng để lọc đúng bộ nhớ của user.

    Returns:
        Chuỗi kết quả, mỗi dòng là một mục nhớ, hoặc thông báo không tìm thấy.
    """
    if not user_id and thread_id:
        conv = get_conversation(thread_id)
        if conv and conv.get("user_id"):
            user_id = str(conv["user_id"])

    coll = get_collection("long_term_memory")

    # 1. Điều kiện cô lập theo user_id hoặc fallback thread_id
    base_filter: dict[str, Any] = {}
    if user_id:
        base_filter["user_id"] = str(user_id)
    else:
        base_filter["thread_id"] = thread_id

    # 2. Xử lý query tìm kiếm linh hoạt
    clean_query = (query or "").strip().lower()
    generic_markers = {
        "", "all", "everything", "any", "me", "about me", "my profile",
        "tất cả", "tat ca", "về tôi", "ve toi", "thông tin", "thong tin",
        "nhớ gì", "nho gi", "bạn nhớ gì", "ban nho gi", "thông tin về tôi",
        "thong tin ve toi", "nhớ", "nho", "gì về tôi", "gi ve toi"
    }

    if not clean_query or clean_query in generic_markers:
        search_filter = base_filter
    else:
        # Tách các từ khóa có nghĩa để tìm kiếm linh hoạt hơn
        stop_words = {
            "là", "gì", "của", "tôi", "cho", "biết", "what", "is", "my",
            "the", "a", "an", "do", "you", "remember", "about", "bạn",
            "có", "nhớ", "nào", "không", "ko"
        }
        words = [
            w for w in re.split(r"[\s,;?!\.]+", clean_query)
            if len(w) > 1 and w not in stop_words
        ]
        if words:
            # Khớp câu gốc HOẶC khớp bất kỳ từ khóa chính nào
            patterns = [re.escape(clean_query)] + [re.escape(w) for w in words]
            combined = "|".join(patterns)
            search_filter = {
                **base_filter,
                "memory": {"$regex": combined, "$options": "i"},
            }
        else:
            search_filter = {
                **base_filter,
                "memory": {"$regex": re.escape(clean_query), "$options": "i"},
            }

    cursor = (
        coll.find(
            search_filter,
            {"_id": 0, "memory": 1},
        )
        .sort("created_at", -1)
        .limit(20)
    )

    memories = [doc["memory"] for doc in cursor]

    if not memories:
        return "No saved memory found."

    return "\n".join(f"- {m}" for m in memories)
