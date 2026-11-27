"""Session persistence operations."""

from __future__ import annotations

from datetime import timedelta, timezone
import secrets

from bson import ObjectId
from storage.mongo_client import get_collection as _mongo_get_collection
from storage._helpers import _now as _now_impl, _to_object_id as _to_object_id_impl, database_callable

get_collection = database_callable("get_collection", _mongo_get_collection)
_now = database_callable("_now", _now_impl)
_to_object_id = database_callable("_to_object_id", _to_object_id_impl)


# =============================================================================
# 01. Cấp phiên đăng nhập
# =============================================================================

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



# =============================================================================
# 02. Tra cứu và thu hồi phiên
# =============================================================================

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
