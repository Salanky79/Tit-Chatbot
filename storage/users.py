"""User account persistence operations."""

from __future__ import annotations

from typing import Any

from bson import ObjectId

from storage.mongo_client import get_collection as _mongo_get_collection
from storage._helpers import (
    _conv_to_obj as _conv_to_obj_impl,
    _now as _now_impl,
    _public_user as _public_user_impl,
    _to_object_id as _to_object_id_impl,
    database_callable,
    hash_password as _hash_password_impl,
    verify_password as _verify_password_impl,
)

get_collection = database_callable("get_collection", _mongo_get_collection)
_now = database_callable("_now", _now_impl)
_to_object_id = database_callable("_to_object_id", _to_object_id_impl)
_public_user = database_callable("_public_user", _public_user_impl)
hash_password = database_callable("hash_password", _hash_password_impl)
verify_password = database_callable("verify_password", _verify_password_impl)

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

    ALLOWED_USERNAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    if not username or len(username) < 3:
        raise ValueError("Tên đăng nhập phải có ít nhất 3 ký tự.")
    if not all(char in ALLOWED_USERNAME_CHARS for char in username):
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
        # Không nhận role từ signup request để người dùng không tự cấp quyền Admin.
        "role": "user",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }

    result = users.insert_one(user_doc)
    user_id = str(result.inserted_id)

    return _public_user(user_doc, user_id)


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

    # Tài khoản bị khóa không thể tạo session đăng nhập mới.
    if not user.get("is_active", True):
        return None

    return _public_user(user)


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

    return _public_user(user)


def get_user_by_username(username: str) -> dict | None:
    """Lấy thông tin user theo username."""
    username = (username or "").strip().lower()
    if not username:
        return None
    users = get_collection("users")
    user = users.find_one({"username": username})
    if not user:
        return None

    return _public_user(user)


def list_users(*, page: int = 1, page_size: int = 25, search: str = "") -> dict[str, Any]:
    """Tìm và phân trang danh sách user; giới hạn page size để tránh truy vấn lớn."""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    users = get_collection("users")
    clean_search = (search or "").strip()[:100]
    cursor = users.find({}, {"hashed_password": 0}).sort("created_at", -1)
    all_users = [_public_user(user) for user in cursor]
    if clean_search:
        term = clean_search.casefold()
        all_users = [
            u for u in all_users
            if term in u.get("username", "").casefold()
            or term in u.get("email", "").casefold()
            or term in u.get("full_name", "").casefold()
        ]
    total = len(all_users)
    items = all_users[(page - 1) * page_size : page * page_size]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def set_user_active(user_id: str | ObjectId, is_active: bool) -> dict:
    """Khóa/mở khóa tài khoản và giữ lại ít nhất một Admin đang hoạt động."""
    query_id = _to_object_id(user_id)
    if query_id is None:
        raise ValueError("ID người dùng không hợp lệ.")

    users = get_collection("users")
    user = users.find_one({"_id": query_id})
    if not user:
        raise ValueError("Không tìm thấy người dùng.")

    if not is_active and user.get("role", "user") == "admin" and user.get("is_active", True):
        # Không cho thao tác làm hệ thống mất toàn bộ tài khoản quản trị.
        active_admins = users.count_documents({"role": "admin", "is_active": {"$ne": False}})
        if active_admins <= 1:
            raise ValueError("Không thể khóa Admin cuối cùng đang hoạt động.")

    users.update_one(
        {"_id": query_id},
        {"$set": {"is_active": bool(is_active), "updated_at": _now()}},
    )
    if not is_active:
        # Thu hồi token cũ để chúng không hoạt động lại nếu tài khoản được mở khóa sau này.
        get_collection("sessions").delete_many({
            "user_id": {"$in": [query_id, str(query_id)]}
        })
    updated_user = users.find_one({"_id": query_id})
    return _public_user(updated_user)


def delete_user(user_id: str | ObjectId) -> dict:
    """Xóa user và dữ liệu MongoDB của họ, xóa bản ghi user sau cùng."""
    query_id = _to_object_id(user_id)
    if query_id is None:
        raise ValueError("ID người dùng không hợp lệ.")

    users = get_collection("users")
    user = users.find_one({"_id": query_id})
    if not user:
        raise ValueError("Không tìm thấy người dùng.")

    if user.get("role", "user") == "admin":
        active_admins = users.count_documents(
            {"role": "admin", "is_active": {"$ne": False}}
        )
        minimum_remaining = 1 if user.get("is_active", True) else 0
        if active_admins <= minimum_remaining:
            raise ValueError(
                "Không thể xóa tài khoản khiến hệ thống không còn Admin hoạt động."
            )

    # Tài khoản có thể dùng ObjectId hoặc chuỗi ID trong dữ liệu cũ.
    owner_filter = {"user_id": {"$in": [query_id, str(query_id)]}}
    summary_jobs = get_collection("summary_jobs")
    job_ids = [
        job["_id"]
        for job in summary_jobs.find({"context.user_id": str(query_id)}, {"_id": 1})
    ]
    if job_ids:
        get_collection("summary_job_checkpoints").delete_many({"job_id": {"$in": job_ids}})
    summary_jobs.delete_many({"context.user_id": str(query_id)})

    for collection_name in (
        "sessions",
        "conversations",
        "chat_messages",
        "long_term_memory",
    ):
        get_collection(collection_name).delete_many(owner_filter)

    users.delete_one({"_id": query_id})
    return _public_user(user)


def list_user_thread_ids(user_id: str | ObjectId) -> list[str]:
    """Lấy danh sách thread ID thuộc về một user hợp lệ."""
    query_id = _to_object_id(user_id)
    if query_id is None:
        raise ValueError("ID người dùng không hợp lệ.")

    conversations = get_collection("conversations")
    cursor = conversations.find(
        {"user_id": {"$in": [query_id, str(query_id)]}},
        {"_id": 0, "thread_id": 1},
    )
    return list(dict.fromkeys(
        str(conversation["thread_id"])
        for conversation in cursor
        if conversation.get("thread_id")
    ))


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

    return _public_user(user)
