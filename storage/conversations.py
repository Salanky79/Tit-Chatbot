"""Conversation and chat-message persistence operations."""

from __future__ import annotations

import re
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
    """Tạo mới hoặc cập nhật conversation theo ``thread_id`` và ``user_id``, giữ nguyên Project scope.

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

    # Bước 1: Nếu thread_id đã tồn tại, kiểm tra tránh đổi nhầm phạm vi Project <-> Cá nhân
    existing = coll.find_one({"thread_id": thread_id}, {"project_id": 1, "user_id": 1})
    if existing:
        existing_project = existing.get("project_id")
        if isinstance(existing_project, ObjectId):
            existing_project = str(existing_project)
        requested_project = str(project_oid) if project_oid else None
        if existing_project != requested_project:
            raise ValueError("Conversation already belongs to a different project scope.")

    # Bước 2: Tạo tiêu đề mặc định từ tin nhắn đầu tiên (cắt gọn tối đa 40 ký tự)
    title = "New Chat"
    if first_message:
        stripped = first_message.strip()
        title = stripped[:40] + ("..." if len(stripped) > 40 else "")

    # Bước 3: Tách các trường cập nhật mỗi lượt ($set) và trường chỉ gán lúc tạo mới ($setOnInsert)
    set_fields: dict[str, Any] = {"updated_at": now}
    set_on_insert: dict[str, Any] = {
        "thread_id": thread_id,
        "title": title,
        "created_at": now,
        "is_pinned": False,
    }
    if project_oid:
        set_on_insert["project_id"] = project_oid

    # Nếu là Temporary Chat -> đặt mốc expires_at sau 30 ngày để MongoDB TTL Index tự xóa
    if is_temporary:
        set_on_insert["is_temporary"] = True
        set_fields["is_temporary"] = True
        expiry = now + timedelta(days=30)
        set_on_insert["expires_at"] = expiry
        set_fields["expires_at"] = expiry

    oid = _to_object_id(user_id)
    if oid:
        # Người dùng đã đăng nhập: gắn user_id (trong Project chỉ gán lúc insert để giữ nguyên chủ tạo chat gốc)
        if not project_oid: # chat cá nhân
            set_fields["user_id"] = oid
        else: # chat project
            set_on_insert["user_id"] = oid 
        set_on_insert["is_guest"] = False
        set_on_insert["created_by"] = oid
    else:
        # Guest chưa đăng nhập: đánh dấu is_guest và đặt TTL 24h để tự dọn rác
        set_on_insert["is_guest"] = True
        if not is_temporary:
            set_on_insert["expires_at"] = now + timedelta(hours=24)

    # Bước 4: Upsert vào collection conversations và trả về object chuẩn hóa
    coll.update_one(
        {"thread_id": thread_id},
        {
            "$set": set_fields,
            "$setOnInsert": set_on_insert, # Giữ nguyên người tạo thread gốc 
        },
        upsert=True,
    )

    doc = coll.find_one({"thread_id": thread_id}, {"_id": 0})
    if doc and isinstance(doc.get("project_id"), ObjectId):
        doc["project_id"] = str(doc["project_id"])
    return _conv_to_obj(doc)


def get_conversation(thread_id: str) -> dict | None:
    """Lấy metadata của một cuộc trò chuyện theo thread_id để kiểm tra quyền truy cập."""
    if not thread_id:
        return None
    coll = get_collection("conversations")
    doc = coll.find_one({"thread_id": thread_id}, {"_id": 0})
    # Ép ObjectId về chuỗi str để các tầng API/kiểm tra quyền so sánh đồng nhất
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
    # Chỉ lấy chat cá nhân thực sự: bỏ guest, bỏ temporary chat, và bỏ chat thuộc Project
    query = {
        "user_id": oid,
        "is_guest": {"$ne": True},
        "is_temporary": {"$ne": True},
        "$or": [{"project_id": {"$exists": False}}, {"project_id": None}],
    }

    try:
        # Ưu tiên sort trực tiếp trên MongoDB theo trạng thái ghim rồi tới thời gian cập nhật
        cursor = (
            coll.find(query, {"_id": 0})
            .sort([("is_pinned", -1), ("updated_at", -1)])
            .limit(limit)
        )
        items = [_conv_to_obj(doc) for doc in cursor]
    except TypeError:
        # Fallback nếu mock DB trong unit test chỉ hỗ trợ sort theo 1 trường đơn
        cursor = (
            coll.find(query, {"_id": 0})
            .sort("updated_at", -1)
            .limit(limit)
        )
        items = [_conv_to_obj(doc) for doc in cursor]

    # Sắp xếp lại trên Python để đảm bảo mục đã ghim (theo pinned_at mới nhất) luôn đứng trên cùng
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
    # Với chat cá nhân: bắt buộc khớp user_id để user khác không ghim/bỏ ghim hộ được
    if user_id is not None:
        oid = _to_object_id(user_id)
        if oid:
            query["user_id"] = oid

    doc = coll.find_one(query)
    if not doc:
        return None

    # Đảo trạng thái ghim và lưu kèm pinned_at để sắp xếp các đoạn chat ghim gần nhất lên trên
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

    # Chuẩn hóa độ dài tiêu đề tối đa 100 ký tự để hiển thị gọn trên sidebar
    clean_title = new_title.strip()[:100]
    coll = get_collection("conversations")
    query: dict[str, Any] = {"thread_id": thread_id}
    if user_id is not None:
        oid = _to_object_id(user_id)
        # Chấp nhận cả ObjectId lẫn chuỗi string cũ (nếu có dữ liệu legacy)
        query["user_id"] = {"$in": [oid, str(oid)]} if oid else user_id

    res = coll.update_one(
        query,
        {"$set": {"title": clean_title, "updated_at": _now()}}
    )
    return res.matched_count > 0


def delete_conversation(thread_id: str, user_id: str | None = None) -> bool:
    """Xóa cuộc trò chuyện và toàn bộ tin nhắn, summary job, tài liệu RAG liên quan.

    Nếu truyền ``user_id``, chỉ xóa khi cuộc trò chuyện thuộc về user đó.
    """
    if not thread_id:
        return False
    conv_coll = get_collection("conversations")
    query: dict[str, Any] = {"thread_id": thread_id}
    if user_id is not None:
        oid = _to_object_id(user_id)
        if oid:
            query["user_id"] = oid

    # Đọc trước project_id để lát nữa biết có cần dọn file upload/ChromaDB riêng của thread hay không
    existing_conv = conv_coll.find_one(query, {"project_id": 1})
    res = conv_coll.delete_one(query)
    if res.deleted_count > 0:
        # 1. Xóa toàn bộ tin nhắn trong chat_messages
        get_collection("chat_messages").delete_many({"thread_id": thread_id})
        # 2. Xóa các tác vụ tóm tắt tài liệu (summary_jobs) và checkpoint Map-Reduce của thread
        job_filter: dict[str, Any] = {"context.thread_id": thread_id}
        if user_id is not None:
            job_filter["context.user_id"] = str(user_id)
        summary_jobs = get_collection("summary_jobs")
        job_ids = [job["_id"] for job in summary_jobs.find(job_filter, {"_id": 1})]
        if job_ids:
            get_collection("summary_job_checkpoints").delete_many({"job_id": {"$in": job_ids}})
        summary_jobs.delete_many(job_filter)

        # 3. Chỉ xóa file upload/vector ChromaDB khi là chat cá nhân (file trong Project dùng chung nhiều chat)
        if not (existing_conv and existing_conv.get("project_id")):
            try:
                from services.rag import delete_thread_documents
                delete_thread_documents(thread_id)
            except Exception as exc:
                logger.warning("Could not clean thread documents %s: %s", thread_id, exc)

        # Lưu ý: long_term_memory gắn theo user_id lâu dài nên không bị xóa theo từng thread
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
    # Bước 1: Xác thực định dạng ID đầu vào
    if not thread_id or not user_id or not project_id:
        raise ValueError("Thiếu thông tin cuộc trò chuyện, người dùng hoặc Project.")

    user_oid = _to_object_id(user_id)
    project_oid = _to_object_id(project_id)
    if user_oid is None or project_oid is None:
        raise ValueError("ID người dùng hoặc ID Project không hợp lệ.")

    # Bước 2: Kiểm tra cuộc trò chuyện tồn tại, thuộc về user_id, không phải guest/temp/đã gắn Project khác
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

    # Bước 3: Xác minh Project tồn tại và người dùng có quyền owner/editor trong Project đó
    from storage.projects import can_edit_project, get_project
    if not get_project(project_id):
        raise LookupError("Project không tồn tại.")
    if not can_edit_project(project_id, user_id):
        raise PermissionError("Chỉ owner hoặc editor mới được thêm cuộc trò chuyện vào Project.")

    # Bước 4: Gắn project_id đồng bộ qua conversations, chat_messages, summary_jobs và projects
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
    """Lưu một tin nhắn vào ``chat_messages`` và cập nhật ``updated_at`` / ``sender_id`` khi chat thuộc Project.

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

    # Bước 1: Chuẩn bị document tin nhắn mới
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
            # Lưu sender_id trên từng tin nhắn user; conversation.user_id vẫn giữ nguyên là người tạo thread
            # để phân biệt người gửi khi nhiều thành viên cùng chat trong 1 Project.
            doc["sender_id"] = oid

    # Bước 2: Kế thừa hạn expires_at (nếu temporary chat) và project_id (nếu chat thuộc Project)
    conv_coll = get_collection("conversations")
    conv_doc = conv_coll.find_one({"thread_id": thread_id}, {"is_temporary": 1, "expires_at": 1, "project_id": 1})
    if conv_doc and conv_doc.get("is_temporary") and conv_doc.get("expires_at"):
        doc["expires_at"] = conv_doc["expires_at"]
    if conv_doc and conv_doc.get("project_id"):
        doc["project_id"] = conv_doc["project_id"]
        get_collection("projects").update_one(
            {"_id": conv_doc["project_id"]}, {"$set": {"updated_at": now}}
        )

    # Bước 3: Insert tin nhắn và cập nhật dấu thời gian hoạt động cuối cùng của conversation
    msg_coll.insert_one(doc)
    update_fields: dict[str, Any] = {"updated_at": now}
    if oid:
        if conv_doc and conv_doc.get("project_id"):
            # Trong Project: ghi nhận người tương tác gần nhất vào last_actor_id thay vì ghi đè user_id
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
    """Lấy lịch sử chat của một thread, sắp xếp cũ nhất trước để dựng lại khung hội thoại."""
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


