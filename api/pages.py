from __future__ import annotations

import config as cfg
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
from api.dependencies import get_current_user

router = APIRouter()
_default_templates = Jinja2Templates(directory=cfg.TEMPLATES_DIR)
templates = AppValueProxy("templates", _default_templates)
get_current_user = app_callable("get_current_user", get_current_user)


# =============================================================================
# 01. Trang chat công khai
# =============================================================================

@router.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )



# =============================================================================
# 02. Trang quản trị có xác thực
# =============================================================================

@router.get("/admin")
async def admin_page(request: Request):
    """Render trang quản trị sau khi xác nhận session và role Admin."""
    user = get_current_user(request)
    if not user or user.get("role", "user") != "admin":
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="admin.html", context={})
