"""Project management API with per-request membership authorization."""

from __future__ import annotations

import config as cfg
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from api.compat import app_callable
from api.dependencies import get_current_user
from services.email_service import send_project_invitation
from services.project_events import project_event_stream
from storage.projects import (
    can_edit_project,
    create_project,
    delete_project,
    delete_project_file_record,
    get_membership,
    get_project,
    get_project_file,
    invite_project_member,
    list_pending_invitations,
    list_project_conversations,
    list_project_files,
    list_project_members,
    list_projects_for_user,
    preview_project_invitation,
    remove_project_member,
    respond_to_invitation,
    respond_to_invitation_token,
    update_member_role,
    update_project,
)

router = APIRouter()
get_current_user = app_callable("get_current_user", get_current_user)
logger = logging.getLogger(__name__)



# =============================================================================
# 01. URL mời, response lỗi và kiểm tra quyền
# =============================================================================

def _invitation_url(request: Request, token: str) -> str:
    base = (os.getenv("PUBLIC_APP_URL") or str(request.base_url)).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("PUBLIC_APP_URL must be an absolute HTTP(S) application URL without query or fragment.")
    # Trả về URL mời tham gia Project, ví dụ: https://app.example.com/#project-invite=TOKEN (sau dấu # là fragment, không gửi về server)
    return f"{base}/#project-invite={token}"


def _error(message: str, status: int) -> JSONResponse:
    """Tạo response lỗi chuẩn cho API Projects."""
    return JSONResponse({"success": False, "error": message}, status_code=status)


def _user(request: Request) -> dict | JSONResponse:
    """Lấy user hiện tại hoặc trả lỗi nếu chưa đăng nhập."""
    user = get_current_user(request)
    return user if user else _error("Sign-in required.", 401)


def _membership(project_id: str, user: dict) -> dict | JSONResponse:
    """Xác nhận user có membership được chấp nhận trong Project."""
    membership = get_membership(project_id, str(user["id"]))
    return membership if membership else _error("Project not found or access denied.", 404)



# =============================================================================
# 02. Danh sách và tạo Project
# =============================================================================

@router.get("/projects")
async def projects(request: Request):
    """Trả danh sách Project của user hiện tại."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    return {"success": True, "projects": list_projects_for_user(str(user["id"]))}


@router.post("/projects")
async def create_project_route(request: Request):
    """Tạo Project mới cho user hiện tại."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    try:
        body = await request.json()
        project = create_project(str(user["id"]), body.get("name", ""), body.get("description", ""))
        return JSONResponse({"success": True, "project": project}, status_code=201)
    except ValueError as exc:
        return _error(str(exc), 400)



# =============================================================================
# 03. Lời mời, SSE và phản hồi của người nhận
# =============================================================================

@router.get("/projects/invitations")
async def invitations(request: Request):
    """Trả các lời mời Project đang chờ của user."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    return {"success": True, "invitations": list_pending_invitations(str(user["id"]))}


@router.get("/projects/events")
async def project_events(request: Request):
    """Mở kết nối SSE đẩy thông báo thay đổi Project/quyền về browser theo thời gian thực."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    # Tắt cache & X-Accel-Buffering để Nginx/proxy đẩy từng event ngay, không giữ buffer
    
    return StreamingResponse(project_event_stream(request, str(user["id"]), get_current_user),
                             media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})


@router.post("/projects/invitations/preview")
async def preview_invitation(request: Request):
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    try:
        body = await request.json()
    except ValueError:
        return _error("Invalid JSON body.", 400)
    if not isinstance(body, dict):
        return _error("Invalid JSON body.", 400)
    try:
        invitation = await run_in_threadpool(preview_project_invitation, body.get("token", ""), str(user["id"]))
    except ValueError as exc:
        return _error(str(exc), 410)
    if not invitation:
        return _error("Invitation not found, revoked, already handled, or belongs to another account.", 404)
    return {"success": True, "invitation": invitation}