def _build_search_snippet(text: str, query: str, max_len: int = 120) -> str:
    """Tạo đoạn trích ngắn (~120 ký tự) cắt quanh vị trí từ khóa xuất hiện trong nội dung tin nhắn."""
    # Gộp nhiều khoảng trắng / xuống dòng thành 1 dấu cách để đoạn trích hiển thị trên 1 dòng gọn gàng
    clean_text = " ".join((text or "").split())
    if not clean_text:
        return ""
    if len(clean_text) <= max_len:
        return clean_text

    # Tìm vị trí đầu tiên của từ khóa (không phân biệt hoa/thường) và lấy cửa sổ đối xứng quanh đó
    idx = clean_text.casefold().find(query.casefold())
    if idx < 0:
        return clean_text[: max_len - 1] + "…"
    half = max(20, (max_len - len(query)) // 2)
    start = max(0, idx - half)
    end = min(len(clean_text), idx + len(query) + half)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(clean_text) else ""
    return f"{prefix}{clean_text[start:end]}{suffix}"


def _iso_str(val: Any) -> str:
    """Chuẩn hóa đối tượng datetime về chuỗi ISO 8601 để frontend phân nhóm thời gian."""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val or "")


def _format_search_item(
    meta: dict[str, Any],
    *,
    snippet: str = "",
    match_type: str = "recent",
) -> dict[str, Any]:
    """Đóng gói metadata cuộc trò chuyện thành payload JSON thống nhất cho API tìm kiếm."""
    return {
        "thread_id": meta["thread_id"],
        "title": meta["title"],
        "project_id": meta["project_id"],
        "project_name": meta["project_name"],
        "updated_at": meta["updated_at"],
        "is_pinned": meta["is_pinned"],
        "snippet": snippet,
        "match_type": match_type,
    }


