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
    _can_access_conversation,
    delete_conversation,
    get_chat_history,
    get_conversation,
    get_current_user,
    get_user_by_id,
    list_conversations,
    toggle_pin_conversation,
    update_conversation_title,
)
from services.rag import migrate_thread_documents_to_project
from storage.conversations import (
    assign_conversation_to_project as _assign_conversation_to_project_impl,
    search_user_conversations as _search_user_conversations_impl,
)
from storage.projects import get_membership, register_project_file

router = APIRouter()

get_current_user = app_callable('get_current_user', get_current_user)
get_user_by_id = app_callable('get_user_by_id', get_user_by_id)
list_conversations = app_callable('list_conversations', list_conversations)
get_conversation = app_callable('get_conversation', get_conversation)
_can_access_conversation = app_callable('_can_access_conversation', _can_access_conversation)
toggle_pin_conversation = app_callable('toggle_pin_conversation', toggle_pin_conversation)
update_conversation_title = app_callable('update_conversation_title', update_conversation_title)
get_chat_history = app_callable('get_chat_history', get_chat_history)
delete_conversation = app_callable('delete_conversation', delete_conversation)
assign_conversation_to_project = app_callable('assign_conversation_to_project', _assign_conversation_to_project_impl)
search_user_conversations = app_callable('search_user_conversations', _search_user_conversations_impl)
migrate_thread_documents_to_project = app_callable('migrate_thread_documents_to_project', migrate_thread_documents_to_project)
register_project_file = app_callable('register_project_file', register_project_file)
logger = AppValueProxy("logger", logging.getLogger("app"))



# =============================================================================
# 01. Quyền quản lý chat Project
# =============================================================================

def _can_manage_project_conversation(conv: dict | None, user: dict | None) -> bool:
    if not conv or not conv.get("project_id"):
        return True
    if not user:
        return False
    if str(conv.get("user_id", "")) == str(user.get("id", "")):
        return True
    membership = get_membership(str(conv["project_id"]), str(user["id"]))
    return bool(membership and membership.get("role") in {"owner", "editor"})


# =============================================================================
# 02. Liệt kê và tìm kiếm cuộc trò chuyện
# =============================================================================

@router.get("/conversations")
async def conversations(request: Request):
    user = get_current_user(request)
    user_id = user["id"] if user else None
    items = list_conversations(user_id=user_id)

    return {
        "conversations": [
            {
                "thread_id": item.thread_id,
                "title": item.title,
                "created_at": item.created_at.isoformat() if hasattr(item.created_at, "isoformat") else str(item.created_at),
                "updated_at": item.updated_at.isoformat() if hasattr(item.updated_at, "isoformat") else str(item.updated_at),
                "user_id": getattr(item, "user_id", None),
                "is_pinned": bool(getattr(item, "is_pinned", False)),
            }
            for item in items
        ]
    }


@router.get("/conversations/search")
async def search_conversations(request: Request, q: str = "", limit: int = 25):
    """Tìm kiếm các cuộc trò chuyện cá nhân & Project theo tiêu đề và nội dung tin nhắn."""
    user = get_current_user(request)
    user_id = user["id"] if user else None
    results = search_user_conversations(user_id=user_id, query=q, limit=limit)
    return {
        "success": True,
        "query": (q or "").strip(),
        "results": results,
    }



# =============================================================================
# 03. Ghim và đổi tên cuộc trò chuyện
# =============================================================================

@router.post("/conversations/{thread_id}/pin")
async def pin_conversation(thread_id: str, request: Request):
    """Bật/tắt trạng thái ghim (pin/unpin) của một cuộc trò chuyện."""
    user = get_current_user(request)
    conv = get_conversation(thread_id)
    if not conv:
        return JSONResponse(
            {"success": False, "error": "Cuộc trò chuyện không tồn tại."},
            status_code=404
        )

    if not _can_access_conversation(conv, user):
        return JSONResponse(
            {"success": False, "error": "Bạn không có quyền ghim cuộc trò chuyện này."},
            status_code=403
        )

    if not _can_manage_project_conversation(conv, user):
        return JSONResponse({"success": False, "error": "Only the chat creator, project owner, or editor can change this chat."}, status_code=403)

    user_id = user["id"] if user and not conv.get("project_id") else None
    new_pinned = toggle_pin_conversation(thread_id, user_id=user_id)
    if new_pinned is None:
        return JSONResponse(
            {"success": False, "error": "Không thể thay đổi trạng thái ghim."},
            status_code=400
        )

    return {
        "success": True,
        "thread_id": thread_id,
        "is_pinned": new_pinned,
        "message": "Đã ghim cuộc trò chuyện." if new_pinned else "Đã bỏ ghim cuộc trò chuyện."
    }