@router.post("/projects/invitations/respond")
async def respond_invitation_link(request: Request):
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    try:
        body = await request.json()
    except ValueError:
        return _error("Invalid JSON body.", 400)
    if not isinstance(body, dict) or type(body.get("accept")) is not bool:
        return _error("A boolean accept value is required.", 400)
    try:
        project_id = await run_in_threadpool(respond_to_invitation_token, body.get("token", ""), str(user["id"]), body["accept"])
    except ValueError as exc:
        return _error(str(exc), 410)
    if not project_id:
        return _error("Invitation not found, revoked, already handled, or belongs to another account.", 404)
    return {"success": True, "project_id": project_id}


@router.post("/projects/invitations/{invitation_id}/accept")
async def accept_invitation(invitation_id: str, request: Request):
    """Chấp nhận lời mời tham gia Project."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    if not respond_to_invitation(invitation_id, str(user["id"]), True):
        return _error("Invitation not found, expired, or already handled.", 404)
    return {"success": True}


@router.post("/projects/invitations/{invitation_id}/decline")
async def decline_invitation(invitation_id: str, request: Request):
    """Từ chối lời mời tham gia Project."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    if not respond_to_invitation(invitation_id, str(user["id"]), False):
        return _error("Invitation not found, expired, or already handled.", 404)
    return {"success": True}



# =============================================================================
# 04. Đọc, sửa và xóa Project
# =============================================================================

@router.get("/projects/{project_id}")
async def project_detail(project_id: str, request: Request):
    """Trả thông tin Project cùng vai trò của user hiện tại."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    membership = _membership(project_id, user)
    if isinstance(membership, JSONResponse):
        return membership
    return {"success": True, "project": get_project(project_id), "membership": membership}


@router.patch("/projects/{project_id}")
async def patch_project(project_id: str, request: Request):
    """Cập nhật cài đặt Project nếu user là owner hoặc editor."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    if not can_edit_project(project_id, str(user["id"])):
        return _error("Project edit permission required.", 403)
    try:
        body = await request.json()
        project = update_project(project_id, str(user["id"]), body)
        return {"success": True, "project": project}
    except ValueError as exc:
        return _error(str(exc), 400)


@router.delete("/projects/{project_id}")
async def remove_project(project_id: str, request: Request):
    """Xóa Project và dữ liệu liên quan theo yêu cầu của owner."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    project = get_project(project_id)
    if not project or str(project.get("owner_id")) != str(user["id"]):
        return _error("Only the owner can delete this project.", 403)
    result = delete_project(project_id, str(user["id"]))
    if result is None:
        return _error("Project not found.", 404)
    return {"success": True, "cleanup": result}



# =============================================================================
# 05. Danh sách chat và quản lý thành viên
# =============================================================================

@router.get("/projects/{project_id}/conversations")
async def project_conversations(project_id: str, request: Request):
    """Trả danh sách chat thuộc Project cho thành viên."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    membership = _membership(project_id, user)
    if isinstance(membership, JSONResponse):
        return membership
    return {"success": True, "conversations": list_project_conversations(project_id)}


@router.get("/projects/{project_id}/members")
async def project_members(project_id: str, request: Request):
    """Trả danh sách thành viên Project cho user có quyền truy cập."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    membership = _membership(project_id, user)
    if isinstance(membership, JSONResponse):
        return membership
    return {"success": True, "members": list_project_members(project_id)}


@router.post("/projects/{project_id}/members")
async def invite_member(project_id: str, request: Request):
    """Mời tài khoản đã đăng ký vào Project với một vai trò."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return _error("Invalid JSON body.", 400)
        # Validate URL configuration before persisting or rotating an invitation.
        _invitation_url(request, "")
        member = await run_in_threadpool(
            invite_project_member, project_id, str(user["id"]), body.get("identifier", ""),
            body.get("role", cfg.DEFAULT_INVITE_ROLE), body.get("expiry_days", cfg.DEFAULT_INVITATION_DAYS)
        )
        token = member.pop("invitation_token")
        invitation_url = _invitation_url(request, token)
        project_name = member.pop("project_name")
        email_sent = False
        try:
            await run_in_threadpool(send_project_invitation, member["email"], project_name,
                                    user.get("full_name") or user.get("username") or "Chủ sở hữu",
                                    member["role"], invitation_url, member["expires_at"])
            email_sent = True
        except Exception:
            logger.warning("Could not deliver project invitation email", exc_info=True)
        return JSONResponse({"success": True, "invitation": member, "invitation_url": invitation_url,
                             "email_sent": email_sent, "email_error": None if email_sent else "Email delivery failed. The in-app invitation and link are still available."}, status_code=201)
    except PermissionError as exc:
        return _error(str(exc), 403)
    except ValueError as exc:
        return _error(str(exc), 400)