def search_user_conversations(
    user_id: str | None,
    query: str = "",
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Tìm kiếm các cuộc trò chuyện cá nhân và Project của user theo tiêu đề lẫn nội dung tin nhắn.

    - Nếu ``query`` rỗng: trả về danh sách trò chuyện gần đây nhất mà user có quyền truy cập.
    - Nếu ``query`` khác rỗng: tìm khớp tiêu đề và nội dung trong ``chat_messages`` thuộc các thread đó.
    """
    # Guest chưa đăng nhập không có lịch sử lưu trữ dài hạn nên trả về rỗng
    oid = _to_object_id(user_id)
    if not oid:
        return []

    safe_limit = min(max(1, int(limit or 25)), 50)
    clean_query = (query or "").strip()[:120]

    # Bước 1: Lấy danh sách Project mà user là owner hoặc thành viên đã chấp nhận (accepted)
    # để mở rộng phạm vi tìm kiếm sang cả chat nhóm trong các Project đó.
    from storage.projects import list_projects_for_user

    project_names: dict[str, str] = {}
    project_oids: list[ObjectId] = []
    for proj in list_projects_for_user(str(oid)):
        pid = str(proj.get("_id") or proj.get("id") or "")
        p_oid = _to_object_id(pid)
        if p_oid:
            project_oids.append(p_oid)
            project_names[str(p_oid)] = str(proj.get("name") or "Project")

    # Bước 2: Dựng bộ lọc quyền truy cập gồm chat cá nhân của user + chat thuộc Project hợp lệ
    # Luôn loại bỏ temporary chat và guest chat khỏi kết quả tìm kiếm.
    personal_scope: dict[str, Any] = {
        "user_id": oid,
        "is_guest": {"$ne": True},
        "is_temporary": {"$ne": True},
        "$or": [{"project_id": {"$exists": False}}, {"project_id": None}],
    }
    if project_oids:
        conv_filter: dict[str, Any] = {
            "$or": [
                personal_scope,
                {"project_id": {"$in": project_oids}, "is_temporary": {"$ne": True}},
            ]
        }
    else:
        conv_filter = personal_scope

    # Truy vấn tối đa 250 cuộc trò chuyện gần nhất thuộc phạm vi quyền của user
    conv_coll = get_collection("conversations")
    conv_docs = list(
        conv_coll.find(
            conv_filter,
            {"_id": 0, "thread_id": 1, "title": 1, "project_id": 1, "updated_at": 1, "created_at": 1, "is_pinned": 1},
        )
        .sort("updated_at", -1)
        .limit(250)
    )
    if not conv_docs:
        return []

    # Bước 3: Lập từ điển metadata theo thread_id và giữ thứ tự mới nhất trước
    conv_by_thread: dict[str, dict[str, Any]] = {}
    ordered_threads: list[str] = []
    for doc in conv_docs:
        tid = str(doc.get("thread_id") or "")
        if not tid or tid in conv_by_thread:
            continue
        pid_raw = doc.get("project_id")
        pid_str = str(pid_raw) if pid_raw else ""
        conv_by_thread[tid] = {
            "thread_id": tid,
            "title": str(doc.get("title") or "Đoạn chat mới"),
            "project_id": pid_str,
            "project_name": project_names.get(pid_str, "") if pid_str else "",
            "updated_at": _iso_str(doc.get("updated_at") or doc.get("created_at")),
            "is_pinned": bool(doc.get("is_pinned", False)),
            "_ts": doc.get("updated_at").timestamp() if hasattr(doc.get("updated_at"), "timestamp") else 0.0,
        }
        ordered_threads.append(tid)

    # Bước 4: Nếu người dùng chưa gõ từ khóa -> trả về danh sách trò chuyện gần đây nhất
    if not clean_query:
        return [
            _format_search_item(conv_by_thread[tid], snippet="", match_type="recent")
            for tid in ordered_threads[:safe_limit]
        ]

    # Bước 5: Tìm các tin nhắn chứa từ khóa bên trong phạm vi ordered_threads (chỉ những thread user được phép xem)
    # Dùng re.escape để các ký tự đặc biệt như C++, [test], a.b không bị hiểu nhầm thành regex syntax.
    term = clean_query.casefold()
    escaped_regex = re.escape(clean_query)
    msg_matches: dict[str, str] = {}
    msg_coll = get_collection("chat_messages")
    try:
        msg_cursor = (
            msg_coll.find(
                {
                    "thread_id": {"$in": ordered_threads},
                    "content": {"$regex": escaped_regex, "$options": "i"},
                },
                {"_id": 0, "thread_id": 1, "role": 1, "content": 1, "created_at": 1},
            )
            .sort("created_at", -1)
            .limit(safe_limit * 4)
        )
        for msg in msg_cursor:
            tid = str(msg.get("thread_id") or "")
            # Giữ tin nhắn mới nhất đầu tiên khớp từ khóa cho mỗi thread làm đoạn trích snippet
            if tid and tid not in msg_matches:
                prefix = "Bạn: " if msg.get("role") == "user" else "Tit: "
                msg_matches[tid] = prefix + _build_search_snippet(str(msg.get("content") or ""), clean_query)
    except Exception:
        # Fallback quét Python an toàn nếu môi trường test mock không hỗ trợ toán tử $regex của MongoDB
        for msg in msg_coll.find(
            {"thread_id": {"$in": ordered_threads}},
            {"_id": 0, "thread_id": 1, "role": 1, "content": 1, "created_at": 1},
        ):
            tid = str(msg.get("thread_id") or "")
            content_str = str(msg.get("content") or "")
            if tid and tid not in msg_matches and term in content_str.casefold():
                prefix = "Bạn: " if msg.get("role") == "user" else "Tit: "
                msg_matches[tid] = prefix + _build_search_snippet(content_str, clean_query)

    # Bước 6: Hợp nhất kết quả khớp tiêu đề + nội dung, ưu tiên kết quả khớp tiêu đề lên trên (priority=2)
    results: list[tuple[int, float, dict[str, Any]]] = []
    for tid in ordered_threads:
        meta = conv_by_thread[tid]
        title_hit = term in meta["title"].casefold()
        snippet = msg_matches.get(tid, "")
        if not title_hit and not snippet:
            continue
        priority = 2 if title_hit else 1
        match_type = "title_and_message" if (title_hit and snippet) else ("title" if title_hit else "message")
        results.append(
            (
                priority,
                meta["_ts"],
                _format_search_item(meta, snippet=snippet, match_type=match_type),
            )
        )

    # Sắp xếp giảm dần theo độ ưu tiên khớp tiêu đề rồi đến thời gian cập nhật gần nhất
    results.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in results[:safe_limit]]