@router.patch("/conversations/{thread_id}/title")
async def rename_conversation(thread_id: str, request: Request):
    """Cập nhật tiêu đề cuộc trò chuyện (Rename)."""
    user = get_current_user(request)
    conv = get_conversation(thread_id)
    if not conv:
        return JSONResponse(
            {"success": False, "error": "Cuộc trò chuyện không tồn tại."},
            status_code=404
        )

    if not _can_access_conversation(conv, user):
        return JSONResponse(
            {"success": False, "error": "Bạn không có quyền đổi tên cuộc trò chuyện này."},
            status_code=403
        )

    if not _can_manage_project_conversation(conv, user):
        return JSONResponse({"success": False, "error": "Only the chat creator, project owner, or editor can change this chat."}, status_code=403)

    try:
        data = await request.json()
        new_title = data.get("title", "")
        if not isinstance(new_title, str) or not new_title.strip():
            return JSONResponse(
                {"success": False, "error": "Tiêu đề không được để trống."},
                status_code=400
            )
    except Exception:
        return JSONResponse(
            {"success": False, "error": "Dữ liệu JSON không hợp lệ."},
            status_code=400
        )

    user_id = user["id"] if user and not conv.get("project_id") else None
    success = update_conversation_title(thread_id, new_title.strip(), user_id=user_id)
    if not success:
        return JSONResponse(
            {"success": False, "error": "Không thể cập nhật tiêu đề."},
            status_code=400
        )

    return {
        "success": True,
        "thread_id": thread_id,
        "title": new_title.strip()[:100],
        "message": "Đã đổi tên cuộc trò chuyện thành công."
    }



# =============================================================================
# 04. Đọc lịch sử và xóa chat
# =============================================================================

@router.get("/history/{thread_id}")
async def history(thread_id: str, request: Request):
    user = get_current_user(request)
    conv = get_conversation(thread_id)
    if not _can_access_conversation(conv, user):
        return JSONResponse(
            {"error": "Bạn không có quyền truy cập cuộc trò chuyện này."},
            status_code=403
        )

    messages = get_chat_history(thread_id)
    project_chat = bool(conv and conv.get("project_id"))
    sender_ids = {
        str(getattr(msg, "sender_id", None) or getattr(msg, "user_id", None))
        for msg in messages
        if project_chat and msg.role == "user" and (getattr(msg, "sender_id", None) or getattr(msg, "user_id", None))
    }
    sender_names = {}
    for sender_id in sender_ids:
        sender = get_user_by_id(sender_id) or {}
        sender_names[sender_id] = sender.get("full_name") or sender.get("username") or "Project member"

    def serialize_message(msg):
        result = {"role": msg.role, "content": msg.content}
        sender_id = getattr(msg, "sender_id", None) or getattr(msg, "user_id", None)
        if project_chat and msg.role == "user" and sender_id:
            result["sender_name"] = sender_names.get(str(sender_id), "Project member")
        return result

    return {
        "messages": [serialize_message(msg) for msg in messages]
    }


@router.delete("/conversations/{thread_id}")
async def remove_conversation(thread_id: str, request: Request):
    user = get_current_user(request)
    conv = get_conversation(thread_id)
    if not _can_access_conversation(conv, user):
        return JSONResponse(
            {"error": "Bạn không có quyền xóa cuộc trò chuyện này."},
            status_code=403
        )

    if conv and conv.get("project_id"):
        membership = get_membership(str(conv["project_id"]), str(user["id"])) if user else None
        is_creator = user and str(conv.get("user_id", "")) == str(user["id"])
        if not is_creator and not (membership and membership.get("role") in {"owner", "editor"}):
            return JSONResponse({"error": "Only the chat creator, project owner, or editor can delete this chat."}, status_code=403)

    user_id = user["id"] if user and not conv.get("project_id") else None
    success = delete_conversation(thread_id, user_id=user_id)
    if not success:
        return JSONResponse(
            {"error": "Không thể xóa hoặc bạn không có quyền xóa cuộc trò chuyện này."},
            status_code=404
        )
    return {"success": True, "message": "Đã xóa cuộc trò chuyện."}



# =============================================================================
# 05. Chuyển chat cá nhân và tài liệu sang Project
# =============================================================================

@router.post("/conversations/{thread_id}/project")
async def add_conversation_to_project(thread_id: str, request: Request):
    """Đưa một cuộc trò chuyện cá nhân vào Project (chỉ dành cho owner hoặc editor)."""
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            {"success": False, "error": "Vui lòng đăng nhập để thực hiện thao tác này."},
            status_code=401,
        )

    try:
        data = await request.json()
        project_id = str(data.get("project_id") or "").strip()
    except Exception:
        return JSONResponse({"success": False, "error": "Dữ liệu JSON không hợp lệ."}, status_code=400)

    if not project_id:
        return JSONResponse({"success": False, "error": "Thiếu project_id đích."}, status_code=400)

    user_id = str(user["id"])
    try:
        assign_conversation_to_project(thread_id, user_id, project_id)
    except LookupError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=404)
    except PermissionError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    # Đồng bộ tài liệu và ảnh đã upload trong thread cá nhân sang kho dùng chung của Project.
    migrated_docs = migrate_thread_documents_to_project(thread_id, project_id)
    for doc in migrated_docs:
        if doc.get("path"):
            register_project_file(
                project_id=project_id,
                document_id=doc["document_id"],
                thread_id=thread_id,
                filename=doc["filename"],
                path=doc["path"],
                file_type=doc["file_type"],
                uploaded_by=user_id,
            )

    return {
        "success": True,
        "thread_id": thread_id,
        "project_id": project_id,
        "files_migrated": len(migrated_docs),
        "message": "Đã thêm cuộc trò chuyện vào Project.",
    }
