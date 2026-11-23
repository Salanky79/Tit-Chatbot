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
    _admin_api_error,
    _delete_user_avatars,
    _serialize_conversation,
    _serialize_user,
    delete_admin_document,
    delete_user,
    delete_user_checkpoints,
    delete_user_documents,
    get_admin_conversation_messages,
    get_current_user,
    get_user_by_username,
    list_admin_conversations,
    list_admin_documents,
    list_user_thread_ids,
    list_users,
    set_user_active,
)

router = APIRouter()

_admin_api_error = app_callable('_admin_api_error', _admin_api_error)
_delete_user_avatars = app_callable('_delete_user_avatars', _delete_user_avatars)
_serialize_conversation = app_callable('_serialize_conversation', _serialize_conversation)
_serialize_user = app_callable('_serialize_user', _serialize_user)
get_current_user = app_callable('get_current_user', get_current_user)
delete_user_checkpoints = app_callable('delete_user_checkpoints', delete_user_checkpoints)
delete_user_documents = app_callable('delete_user_documents', delete_user_documents)
list_admin_documents = app_callable('list_admin_documents', list_admin_documents)
delete_admin_document = app_callable('delete_admin_document', delete_admin_document)
get_admin_conversation_messages = app_callable('get_admin_conversation_messages', get_admin_conversation_messages)
list_admin_conversations = app_callable('list_admin_conversations', list_admin_conversations)
delete_user = app_callable('delete_user', delete_user)
get_user_by_username = app_callable('get_user_by_username', get_user_by_username)
list_user_thread_ids = app_callable('list_user_thread_ids', list_user_thread_ids)
list_users = app_callable('list_users', list_users)
set_user_active = app_callable('set_user_active', set_user_active)
logger = AppValueProxy("logger", logging.getLogger("app"))

@router.get("/admin/api/users")
async def admin_list_users(
    request: Request,
    search: str = "",
    page: int = 1,
    page_size: int = 25,
):
    """Trả danh sách user đã lọc/phân trang cho bảng quản trị."""
    error = _admin_api_error(request)
    if error:
        return error
    result = list_users(page=page, page_size=page_size, search=search)
    result["items"] = [_serialize_user(user) for user in result["items"]]
    return {"success": True, **result}


