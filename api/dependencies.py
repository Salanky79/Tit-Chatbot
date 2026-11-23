"""Shared API helpers and compatibility exports."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from api.compat import AppValueProxy, app_callable
from agent import delete_user_checkpoints, get_agent, normalize_model_name
from services.followups import FOLLOWUPS_ENABLED, generate_followup_questions
from services.image_analysis import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_TYPES
from services.language import is_vietnamese, resolve_request_language, set_current_language
from services.token_limit_handler import (
    handle_partial_stream,
    is_daily_token_limit,
    is_rate_limit,
)
from services.authentication import (
    allow_immediate_resend,
    cancel_challenge,
    get_challenge,
    resend_challenge,
    start_challenge,
    verify_challenge,
)
from services.email_service import send_verification_code
from storage.database import (
    authenticate_user,
    create_or_update_conversation,
    create_session,
    create_user,
    delete_conversation,
    delete_session,
    delete_user,
    get_admin_conversation_messages,
    get_chat_history,
    get_conversation,
    get_session,
    get_user_by_id,
    get_user_by_username,
    list_admin_conversations,
    list_conversations,
    list_user_thread_ids,
    list_users,
    save_chat_message,
    set_user_active,
    toggle_pin_conversation,
    update_conversation_title,
    update_user_profile,
)
from services.rag import (
    add_document_to_rag,
    add_image_to_rag,
    delete_admin_document,
    delete_user_documents,
    list_admin_documents,
)
from tools import set_current_model_name, set_current_thread_id, set_current_user_id

logger = AppValueProxy("logger", logging.getLogger("app"))
get_session = app_callable("get_session", get_session)
get_user_by_id = app_callable("get_user_by_id", get_user_by_id)
start_challenge = app_callable("start_challenge", start_challenge)
allow_immediate_resend = app_callable("allow_immediate_resend", allow_immediate_resend)
send_verification_code = app_callable("send_verification_code", send_verification_code)

AUTH_CHALLENGE_COOKIE = "tit_auth_challenge"

def _serialize_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    """Chuyển đổi các trường datetime của user sang định dạng ISO string để serialize JSON an toàn."""
    if not user:
        return None
    d = dict(user)
    if hasattr(d.get("created_at"), "isoformat"):
        d["created_at"] = d["created_at"].isoformat()
    if hasattr(d.get("updated_at"), "isoformat"):
        d["updated_at"] = d["updated_at"].isoformat()
    return d


def _serialize_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    """Serialize conversation timestamps for JSON responses."""
    result = dict(conversation)
    for field in ("created_at", "updated_at"):
        value = result.get(field)
        if hasattr(value, "isoformat"):
            result[field] = value.isoformat()
    return result


def _delete_user_avatars(user_id: str) -> None:
    """Remove locally stored avatar files whose names are owned by this user."""
    avatar_root = Path("uploads/avatars").resolve()
    for avatar_path in avatar_root.glob(f"{user_id}_*"):
        candidate = avatar_path.resolve()
        if candidate.is_relative_to(avatar_root) and candidate.is_file():
            try:
                candidate.unlink()
            except OSError as exc:
                logger.warning("Could not remove avatar file %s: %s", candidate, exc)


def _session_id_from_request(
    request: Request, *, include_query: bool = False
) -> str | None:
    """Read the session token from the request's supported authentication sources."""
    session_id = request.cookies.get("tit_session")
    if not session_id:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_id = auth_header[7:].strip()
    if not session_id and include_query:
        session_id = request.query_params.get("session_id")
    return session_id or None


def _conversation_owner_id(conversation: Any) -> str | None:
    """Return a conversation's owner ID when the conversation has one."""
    if not conversation:
        return None
    owner_id = (
        conversation.get("user_id")
        if isinstance(conversation, dict)
        else getattr(conversation, "user_id", None)
    )
    return str(owner_id) if owner_id else None


def _can_access_conversation(conversation: Any, user: dict | None) -> bool:
    """Allow unowned conversations and require matching users for owned ones."""
    owner_id = _conversation_owner_id(conversation)
    return owner_id is None or bool(user and str(user["id"]) == owner_id)


def get_current_user(request: Request) -> dict | None:
    """Extract the current user from a cookie, bearer token, or query parameter."""
    session_id = _session_id_from_request(request, include_query=True)
    if not session_id:
        return None

    session = get_session(session_id)
    if not session:
        return None

    user = get_user_by_id(session.get("user_id", ""))
    # Kiểm tra trạng thái ở mỗi request để session đã cấp trước đó cũng bị vô hiệu khi khóa user.
    if user and not user.get("is_active", True):
        return None
    return user


def _admin_api_error(request: Request) -> JSONResponse | None:
    """Chặn API Admin ở backend; ẩn nút trên UI không thay thế được bước kiểm tra này."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Sign-in required."}, status_code=401)
    if user.get("role", "user") != "admin":
        return JSONResponse({"success": False, "error": "Admin access required."}, status_code=403)
    return None


def _set_challenge_cookie(response: JSONResponse, secret: str, request: Request) -> None:
    response.set_cookie(
        key=AUTH_CHALLENGE_COOKIE, value=secret, httponly=True,
        secure=request.url.scheme == "https", samesite="lax",
        max_age=15 * 60, path="/auth",
    )


def _clear_challenge_cookie(response: JSONResponse) -> None:
    response.delete_cookie(key=AUTH_CHALLENGE_COOKIE, path="/auth")


def _masked_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'*' * max(2, min(len(local) - 1, 6))}@{domain}"


def _begin_email_verification(user: dict, request: Request, message: str) -> JSONResponse:
    secret, code = start_challenge(user)
    try:
        send_verification_code(user["email"], code)
        response = JSONResponse({
            "success": True, "requires_verification": True,
            "message": message, "email": _masked_email(user["email"]),
        }, status_code=202)
    except Exception:
        logger.exception("Could not send account verification email")
        allow_immediate_resend(secret)
        response = JSONResponse({
            "success": False, "requires_verification": True,
            "error": "Không thể gửi email xác minh lúc này. Vui lòng thử gửi lại mã.",
            "email": _masked_email(user["email"]),
        }, status_code=503)
    _set_challenge_cookie(response, secret, request)
    return response