@router.patch("/projects/{project_id}/members/{member_id}")
async def patch_member(project_id: str, member_id: str, request: Request):
    """Đổi vai trò thành viên theo yêu cầu của Project owner."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    try:
        body = await request.json()
        success = update_member_role(project_id, str(user["id"]), member_id, body.get("role", ""))
    except Exception:
        success = False
    return {"success": success} if success else _error("Member or project not found, or only the owner may change roles.", 403)


@router.delete("/projects/{project_id}/members/{member_id}")
async def delete_member(project_id: str, member_id: str, request: Request):
    """Thu hồi membership của user khỏi Project."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    if not remove_project_member(project_id, str(user["id"]), member_id):
        return _error("Member not found, or only the owner may remove members.", 403)
    return {"success": True}



# =============================================================================
# 06. Liệt kê, tải xuống và xóa tài liệu Project
# =============================================================================

@router.get("/projects/{project_id}/files")
async def project_files(project_id: str, request: Request):
    """Liệt kê tên và metadata công khai của file Project."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    membership = _membership(project_id, user)
    if isinstance(membership, JSONResponse):
        return membership
    files = list_project_files(project_id)
    visible = [
        {key: item.get(key) for key in ("document_id", "filename", "file_type", "uploaded_by", "created_at")}
        for item in files
    ]
    return {"success": True, "files": visible}


@router.get("/projects/{project_id}/files/{document_id}/download")
async def download_project_file(project_id: str, document_id: str, request: Request):
    """Cho thành viên Project tải file đã đăng ký."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    membership = _membership(project_id, user)
    if isinstance(membership, JSONResponse):
        return membership
    file_doc = get_project_file(project_id, document_id)
    if not file_doc:
        return _error("File not found.", 404)
    upload_root = Path(cfg.UPLOAD_DIR).resolve()
    candidate = Path(file_doc.get("path", "")).resolve()
    if not candidate.is_relative_to(upload_root) or not candidate.is_file():
        return _error("File not found.", 404)
    return FileResponse(candidate, filename=file_doc.get("filename", candidate.name))


@router.delete("/projects/{project_id}/files/{document_id}")
async def delete_project_file(project_id: str, document_id: str, request: Request):
    """Xóa file Project và vector chunks tương ứng."""
    user = _user(request)
    if isinstance(user, JSONResponse):
        return user
    if not can_edit_project(project_id, str(user["id"])):
        return _error("Project edit permission required.", 403)
    file_doc = get_project_file(project_id, document_id)
    if not file_doc:
        return _error("File not found.", 404)
    from services.rag import delete_project_document
    try:
        delete_project_document(project_id, document_id)
    except (OSError, RuntimeError) as exc:
        return _error(f"Could not delete the file from disk: {exc}", 500)
    upload_root = Path(cfg.UPLOAD_DIR).resolve()
    candidate = Path(file_doc.get("path", "")).resolve()
    if candidate.is_relative_to(upload_root) and candidate.is_file():
        try:
            candidate.unlink()
        except OSError as exc:
            return _error(f"Could not delete the file from disk: {exc}", 500)
    delete_project_file_record(project_id, document_id)
    return {"success": True}