@router.patch("/admin/api/users/{user_id}/status")
async def admin_set_user_status(user_id: str, request: Request):
    """Cập nhật trạng thái tài khoản; Admin không thể tự khóa chính mình."""
    error = _admin_api_error(request)
    if error:
        return error
    admin = get_current_user(request)
    try:
        data = await request.json()
        if not isinstance(data.get("is_active"), bool):
            raise ValueError("is_active must be a boolean.")
        if user_id.lower() == str(admin["id"]).lower() and not data["is_active"]:
            raise ValueError("You cannot disable your own account.")
        user = set_user_active(user_id, data["is_active"])
        return {"success": True, "user": _serialize_user(user)}
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@router.delete("/admin/api/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    """Disable and permanently remove a user and their associated data."""
    error = _admin_api_error(request)
    if error:
        return error

    admin = get_current_user(request)
    if user_id.lower() == str(admin["id"]).lower():
        return JSONResponse(
            {"success": False, "error": "Bạn không thể xóa tài khoản Admin đang đăng nhập."},
            status_code=400,
        )

    try:
        # Khóa tài khoản và thu hồi session trước để có thể thử lại nếu dọn dữ liệu lỗi.
        set_user_active(user_id, False)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("Could not disable user %s before deletion", user_id)
        return JSONResponse(
            {"success": False, "error": "Không thể khóa tài khoản trước khi xóa."},
            status_code=500,
        )

    try:
        thread_ids = list_user_thread_ids(user_id)
        checkpoints_deleted = delete_user_checkpoints(thread_ids)
        cleanup = delete_user_documents(user_id)
        _delete_user_avatars(user_id)
        deleted_user = delete_user(user_id)
    except Exception:
        logger.exception("Could not complete deletion for user %s", user_id)
        return JSONResponse(
            {
                "success": False,
                "error": (
                    "Chưa thể hoàn tất xóa tài khoản. Tài khoản đã bị khóa; "
                    "hãy thử lại để tiếp tục dọn dữ liệu."
                ),
            },
            status_code=500,
        )

    cleanup["checkpoints_deleted"] = checkpoints_deleted
    return {
        "success": True,
        "message": f"Đã xóa tài khoản @{deleted_user['username']} và dữ liệu liên quan.",
        "cleanup": cleanup,
    }


@router.get("/admin/api/users/{user_id}/conversations")
async def admin_list_user_conversations(
    user_id: str,
    request: Request,
    search: str = "",
    page: int = 1,
    page_size: int = 20,
):
    """List only the selected user's conversations for the admin viewer."""
    error = _admin_api_error(request)
    if error:
        return error
    result = list_admin_conversations(
        user_id,
        page=page,
        page_size=page_size,
        search=search,
    )
    result["items"] = [_serialize_conversation(item) for item in result["items"]]
    return {"success": True, **result}


@router.get("/admin/api/users/{user_id}/conversations/{thread_id}/messages")
async def admin_get_conversation_messages(
    user_id: str,
    thread_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 50,
):
    """Return one page of messages after verifying conversation ownership."""
    error = _admin_api_error(request)
    if error:
        return error

    result = get_admin_conversation_messages(
        user_id,
        thread_id,
        page=page,
        page_size=page_size,
    )
    if result is None:
        return JSONResponse({"success": False, "error": "Conversation not found."}, status_code=404)

    return {
        "success": True,
        "conversation": _serialize_conversation(result["conversation"]),
        "messages": [
            {
                "role": message.get("role", ""),
                "content": message.get("content", ""),
                "created_at": (
                    message["created_at"].isoformat()
                    if hasattr(message.get("created_at"), "isoformat")
                    else message.get("created_at")
                ),
            }
            for message in result["messages"]
        ],
        "page": result["page"],
        "page_size": result["page_size"],
        "total": result["total"],
    }


@router.get("/admin/api/documents")
async def admin_list_documents(
    request: Request,
    search: str = "",
    user_id: str = "",
    file_type: str = "",
    page: int = 1,
    page_size: int = 25,
):
    """Lọc danh sách tài liệu Chroma và chỉ trả metadata cần hiển thị."""
    error = _admin_api_error(request)
    if error:
        return error

    documents = list_admin_documents()
    clean_search = search.strip().casefold()[:100]
    if clean_search:
        documents = [doc for doc in documents if clean_search in doc["source"].casefold()]
    clean_user = user_id.strip()
    if clean_user:
        # Hỗ trợ lọc theo cả user_id hoặc username để Admin tra cứu linh hoạt
        target_user_ids = {clean_user}
        user_info = get_user_by_username(clean_user)
        if user_info:
            target_user_ids.add(str(user_info["id"]))
        documents = [doc for doc in documents if str(doc.get("user_id") or "") in target_user_ids]
    clean_type = file_type.strip().lower()
    if clean_type:
        documents = [doc for doc in documents if (doc.get("file_type") or "").lower() == clean_type]

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    total = len(documents)
    start = (page - 1) * page_size
    # Không gửi đường dẫn ổ đĩa hay chunk IDs nội bộ về trình duyệt.
    visible_fields = (
        "id", "source", "user_id", "thread_id", "file_type", "uploaded_at", "chunks"
    )
    return {
        "success": True,
        "items": [
            {field: document[field] for field in visible_fields}
            for document in documents[start:start + page_size]
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.delete("/admin/api/documents/{document_key:path}")
async def admin_delete_document(document_key: str, request: Request):
    """Xóa tài liệu khỏi Chroma và file upload nếu hệ thống có đường dẫn an toàn."""
    error = _admin_api_error(request)
    if error:
        return error
    if not delete_admin_document(document_key):
        return JSONResponse({"success": False, "error": "Document not found."}, status_code=404)
    return {"success": True}
