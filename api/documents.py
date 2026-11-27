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
    add_document_to_rag,
    add_image_to_rag,
    create_or_update_conversation,
    get_conversation,
    get_current_user,
    MAX_IMAGE_BYTES,
    SUPPORTED_IMAGE_TYPES,
    _can_access_conversation,
)
from storage.projects import can_edit_project, get_membership, get_project, register_project_file

router = APIRouter()

get_current_user = app_callable('get_current_user', get_current_user)
get_conversation = app_callable('get_conversation', get_conversation)
create_or_update_conversation = app_callable('create_or_update_conversation', create_or_update_conversation)
add_image_to_rag = app_callable('add_image_to_rag', add_image_to_rag)
add_document_to_rag = app_callable('add_document_to_rag', add_document_to_rag)
logger = AppValueProxy("logger", logging.getLogger("app"))


# =============================================================================
# 01. Nhận diện định dạng upload
# =============================================================================

def _looks_like_image(contents: bytes, suffix: str) -> bool:
    if suffix in {".jpg", ".jpeg"}:
        return contents.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return contents.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(contents) >= 12 and contents[:4] == b"RIFF" and contents[8:12] == b"WEBP"
    return False


# =============================================================================
# 02. Upload: kiểm tra quyền, ghi file và lập chỉ mục
# =============================================================================

@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    thread_id: str = Form(...),
    is_temporary: str = Form(default="false"),
    project_id: str = Form(default=""),
):
    """Upload file cá nhân hoặc file dùng chung trong Project."""
    file_path: str | None = None
    written_to_disk = False
    try:
        allowed_extensions = {".pdf", ".docx", ".txt", ".md", ".py", ".csv"}

        filename = Path(file.filename or "uploaded_file").name
        suffix = Path(filename).suffix.lower()

        if suffix not in allowed_extensions and suffix not in SUPPORTED_IMAGE_TYPES:
            return JSONResponse(
                {
                    "success": False,
                    "message": "Unsupported file type. Upload PDF, DOCX, TXT, MD, PY, CSV, JPG, PNG, or WEBP."
                },
                status_code=400
            )

        file_id = str(uuid.uuid4())
        safe_filename = filename.replace(" ", "_")
        file_path = f"uploads/{file_id}_{safe_filename}"
        contents = await file.read()

        if suffix in SUPPORTED_IMAGE_TYPES:
            if len(contents) > MAX_IMAGE_BYTES:
                return JSONResponse(
                    {"success": False, "message": "Image is too large (maximum 14 MB)."},
                    status_code=413,
                )
            if not _looks_like_image(contents, suffix):
                return JSONResponse(
                    {"success": False, "message": "The uploaded file is not a valid JPG, PNG, or WEBP image."},
                    status_code=400,
                )

        user = get_current_user(request)
        user_id = user["id"] if user else None
        is_temp_bool = str(is_temporary).strip().lower() in ("true", "1")
        conv = get_conversation(thread_id)
        project_id = (project_id or "").strip()
        if conv and not _can_access_conversation(conv, user):
            return JSONResponse({"success": False, "message": "You cannot access this conversation."}, status_code=403)
        if conv and conv.get("project_id"):
            existing_project = str(conv.get("project_id"))
            if project_id and project_id != existing_project:
                return JSONResponse({"success": False, "message": "Conversation belongs to another project."}, status_code=403)
            project_id = existing_project
        if project_id:
            if not user or not get_project(project_id) or not get_membership(project_id, user_id):
                return JSONResponse({"success": False, "message": "Project not found or access denied."}, status_code=404)
            if not can_edit_project(project_id, user_id):
                return JSONResponse({"success": False, "message": "Project edit permission required to upload files."}, status_code=403)
            if conv and str(conv.get("project_id") or "") != project_id:
                return JSONResponse({"success": False, "message": "Conversation is not part of this project."}, status_code=403)
            if is_temp_bool:
                return JSONResponse({"success": False, "message": "Temporary chats cannot upload project files."}, status_code=400)
        if not is_temp_bool and conv and conv.get("is_temporary"):
            is_temp_bool = True
        with open(file_path, "wb") as f:
            f.write(contents)
        written_to_disk = True

        if suffix in SUPPORTED_IMAGE_TYPES:
            # Dùng chung UUID của file lưu trên disk để liên kết metadata Chroma với file gốc.
            result = add_image_to_rag(
                file_path,
                thread_id,
                filename,
                user_id=user_id,
                document_id=file_id,
                project_id=project_id or None,
            )
            create_or_update_conversation(
                thread_id, "Uploaded document", user_id=user_id,
                is_temporary=is_temp_bool, project_id=project_id or None,
            )
            if project_id:
                register_project_file(project_id, file_id, thread_id, filename, file_path, "image", user_id)
            return JSONResponse({
                "success": True,
                "source": result["filename"],
                "message": f"Uploaded image {result['filename']}. You can now ask questions about it.",
            })

        # ID riêng cho lần upload giúp Admin xóa đúng chunks khi các file trùng tên.
        result = add_document_to_rag(
            file_path=file_path,
            thread_id=thread_id,
            user_id=user_id,
            document_id=file_id,
            project_id=project_id or None,
        )
        create_or_update_conversation(
            thread_id, "Uploaded document", user_id=user_id,
            is_temporary=is_temp_bool, project_id=project_id or None,
        )
        if project_id:
            register_project_file(project_id, file_id, thread_id, filename, file_path, suffix.lstrip("."), user_id)

        return JSONResponse({
            "success": True,
            "source": result["filename"],
            "message": f"Uploaded {result['filename']} and created {result['chunks']} chunks."
        })

    except Exception as e:
        if written_to_disk and file_path:
            try:
                Path(file_path).unlink(missing_ok=True)
            except OSError:
                pass
        return JSONResponse(
            {
                "success": False,
                "message": str(e)
            },
            status_code=500
        )
