"""Conversation and chat-message persistence operations."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from bson import ObjectId
from storage.mongo_client import get_collection as _mongo_get_collection
from storage._helpers import (
    _conv_to_obj as _conv_to_obj_impl,
    _now as _now_impl,
    _to_object_id as _to_object_id_impl,
    database_callable,
    logger,
)

get_collection = database_callable("get_collection", _mongo_get_collection)
_now = database_callable("_now", _now_impl)
_to_object_id = database_callable("_to_object_id", _to_object_id_impl)
_conv_to_obj = database_callable("_conv_to_obj", _conv_to_obj_impl)

def create_or_update_conversation(
    thread_id: str,
    first_message: str | None = None,
    user_id: str | None = None,
    is_temporary: bool = False,
    project_id: str | None = None,
) -> Any:
    """Tạo hoặc cập nhật chat, đồng thời giữ nguyên Project scope và creator."""
    """Tạo mới hoặc cập nhật conversation theo ``thread_id`` và ``user_id``.

    - Nếu chưa tồn tại: insert với ``title`` lấy từ ``first_message`` (40 ký tự đầu).
    - Nếu đã tồn tại: cập nhật ``updated_at`` (và gắn ``user_id`` nếu chưa có).
    - Temporary (is_temporary=True): gắn ``is_temporary=True`` và đặt TTL (30 ngày) để tự dọn dẹp.
    - Guest (user_id=None): gắn ``is_guest=True`` và ``expires_at`` = 24h sau để TTL tự dọn.

    Returns:
        Object-like với các thuộc tính ``thread_id``, ``title``,
        ``created_at``, ``updated_at``, ``user_id``, ``is_temporary``.
    """
    coll = get_collection("conversations")
    now = _now()
    project_oid = _to_object_id(project_id)
    existing = coll.find_one({"thread_id": thread_id}, {"project_id": 1, "user_id": 1})
    if existing:
        existing_project = existing.get("project_id")
        if isinstance(existing_project, ObjectId):
            existing_project = str(existing_project)
        requested_project = str(project_oid) if project_oid else None
        if existing_project != requested_project:
            raise ValueError("Conversation already belongs to a different project scope.")

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
    if project_oid:
        set_on_insert["project_id"] = project_oid

    if is_temporary:
        set_on_insert["is_temporary"] = True
        set_fields["is_temporary"] = True
        expiry = now + timedelta(days=30)
        set_on_insert["expires_at"] = expiry
        set_fields["expires_at"] = expiry

    oid = _to_object_id(user_id)
    if oid:
        # Người dùng đã đăng nhập: gắn user_id (ObjectId)
        if not project_oid:
            set_fields["user_id"] = oid
        else:
            set_on_insert["user_id"] = oid
        set_on_insert["is_guest"] = False
        set_on_insert["created_by"] = oid
    else:
        # Guest: đánh dấu và đặt TTL 24h để MongoDB tự dọn
        set_on_insert["is_guest"] = True
        if not is_temporary:
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
    if doc and isinstance(doc.get("project_id"), ObjectId):
        doc["project_id"] = str(doc["project_id"])
    return _conv_to_obj(doc)


def get_conversation(thread_id: str) -> dict | None:
    """Lấy metadata của một chat theo thread ID."""
    """Lấy thông tin chi tiết một cuộc trò chuyện để kiểm tra quyền sở hữu."""
    if not thread_id:
        return None
    coll = get_collection("conversations")
    doc = coll.find_one({"thread_id": thread_id}, {"_id": 0})
    if doc and isinstance(doc.get("user_id"), ObjectId):
        doc["user_id"] = str(doc["user_id"])
    if doc and isinstance(doc.get("project_id"), ObjectId):
        doc["project_id"] = str(doc["project_id"])
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
    query = {
        "user_id": oid,
        "is_guest": {"$ne": True},
        "is_temporary": {"$ne": True},
        "$or": [{"project_id": {"$exists": False}}, {"project_id": None}],
    }

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


def list_admin_conversations(
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    search: str = "",
) -> dict[str, Any]:
    """Tìm và phân trang hội thoại của một user để Admin xem.

    User ID được xác thực thành ObjectId trước mọi truy vấn. Guest và hội thoại
    không thuộc user đã chọn không xuất hiện trong kết quả.
    """
    oid = _to_object_id(user_id)
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    if oid is None:
        return {"items": [], "page": page, "page_size": page_size, "total": 0}

    query: dict[str, Any] = {"user_id": oid, "is_guest": {"$ne": True}}
    conversations = get_collection("conversations")
    clean_search = (search or "").strip()[:100]

    total = conversations.count_documents(query)
    cursor = (
        conversations.find(
            query,
            {
                "_id": 0,
                "thread_id": 1,
                "title": 1,
                "created_at": 1,
                "updated_at": 1,
                "is_pinned": 1,
            },
        )
        .sort("updated_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    all_items = list(cursor)
    if clean_search:
        term = clean_search.casefold()
        all_items = [c for c in all_items if term in (c.get("title") or "").casefold()]
    return {
        "items": all_items,
        "page": page,
        "page_size": page_size,
        "total": total,
    }


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


def update_conversation_title(
    thread_id: str,
    new_title: str,
    user_id: str | None = None
) -> bool:
    """Cập nhật tiêu đề cuộc trò chuyện, có kiểm tra quyền sở hữu user_id."""
    if not thread_id or not isinstance(new_title, str) or not new_title.strip():
        return False

    clean_title = new_title.strip()[:100]
    coll = get_collection("conversations")
    query: dict[str, Any] = {"thread_id": thread_id}
    if user_id is not None:
        oid = _to_object_id(user_id)
        query["user_id"] = {"$in": [oid, str(oid)]} if oid else user_id

    res = coll.update_one(
        query,
        {"$set": {"title": clean_title, "updated_at": _now()}}
    )
    return res.matched_count > 0


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

    existing_conv = conv_coll.find_one(query, {"project_id": 1})
    res = conv_coll.delete_one(query)
    if res.deleted_count > 0:
        get_collection("chat_messages").delete_many({"thread_id": thread_id})
        job_filter: dict[str, Any] = {"context.thread_id": thread_id}
        if user_id is not None:
            job_filter["context.user_id"] = str(user_id)
        summary_jobs = get_collection("summary_jobs")
        job_ids = [job["_id"] for job in summary_jobs.find(job_filter, {"_id": 1})]
        if job_ids:
            get_collection("summary_job_checkpoints").delete_many({"job_id": {"$in": job_ids}})
        summary_jobs.delete_many(job_filter)

        # Dọn dẹp tài liệu vật lý và vector ChromaDB gắn với thread_id này
        if not (existing_conv and existing_conv.get("project_id")):
            try:
                from services.rag import delete_thread_documents
                delete_thread_documents(thread_id)
            except Exception as exc:
                logger.warning("Could not clean thread documents %s: %s", thread_id, exc)

        # long_term_memory gắn với user_id, không xóa theo thread_id đơn lẻ
        return True
    return False


def assign_conversation_to_project(
    thread_id: str,
    user_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Chuyển một cuộc trò chuyện cá nhân vào Project với kiểm tra quyền owner/editor.

    Kiểm tra cuộc trò chuyện thuộc về ``user_id``, không phải guest/tạm thời và chưa
    thuộc Project nào khác; đồng thời xác nhận ``user_id`` có quyền ``owner`` hoặc
    ``editor`` trong Project đích trước khi cập nhật ``conversations``,
    ``chat_messages``, ``summary_jobs`` và ``projects``.
    """
    if not thread_id or not user_id or not project_id:
        raise ValueError("Thiếu thông tin cuộc trò chuyện, người dùng hoặc Project.")

    user_oid = _to_object_id(user_id)
    project_oid = _to_object_id(project_id)
    if user_oid is None or project_oid is None:
        raise ValueError("ID người dùng hoặc ID Project không hợp lệ.")

    conv_coll = get_collection("conversations")
    conv = conv_coll.find_one({"thread_id": thread_id})
    if not conv:
        raise LookupError("Cuộc trò chuyện không tồn tại.")

    conv_owner = conv.get("user_id")
    if conv.get("is_guest") or not conv_owner or str(conv_owner) != str(user_oid):
        raise PermissionError("Bạn không có quyền chuyển cuộc trò chuyện này.")
    if conv.get("is_temporary"):
        raise ValueError("Không thể thêm cuộc trò chuyện tạm thời vào Project.")
    if conv.get("project_id"):
        raise ValueError("Cuộc trò chuyện này đã thuộc một Project.")

    from storage.projects import can_edit_project, get_project
    if not get_project(project_id):
        raise LookupError("Project không tồn tại.")
    if not can_edit_project(project_id, user_id):
        raise PermissionError("Chỉ owner hoặc editor mới được thêm cuộc trò chuyện vào Project.")

    now = _now()
    # Cập nhật phạm vi Project cho cuộc trò chuyện; giữ nguyên user_id làm người tạo gốc.
    conv_coll.update_one(
        {"thread_id": thread_id},
        {"$set": {"project_id": project_oid, "updated_at": now, "last_actor_id": user_oid}},
    )
    msg_coll = get_collection("chat_messages")
    msg_coll.update_many(
        {"thread_id": thread_id},
        {"$set": {"project_id": project_oid}},
    )
    # Đảm bảo tin nhắn người dùng trước đó có sender_id để hiển thị đúng tác giả trong Project.
    msg_coll.update_many(
        {"thread_id": thread_id, "role": "user", "sender_id": {"$exists": False}},
        {"$set": {"sender_id": user_oid}},
    )
    get_collection("summary_jobs").update_many(
        {"context.thread_id": thread_id},
        {"$set": {"context.project_id": str(project_oid)}},
    )
    get_collection("projects").update_one(
        {"_id": project_oid},
        {"$set": {"updated_at": now}},
    )

    return get_conversation(thread_id) or {
        "thread_id": thread_id,
        "project_id": str(project_oid),
    }


