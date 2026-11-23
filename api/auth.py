from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from api.compat import AppValueProxy, app_callable, app_value
from api.dependencies import (
    AUTH_CHALLENGE_COOKIE,
    _begin_email_verification,
    _clear_challenge_cookie,
    _masked_email,
    _serialize_user,
    _session_id_from_request,
    _set_challenge_cookie,
    authenticate_user,
    cancel_challenge,
    create_session,
    create_user,
    delete_session,
    get_challenge,
    get_current_user,
    get_session,
    get_user_by_id,
    resend_challenge,
    start_challenge,
    update_user_profile,
    verify_challenge,
    allow_immediate_resend,
    send_verification_code,
)

router = APIRouter()

_begin_email_verification = app_callable('_begin_email_verification', _begin_email_verification)
_clear_challenge_cookie = app_callable('_clear_challenge_cookie', _clear_challenge_cookie)
_serialize_user = app_callable('_serialize_user', _serialize_user)
_session_id_from_request = app_callable('_session_id_from_request', _session_id_from_request)
_set_challenge_cookie = app_callable('_set_challenge_cookie', _set_challenge_cookie)
_masked_email = app_callable('_masked_email', _masked_email)
get_current_user = app_callable('get_current_user', get_current_user)
create_user = app_callable('create_user', create_user)
authenticate_user = app_callable('authenticate_user', authenticate_user)
get_user_by_id = app_callable('get_user_by_id', get_user_by_id)
create_session = app_callable('create_session', create_session)
delete_session = app_callable('delete_session', delete_session)
get_session = app_callable('get_session', get_session)
update_user_profile = app_callable('update_user_profile', update_user_profile)
start_challenge = app_callable('start_challenge', start_challenge)
get_challenge = app_callable('get_challenge', get_challenge)
verify_challenge = app_callable('verify_challenge', verify_challenge)
resend_challenge = app_callable('resend_challenge', resend_challenge)
cancel_challenge = app_callable('cancel_challenge', cancel_challenge)
allow_immediate_resend = app_callable('allow_immediate_resend', allow_immediate_resend)
send_verification_code = app_callable('send_verification_code', send_verification_code)
logger = AppValueProxy("logger", logging.getLogger("app"))

