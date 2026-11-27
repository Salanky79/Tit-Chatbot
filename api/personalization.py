"""API quản lý cài đặt Personalization (Style, Custom Instructions) và Memory Summary."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.compat import AppValueProxy, app_callable
from api.dependencies import _serialize_user, get_current_user
from services.personalization_memory import (
    apply_llm_memory_instruction as _apply_llm_memory_instruction_impl,
    summarize_user_memories as _summarize_user_memories_impl,
)
from storage.memories import (
    delete_all_personal_memories as _delete_all_personal_memories_impl,
    delete_user_memory_by_id_for_settings as _delete_user_memory_impl,
    get_user_memories as _get_user_memories_impl,
)
from storage.users import update_user_personalization as _update_user_personalization_impl

router = APIRouter()

logger = AppValueProxy("logger", logging.getLogger("app"))
get_current_user = app_callable("get_current_user", get_current_user)
get_user_memories = app_callable("get_user_memories", _get_user_memories_impl)
update_user_personalization = app_callable("update_user_personalization", _update_user_personalization_impl)
summarize_user_memories = app_callable("summarize_user_memories", _summarize_user_memories_impl)
apply_llm_memory_instruction = app_callable("apply_llm_memory_instruction", _apply_llm_memory_instruction_impl)
delete_user_memory_by_id_for_settings = app_callable("delete_user_memory_by_id_for_settings", _delete_user_memory_impl)
delete_all_personal_memories = app_callable("delete_all_personal_memories", _delete_all_personal_memories_impl)



# =============================================================================
# 01. Xác thực người dùng
# =============================================================================

def _require_user(request: Request) -> dict | JSONResponse:
    """Xác thực người dùng đã đăng nhập; trả về JSONResponse 401 nếu chưa đăng nhập."""
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            {"success": False, "error": "Vui lòng đăng nhập để sử dụng Personalization."},
            status_code=401,
        )
    return user



# =============================================================================
# 02. Đọc và lưu phong cách, hướng dẫn cá nhân
# =============================================================================

@router.get("/personalization")
async def get_personalization(request: Request):
    """Trả về cài đặt Personalization hiện tại và danh sách bộ nhớ cá nhân đang lưu."""
    user = _require_user(request)
    if isinstance(user, JSONResponse):
        return user

    mem_data = get_user_memories(user_id=str(user["id"]), query="", allow_full_fallback=True)
    return {
        "success": True,
        "personalization": user.get("personalization", {"base_style": "default", "custom_instructions": ""}),
        "memories": mem_data.get("memories", []),
    }


@router.patch("/personalization")
async def patch_personalization(request: Request):
    """Lưu cài đặt Base style & tone và Custom Instructions cá nhân của user."""
    user = _require_user(request)
    if isinstance(user, JSONResponse):
        return user

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Dữ liệu JSON không hợp lệ."}, status_code=400)

    try:
        updated_user = update_user_personalization(
            str(user["id"]),
            base_style=data.get("base_style"),
            custom_instructions=data.get("custom_instructions"),
        )
        return {
            "success": True,
            "message": "Đã lưu cài đặt Personalization!",
            "personalization": updated_user.get("personalization"),
            "user": _serialize_user(updated_user),
        }
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("Lỗi lưu cài đặt Personalization:")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)



# =============================================================================
# 03. Tóm tắt và cập nhật bộ nhớ bằng AI
# =============================================================================

@router.post("/personalization/memory-summary")
async def create_memory_summary(request: Request):
    """Gọi LLM tóm tắt toàn bộ long_term_memory cá nhân lọc theo user_id."""
    user = _require_user(request)
    if isinstance(user, JSONResponse):
        return user

    try:
        result = summarize_user_memories(str(user["id"]))
        return {"success": True, **result}
    except Exception as exc:
        logger.exception("Lỗi tóm tắt bộ nhớ cá nhân:")
        return JSONResponse({"success": False, "error": f"Không thể tóm tắt bộ nhớ: {exc}"}, status_code=500)


@router.post("/personalization/memory-ai-update")
async def ai_update_memory(request: Request):
    """Nhận lệnh tự nhiên để LLM thêm/sửa/xóa long_term_memory cá nhân và trả về tóm tắt mới."""
    user = _require_user(request)
    if isinstance(user, JSONResponse):
        return user

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Dữ liệu JSON không hợp lệ."}, status_code=400)

    instruction = str(data.get("instruction") or "").strip()
    try:
        result = apply_llm_memory_instruction(str(user["id"]), instruction)
        return result
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("Lỗi cập nhật bộ nhớ bằng AI:")
        return JSONResponse({"success": False, "error": f"Không thể cập nhật bộ nhớ: {exc}"}, status_code=500)



# =============================================================================
# 04. Xóa bộ nhớ cá nhân
# =============================================================================

@router.delete("/personalization/memories/{memory_id}")
async def remove_personal_memory(memory_id: str, request: Request):
    """Xóa mềm một bản ghi bộ nhớ cá nhân theo ID."""
    user = _require_user(request)
    if isinstance(user, JSONResponse):
        return user

    if not delete_user_memory_by_id_for_settings(str(user["id"]), memory_id):
        return JSONResponse({"success": False, "error": "Không tìm thấy mục bộ nhớ này."}, status_code=404)
    return {"success": True}


@router.delete("/personalization/memories")
async def clear_personal_memories(request: Request):
    """Xóa mềm toàn bộ bộ nhớ cá nhân của người dùng hiện tại."""
    user = _require_user(request)
    if isinstance(user, JSONResponse):
        return user

    deleted_count = delete_all_personal_memories(str(user["id"]))
    return {"success": True, "deleted_count": deleted_count}
