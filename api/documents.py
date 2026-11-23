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
)

router = APIRouter()

get_current_user = app_callable('get_current_user', get_current_user)
get_conversation = app_callable('get_conversation', get_conversation)
create_or_update_conversation = app_callable('create_or_update_conversation', create_or_update_conversation)
add_image_to_rag = app_callable('add_image_to_rag', add_image_to_rag)
add_document_to_rag = app_callable('add_document_to_rag', add_document_to_rag)
logger = AppValueProxy("logger", logging.getLogger("app"))

def _looks_like_image(contents: bytes, suffix: str) -> bool:
    if suffix in {".jpg", ".jpeg"}:
        return contents.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return contents.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(contents) >= 12 and contents[:4] == b"RIFF" and contents[8:12] == b"WEBP"
    return False

@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    thread_id: str = Form(...),
    is_temporary: str = Form(default="false"),
):
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

        with open(file_path, "wb") as f:
            f.write(contents)

        user = get_current_user(request)
        user_id = user["id"] if user else None
        is_temp_bool = str(is_temporary).strip().lower() in ("true", "1")
        conv = get_conversation(thread_id)
        if not is_temp_bool and conv and conv.get("is_temporary"):
            is_temp_bool = True
        create_or_update_conversation(thread_id, "Uploaded document", user_id=user_id, is_temporary=is_temp_bool)

        if suffix in SUPPORTED_IMAGE_TYPES:
            # Dùng chung UUID của file lưu trên disk để liên kết metadata Chroma với file gốc.
            result = add_image_to_rag(
                file_path,
                thread_id,
                filename,
                user_id=user_id,
                document_id=file_id,
            )
            return JSONResponse({
                "success": True,
                "message": f"Uploaded image {result['filename']}. You can now ask questions about it.",
            })

        # ID riêng cho lần upload giúp Admin xóa đúng chunks khi các file trùng tên.
        result = add_document_to_rag(
            file_path=file_path,
            thread_id=thread_id,
            user_id=user_id,
            document_id=file_id,
        )

        return JSONResponse({
            "success": True,
            "message": f"Uploaded {result['filename']} and created {result['chunks']} chunks."
        })

    except Exception as e:
        return JSONResponse(
            {
                "success": False,
                "message": str(e)
            },
            status_code=500
        )