@router.post("/auth/signup")
async def auth_signup(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body."}, status_code=400)
    try:
        user = create_user(
            username=data.get("username", ""), email=data.get("email", ""),
            password=data.get("password", ""), full_name=data.get("full_name", ""),
        )
        return _begin_email_verification(user, request, "Tài khoản đã tạo. Hãy xác minh email để đăng nhập.")
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("Account registration failed")
        return JSONResponse({"success": False, "error": f"System error: {exc}"}, status_code=500)


@router.post("/auth/signin")
async def auth_signin(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body."}, status_code=400)
    identifier = (
        data.get("identifier", "") or data.get("username", "")
        or data.get("email", "") or data.get("username_or_email", "")
    )
    user = authenticate_user(identifier, data.get("password", ""))
    if not user:
        return JSONResponse({
            "success": False,
            "error": "Tên đăng nhập/email hoặc mật khẩu không chính xác.",
        }, status_code=401)
    return _begin_email_verification(user, request, "Mã xác minh đã được gửi đến email của bạn.")


@router.get("/auth/challenge")
async def auth_challenge_status(request: Request):
    challenge = get_challenge(request.cookies.get(AUTH_CHALLENGE_COOKIE))
    return {"active": bool(challenge), "challenge": challenge}


@router.post("/auth/verify")
async def auth_verify(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body."}, status_code=400)
    result, user_id = verify_challenge(
        request.cookies.get(AUTH_CHALLENGE_COOKIE), data.get("code", "")
    )
    if result != "valid":
        messages = {
            "invalid": "Mã xác minh không chính xác.",
            "expired": "Mã xác minh đã hết hạn. Hãy gửi mã mới.",
            "locked": "Bạn đã nhập sai quá nhiều lần. Hãy gửi mã mới.",
            "used": "Mã xác minh này đã được sử dụng.",
            "missing": "Không tìm thấy yêu cầu xác minh. Hãy đăng nhập lại.",
        }
        return JSONResponse({
            "success": False, "error": messages[result], "reason": result,
        }, status_code=400)
    user = get_user_by_id(user_id)
    if not user or not user.get("is_active", True):
        response = JSONResponse({"success": False, "error": "Account is no longer active."}, status_code=401)
        _clear_challenge_cookie(response)
        return response
    session_id = create_session(user["id"])
    response = JSONResponse({
        "success": True, "message": "Verification successful.",
        "user": _serialize_user(user), "token": session_id,
    })
    response.set_cookie(
        key="tit_session", value=session_id, httponly=True,
        secure=request.url.scheme == "https", samesite="lax",
        max_age=7 * 24 * 3600, path="/",
    )
    _clear_challenge_cookie(response)
    return response


@router.post("/auth/resend")
async def auth_resend(request: Request):
    secret = request.cookies.get(AUTH_CHALLENGE_COOKIE)
    result, email, code = resend_challenge(secret)
    if result == "sent":
        try:
            send_verification_code(email, code)
        except Exception:
            logger.exception("Could not resend account verification email")
            allow_immediate_resend(secret)
            return JSONResponse({"success": False, "error": "Không thể gửi email. Vui lòng thử lại."}, status_code=503)
        return {"success": True, "message": "Mã xác minh mới đã được gửi."}
    messages = {
        "missing": "Yêu cầu xác minh không còn hiệu lực. Hãy đăng nhập lại.",
        "limit": "Bạn đã đạt giới hạn gửi lại mã. Hãy đăng nhập lại để bắt đầu yêu cầu mới.",
        "cooldown": "Vui lòng đợi trước khi gửi mã mới.",
    }
    status = 429 if result in {"limit", "cooldown"} else 400
    return JSONResponse({"success": False, "error": messages[result]}, status_code=status)


@router.post("/auth/cancel-challenge")
async def auth_cancel_challenge(request: Request):
    cancel_challenge(request.cookies.get(AUTH_CHALLENGE_COOKIE))
    response = JSONResponse({"success": True})
    _clear_challenge_cookie(response)
    return response


@router.post("/auth/signout")
async def auth_signout(request: Request):
    session_id = _session_id_from_request(request)
    if session_id:
        delete_session(session_id)
    cancel_challenge(request.cookies.get(AUTH_CHALLENGE_COOKIE))
    response = JSONResponse({"success": True, "message": "Đã đăng xuất thành công."})
    response.delete_cookie(key="tit_session", path="/")
    _clear_challenge_cookie(response)
    return response


@router.get("/auth/me")
async def auth_me(request: Request):
    user = get_current_user(request)
    return {"authenticated": bool(user), "user": _serialize_user(user)}


@router.post("/auth/profile")
async def auth_update_profile(request: Request):
    """Cập nhật thông tin cá nhân (họ tên, email, mật khẩu)."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Bạn cần đăng nhập để thực hiện tính năng này."}, status_code=401)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Dữ liệu JSON không hợp lệ."}, status_code=400)

    full_name = data.get("full_name")
    email = data.get("email")
    current_password = data.get("current_password")
    new_password = data.get("new_password")

    try:
        updated_user = update_user_profile(
            user["id"],
            full_name=full_name,
            email=email,
            current_password=current_password,
            new_password=new_password,
        )
        return JSONResponse({
            "success": True,
            "message": "Cập nhật thông tin cá nhân thành công!",
            "user": _serialize_user(updated_user),
        })
    except ValueError as ve:
        return JSONResponse({"success": False, "error": str(ve)}, status_code=400)
    except Exception as exc:
        logger.exception("Lỗi cập nhật thông tin cá nhân:")
        return JSONResponse({"success": False, "error": f"Lỗi hệ thống: {str(exc)}"}, status_code=500)


@router.post("/auth/avatar")
async def auth_avatar(request: Request, file: UploadFile = File(...)):
    """Upload và cập nhật ảnh đại diện người dùng."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Bạn cần đăng nhập để thực hiện tính năng này."}, status_code=401)

    allowed_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_exts:
        return JSONResponse({
            "success": False,
            "error": "Định dạng file không hỗ trợ. Vui lòng tải file ảnh (PNG, JPG, WEBP, GIF)."
        }, status_code=400)

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        return JSONResponse({"success": False, "error": "Kích thước ảnh không được vượt quá 5MB."}, status_code=400)

    avatar_dir = Path("uploads/avatars")
    avatar_dir.mkdir(exist_ok=True, parents=True)
    safe_filename = f"{user['id']}_{uuid.uuid4().hex[:8]}{ext}"
    target_path = avatar_dir / safe_filename
    with open(target_path, "wb") as f:
        f.write(content)

    avatar_url = f"/uploads/avatars/{safe_filename}"
    updated_user = update_user_profile(user["id"], avatar_url=avatar_url)

    return JSONResponse({
        "success": True,
        "message": "Cập nhật ảnh đại diện thành công!",
        "avatar_url": avatar_url,
        "user": _serialize_user(updated_user),
    })


@router.get("/uploads/avatars/{filename}")
async def get_avatar_file(filename: str):
    """Trả về file ảnh đại diện."""
    safe_name = Path(filename).name
    file_path = Path("uploads/avatars") / safe_name
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse({"error": "Không tìm thấy ảnh đại diện."}, status_code=404)
    suffix = file_path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_types.get(suffix, "application/octet-stream")
    return FileResponse(file_path, media_type=media_type)