def save_chat_message(
    thread_id: str,
    role: str,
    content: str,
    user_id: str | None = None,
) -> Any:
    """Lưu tin nhắn và ghi riêng sender khi chat thuộc Project."""
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
        if role == "user":
            # Keep the author on each message; the conversation's user_id stays
            # the original creator when multiple project members share a chat.
            doc["sender_id"] = oid

    # Cập nhật updated_at của conversation
    conv_coll = get_collection("conversations")
    conv_doc = conv_coll.find_one({"thread_id": thread_id}, {"is_temporary": 1, "expires_at": 1, "project_id": 1})
    if conv_doc and conv_doc.get("is_temporary") and conv_doc.get("expires_at"):
        doc["expires_at"] = conv_doc["expires_at"]
    if conv_doc and conv_doc.get("project_id"):
        doc["project_id"] = conv_doc["project_id"]
        get_collection("projects").update_one(
            {"_id": conv_doc["project_id"]}, {"$set": {"updated_at": now}}
        )

    msg_coll.insert_one(doc)
    update_fields: dict[str, Any] = {"updated_at": now}
    if oid:
        if conv_doc and conv_doc.get("project_id"):
            update_fields["last_actor_id"] = oid
        else:
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


def get_admin_conversation_messages(
    user_id: str,
    thread_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any] | None:
    """Lấy một trang tin nhắn sau khi xác nhận hội thoại thuộc về user đã chọn.

    Trả ``None`` nếu user ID không hợp lệ hoặc hội thoại không thuộc user đó.
    Tin nhắn được lọc đồng thời theo ``user_id`` và ``thread_id`` để không thể
    dùng API này đọc nội dung của tài khoản khác.
    """
    oid = _to_object_id(user_id)
    if oid is None or not thread_id:
        return None

    conversation_query = {
        "user_id": oid,
        "thread_id": thread_id,
        "is_guest": {"$ne": True},
    }
    conversation = get_collection("conversations").find_one(
        conversation_query,
        {
            "_id": 0,
            "thread_id": 1,
            "title": 1,
            "created_at": 1,
            "updated_at": 1,
            "project_id": 1,
        },
    )
    if not conversation:
        return None

    is_project_conv = bool(conversation.get("project_id"))
    if isinstance(conversation.get("project_id"), ObjectId):
        conversation["project_id"] = str(conversation["project_id"])

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    message_query = (
        {"thread_id": thread_id}
        if is_project_conv
        else {"user_id": oid, "thread_id": thread_id}
    )
    messages = get_collection("chat_messages")
    total = messages.count_documents(message_query)
    cursor = (
        messages.find(
            message_query,
            {"_id": 0, "role": 1, "content": 1, "created_at": 1},
        )
        .sort("created_at", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "conversation": conversation,
        "messages": list(cursor),
        "page": page,
        "page_size": page_size,
        "total": total,
    }
