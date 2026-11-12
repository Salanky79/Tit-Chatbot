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


def _to_object_id(val: Any) -> ObjectId | None:
    """Chuyển đổi chuỗi 24 hex hoặc ObjectId thành ObjectId hợp lệ."""
    if not val:
        return None
    if isinstance(val, ObjectId):
        return val
    if isinstance(val, str) and ObjectId.is_valid(val):
        return ObjectId(val)
    return None


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
    Tự động chuyển ObjectId thành string để tránh lỗi serialize JSON.
    """
    if doc is None:
        return None

    class _Row:
        pass

    obj = _Row()
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            value = str(value)
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


def get_user_by_id(user_id: str | ObjectId) -> dict | None:
    """Lấy thông tin user theo ID."""
    if not user_id:
        return None
    users = get_collection("users")
    try:
        query_id = _to_object_id(user_id) or user_id
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
    user_id: str | ObjectId,
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
    query_id = _to_object_id(user_id) or user_id
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

def create_session(user_id: str | ObjectId, duration_days: int = 7) -> str:
    """Tạo phiên đăng nhập an toàn mới lưu vào MongoDB."""
    session_id = secrets.token_urlsafe(32)
    now = _now()
    expires_at = now + timedelta(days=duration_days) # thời hạn mặc định 7 ngày

    oid = _to_object_id(user_id)
    sessions = get_collection("sessions")
    sessions.insert_one({
        "session_id": session_id,
        "user_id": oid or user_id,
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
    if "user_id" in session and session["user_id"] is not None:
        session["user_id"] = str(session["user_id"])
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
    - Guest (user_id=None): gắn ``is_guest=True`` và ``expires_at`` = 24h sau để TTL tự dọn.

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

    oid = _to_object_id(user_id)
    if oid:
        # Người dùng đã đăng nhập: gắn user_id (ObjectId), không có expires_at
        set_fields["user_id"] = oid
        set_on_insert["is_guest"] = False
    else:
        # Guest: đánh dấu và đặt TTL 24h để MongoDB tự dọn
        set_on_insert["is_guest"] = True
        set_on_insert["expires_at"] = now + timedelta(hours=24)

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
    doc = coll.find_one({"thread_id": thread_id}, {"_id": 0})
    if doc and isinstance(doc.get("user_id"), ObjectId):
        doc["user_id"] = str(doc["user_id"])
    return doc


def list_conversations(
    user_id: str | None = None,
    limit: int = 100
) -> list[Any]:
    """Lấy danh sách conversations theo user_id, ưu tiên các cuộc trò chuyện được ghim lên đầu.

    Chỉ trả về conversations của user đã đăng nhập.
    Guest (user_id=None) luôn nhận về [] — lịch sử không được lưu cho guest.
    """
    oid = _to_object_id(user_id)
    if not oid:
        return []  # Guest không có lịch sử conversation

    coll = get_collection("conversations")
    query = {"user_id": oid, "is_guest": {"$ne": True}}

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
        oid = _to_object_id(user_id)
        if oid:
            query["user_id"] = oid

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
        oid = _to_object_id(user_id)
        if oid:
            query["user_id"] = oid

    res = conv_coll.delete_one(query)
    if res.deleted_count > 0:
        get_collection("chat_messages").delete_many({"thread_id": thread_id})
        # long_term_memory gắn với user_id, không xóa theo thread_id đơn lẻ
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
    oid = _to_object_id(user_id)

    # Insert tin nhắn
    msg_coll = get_collection("chat_messages")
    doc: dict[str, Any] = {
        "thread_id": thread_id,
        "role": role,
        "content": content,
        "created_at": now,
    }
    if oid:
        doc["user_id"] = oid
    msg_coll.insert_one(doc)

    # Cập nhật updated_at của conversation
    conv_coll = get_collection("conversations")
    update_fields: dict[str, Any] = {"updated_at": now}
    if oid:
        update_fields["user_id"] = oid
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

    Bộ nhớ dài hạn gắn với ``user_id`` và dùng chung xuyên suốt TẤT CẢ
    các cuộc trò chuyện của người dùng đó (Cross-chat memory).

    Chỉ lưu khi đã đăng nhập (có user_id). Guest không có long-term memory.

    Args:
        thread_id: ID luồng chat hiện tại (chỉ lưu để trace, không dùng để query).
        memory: Nội dung cần ghi nhớ.
        user_id: ID người dùng — bắt buộc để lưu.

    Returns:
        Chuỗi xác nhận thành công hoặc thông báo yêu cầu đăng nhập.
    """
    oid = _to_object_id(user_id)
    if not oid:
        return "Memory is only available for signed-in users. Please sign in to save memories."

    coll = get_collection("long_term_memory")
    now = _now()
    doc: dict[str, Any] = {
        "user_id": oid,
        "memory": memory.strip(),
        "is_deleted": False,
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    if thread_id:
        doc["thread_id"] = thread_id  # lưu để trace nguồn gốc, không dùng để query
    coll.insert_one(doc)
    return "Memory saved successfully."


def search_memory(thread_id: str, query: str = "", user_id: str | None = None) -> str:
    """Tìm kiếm bộ nhớ dài hạn của người dùng trên TẤT CẢ các cuộc trò chuyện.

    - Lọc **chỉ theo ``user_id``** và trạng thái ``is_deleted: False``.
    - Chỉ hoạt động khi đã đăng nhập (có user_id). Guest nhận thông báo yêu cầu đăng nhập.
    - Quy trình tìm kiếm linh hoạt (Tránh đứt gãy do từ đồng nghĩa như 'trường' vs 'Đại học'):
      1. Khớp chính xác query bằng regex an toàn (re.escape).
      2. Nếu query nhiều từ, thử khớp theo từng từ khóa độc lập (any word match).
      3. Fallback: Nếu không tìm thấy hoặc query rỗng, trả về các ký ức gần nhất của người dùng
         để LLM đối chiếu và nhận diện thông tin (ví dụ: 'học ở đâu' -> 'học tại Đại học UET').

    Args:
        thread_id: ID luồng chat hiện tại (không dùng để lọc, chỉ nhận để tương thích).
        query: Từ khóa tìm kiếm cốt lõi do Agent trích xuất (hoặc "" nếu hỏi chung).
        user_id: ID người dùng — bắt buộc để tìm kiếm.

    Returns:
        Chuỗi kết quả, mỗi dòng là một mục nhớ, hoặc thông báo không tìm thấy.
    """
    oid = _to_object_id(user_id)
    if not oid:
        return "Memory search is only available for signed-in users. Please sign in to recall memories."

    coll = get_collection("long_term_memory")
    clean_query = (query or "").strip()

    # 1. Thử tìm kiếm khớp cụm từ regex
    if clean_query:
        cursor = coll.find(
            {"user_id": oid, "is_deleted": False, "memory": {"$regex": re.escape(clean_query), "$options": "i"}},
            {"_id": 0, "memory": 1},
        ).sort("created_at", -1).limit(20)
        memories = [doc["memory"] for doc in cursor]
        if memories:
            return "\n".join(f"- {m}" for m in memories)

        # 1b. Nếu query có nhiều từ (ví dụ 'học ở đâu', 'trường học'), tách từ để tìm khớp bất kỳ từ nào
        words = [w for w in re.split(r"[\s,;?]+", clean_query) if len(w) >= 2]
        if len(words) > 1:
            pattern = "|".join(re.escape(w) for w in words)
            cursor = coll.find(
                {"user_id": oid, "is_deleted": False, "memory": {"$regex": pattern, "$options": "i"}},
                {"_id": 0, "memory": 1},
            ).sort("created_at", -1).limit(20)
            memories = [doc["memory"] for doc in cursor]
            if memories:
                return "\n".join(f"- {m}" for m in memories)

    # 2. Fallback: Nếu không khớp từ khóa (do từ đồng nghĩa hoặc query lệch) hoặc query rỗng:
    # Lấy danh sách các ký ức gần nhất của người dùng để LLM tự đọc và suy luận thông tin
    fallback_cursor = coll.find(
        {"user_id": oid, "is_deleted": False},
        {"_id": 0, "memory": 1},
    ).sort("created_at", -1).limit(20)
    recent_memories = [doc["memory"] for doc in fallback_cursor]

    if not recent_memories:
        return "No saved memory found."

    return "\n".join(f"- {m}" for m in recent_memories)


def find_memory_candidates(user_id: str | None = None, keyword: str = "") -> list[dict]:
    """Bước 1 & 2: Tìm danh sách candidate memory theo từ khóa hoặc fallback danh sách gần nhất.

    Args:
        user_id: ID người dùng.
        keyword: Từ khóa tìm kiếm.

    Returns:
        Danh sách các candidate: [{"memory_id": str, "memory": str, "version": int}].
    """
    oid = _to_object_id(user_id)
    if not oid:
        return []

    coll = get_collection("long_term_memory")
    clean_keyword = (keyword or "").strip()

    def _format_docs(cursor):
        return [
            {
                "memory_id": str(doc["_id"]),
                "memory": doc.get("memory", ""),
                "version": int(doc.get("version", 1)),
            }
            for doc in cursor
        ]

    # 1. Khớp cụm từ regex
    if clean_keyword:
        cursor = coll.find(
            {"user_id": oid, "is_deleted": False, "memory": {"$regex": re.escape(clean_keyword), "$options": "i"}},
            {"_id": 1, "memory": 1, "version": 1, "updated_at": 1, "created_at": 1},
        ).sort("updated_at", -1).limit(10)
        candidates = _format_docs(cursor)
        if candidates:
            return candidates

        # 1b. Khớp bất kỳ từ nào nếu keyword có nhiều từ
        words = [w for w in re.split(r"[\s,;?]+", clean_keyword) if len(w) >= 2]
        if len(words) > 1:
            pattern = "|".join(re.escape(w) for w in words)
            cursor = coll.find(
                {"user_id": oid, "is_deleted": False, "memory": {"$regex": pattern, "$options": "i"}},
                {"_id": 1, "memory": 1, "version": 1, "updated_at": 1, "created_at": 1},
            ).sort("updated_at", -1).limit(10)
            candidates = _format_docs(cursor)
            if candidates:
                return candidates

    # 2. Fallback: Nếu không khớp hoặc keyword rỗng, trả về các ký ức gần nhất để người dùng lựa chọn
    fallback_cursor = coll.find(
        {"user_id": oid, "is_deleted": False},
        {"_id": 1, "memory": 1, "version": 1, "updated_at": 1, "created_at": 1},
    ).sort("updated_at", -1).limit(10)
    return _format_docs(fallback_cursor)


def update_memory_by_id(
    user_id: str | None,
    memory_id: str,
    new_memory: str,
    version: int
) -> str:
    """Bước 4: Sửa memory bằng ID + user_id + is_deleted + version."""
    oid = _to_object_id(user_id)
    mem_oid = _to_object_id(memory_id)
    if not oid:
        return "Vui lòng đăng nhập để cập nhật bộ nhớ."
    if not mem_oid:
        return "Mã memory_id không hợp lệ."

    coll = get_collection("long_term_memory")
    now = _now()

    filter_q = {
        "_id": mem_oid,
        "user_id": oid,
        "is_deleted": False,
        "version": int(version),
    }
    update_q = {
        "$set": {
            "memory": new_memory.strip(),
            "updated_at": now,
        },
        "$inc": {"version": 1},
    }

    res = coll.update_one(filter_q, update_q)
    if res.matched_count == 1:
        return f"Đã cập nhật thành công memory [{memory_id}] thành: '{new_memory.strip()}' (version mới: {int(version) + 1})."

    # Kiểm tra nguyên nhân không khớp
    existing = coll.find_one({"_id": mem_oid, "user_id": oid})
    if not existing:
        return f"Lỗi: Không tìm thấy memory với ID [{memory_id}]."
    if existing.get("is_deleted", False):
        return f"Lỗi: Memory [{memory_id}] đã bị xóa trước đó."
    current_ver = existing.get("version", 1)
    if current_ver != int(version):
        return f"Lỗi xung đột phiên bản: Memory [{memory_id}] hiện là version {current_ver}, khác với version {version} bạn cung cấp."

    return f"Lỗi: Không thể cập nhật memory [{memory_id}]."


def delete_memory_by_id(
    user_id: str | None,
    memory_id: str,
    version: int
) -> str:
    """Bước 4: Xóa mềm (soft-delete) memory bằng ID + user_id + is_deleted + version."""
    oid = _to_object_id(user_id)
    mem_oid = _to_object_id(memory_id)
    if not oid:
        return "Vui lòng đăng nhập để xóa bộ nhớ."
    if not mem_oid:
        return "Mã memory_id không hợp lệ."

    coll = get_collection("long_term_memory")
    now = _now()

    filter_q = {
        "_id": mem_oid,
        "user_id": oid,
        "is_deleted": False,
        "version": int(version),
    }
    update_q = {
        "$set": {
            "is_deleted": True,
            "deleted_at": now,
            "updated_at": now,
        },
        "$inc": {"version": 1},
    }

    res = coll.update_one(filter_q, update_q)
    if res.matched_count == 1:
        return f"Đã xóa thành công memory [{memory_id}]."

    existing = coll.find_one({"_id": mem_oid, "user_id": oid})
    if not existing:
        return f"Lỗi: Không tìm thấy memory với ID [{memory_id}]."
    if existing.get("is_deleted", False):
        return f"Memory [{memory_id}] đã được xóa từ trước."
    current_ver = existing.get("version", 1)
    if current_ver != int(version):
        return f"Lỗi xung đột phiên bản: Memory [{memory_id}] hiện là version {current_ver}, khác với version {version} bạn cung cấp."

    return f"Lỗi: Không thể xóa memory [{memory_id}]."
