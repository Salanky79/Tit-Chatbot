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
    list_conversations,
    toggle_pin_conversation,
    update_conversation_title,
)

router = APIRouter()

get_current_user = app_callable('get_current_user', get_current_user)
list_conversations = app_callable('list_conversations', list_conversations)
get_conversation = app_callable('get_conversation', get_conversation)
_can_access_conversation = app_callable('_can_access_conversation', _can_access_conversation)
toggle_pin_conversation = app_callable('toggle_pin_conversation', toggle_pin_conversation)
update_conversation_title = app_callable('update_conversation_title', update_conversation_title)
get_chat_history = app_callable('get_chat_history', get_chat_history)
delete_conversation = app_callable('delete_conversation', delete_conversation)
logger = AppValueProxy("logger", logging.getLogger("app"))

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

    user_id = user["id"] if user else None
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

    user_id = user["id"] if user else None
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

    return {
        "messages": [
            {
                "role": msg.role,
                "content": msg.content
            }
            for msg in messages
        ]
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

    user_id = user["id"] if user else None
    success = delete_conversation(thread_id, user_id=user_id)
    if not success:
        return JSONResponse(
            {"error": "Không thể xóa hoặc bạn không có quyền xóa cuộc trò chuyện này."},
            status_code=404
        )
    return {"success": True, "message": "Đã xóa cuộc trò chuyện."}
