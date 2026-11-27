"""Shared database value conversions and compatibility dispatch."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

import bcrypt
from bson import ObjectId

logger = logging.getLogger("storage.database")



# =============================================================================
# 01. Tương thích facade storage
# =============================================================================

def database_callable(name: str, default: Callable[..., Any]) -> Callable[..., Any]:
    """Resolve legacy facade patches while keeping repository implementations separate."""
    def call(*args: Any, **kwargs: Any) -> Any:
        module = sys.modules.get("storage.database")
        target = getattr(module, name, default) if module is not None else default
        return target(*args, **kwargs)

    return call


# =============================================================================
# 02. Thời gian và ObjectId
# =============================================================================

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



# =============================================================================
# 03. Băm và xác minh mật khẩu
# =============================================================================

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



# =============================================================================
# 04. Chuẩn hóa dữ liệu công khai
# =============================================================================

def _conv_to_obj(doc: dict[str, Any] | None) -> SimpleNamespace | None:
    """Chuyển dict MongoDB thành object-like để tương thích với code cũ dùng
    attribute access (``item.thread_id``, ``item.title``, …).
    Tự động chuyển ObjectId thành string để tránh lỗi serialize JSON.
    """
    if doc is None:
        return None
    converted = {
        key: str(value) if isinstance(value, ObjectId) else value
        for key, value in doc.items()
    }
    return SimpleNamespace(**converted)


VALID_BASE_STYLES = ("default", "professional", "friendly", "efficient", "creative")
MAX_CUSTOM_INSTRUCTIONS_LEN = 1500


def normalize_personalization(raw: Any) -> dict[str, str]:
    """Chuẩn hóa cấu hình cá nhân hóa (base_style, custom_instructions) về giá trị an toàn."""
    data = raw if isinstance(raw, dict) else {}
    style = str(data.get("base_style") or "default").strip().lower()
    if style not in VALID_BASE_STYLES:
        style = "default"
    instructions = str(data.get("custom_instructions") or "").strip()[:MAX_CUSTOM_INSTRUCTIONS_LEN]
    return {
        "base_style": style,
        "custom_instructions": instructions,
    }


def _public_user(
    user: dict[str, Any], user_id: str | ObjectId | None = None
) -> dict[str, Any]:
    """Trả về thông tin công khai, không để lộ credential đã lưu trong MongoDB."""
    username = user["username"]
    return {
        "id": str(user_id if user_id is not None else user["_id"]),
        "username": username,
        "email": user["email"],
        "full_name": user.get("full_name", username),
        "avatar_url": user.get("avatar_url", ""),
        # Mặc định này giữ cho tài khoản tạo trước tính năng Admin tiếp tục hoạt động.
        "role": user.get("role", "user"),
        "is_active": user.get("is_active", True),
        "personalization": normalize_personalization(user.get("personalization")),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }
