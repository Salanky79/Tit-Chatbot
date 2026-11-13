from dotenv import load_dotenv
import os
import certifi

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

import asyncio
import json
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import uvicorn
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    AIMessageChunk,
    ToolMessage
)

from agent import get_agent, delete_user_checkpoints
from services.followups import FOLLOWUPS_ENABLED, generate_followup_questions
from services.token_limit_handler import (
    handle_partial_stream,
    is_daily_token_limit,
    is_rate_limit,
)
from storage.database import (
    init_db,
    save_chat_message,
    get_chat_history,
    create_or_update_conversation,
    list_conversations,
    get_conversation,
    delete_conversation,
    toggle_pin_conversation,
    create_user,
    authenticate_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    list_admin_conversations,
    get_admin_conversation_messages,
    list_user_thread_ids,
    delete_user,
    set_user_active,
    update_user_profile,
    create_session,
    get_session,
    delete_session,
)

from services.rag import (
    add_document_to_rag,
    add_image_to_rag,
    list_admin_documents,
    delete_admin_document,
    delete_user_documents,
)
from services.image_analysis import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_TYPES
from tools import set_current_thread_id, set_current_user_id


app = FastAPI() # tạo ứng dụng backend FastAPI

templates = Jinja2Templates(directory="templates") # giao diện người dùng (UI) được lưu trong thư mục templates

Path("uploads").mkdir(exist_ok=True)
Path("uploads/avatars").mkdir(exist_ok=True, parents=True)
Path("data").mkdir(exist_ok=True)


@app.on_event("startup")
def startup_event() -> None:
    if os.getenv("TESTING", "").lower() in ("1", "true"):
        return
    try:
        init_db()
    except Exception as exc:
        logger.warning("Could not initialize MongoDB indexes on startup: %s", exc)


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


# ---------------------------------------------------------------------------
# Authentication APIs
# ---------------------------------------------------------------------------

@app.post("/auth/signup")
async def auth_signup(request: Request):
    """Đăng ký tài khoản người dùng mới và tự động tạo phiên đăng nhập."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body."}, status_code=400)

    username = data.get("username", "")
    email = data.get("email", "")
    password = data.get("password", "")
    full_name = data.get("full_name", "")

    try:
        user = create_user(username=username, email=email, password=password, full_name=full_name)
        session_id = create_session(user["id"])
        response = JSONResponse({
            "success": True,
            "message": "Đăng ký tài khoản thành công!",
            "user": _serialize_user(user),
            "token": session_id,
        })
        response.set_cookie(
            key="tit_session",
            value=session_id,
            httponly=True, # Javascript không thể truy cập cookie này, chỉ server mới đọc được
            samesite="lax", # cho phép gửi cookie trong các request cùng domain và một số request cross-site an toàn
            max_age=7 * 24 * 3600,
            path="/",
        )
        return response
    except ValueError as ve:
        return JSONResponse({"success": False, "error": str(ve)}, status_code=400)
    except Exception as exc:
        logger.exception("Lỗi đăng ký tài khoản:")
        return JSONResponse({"success": False, "error": f"Lỗi hệ thống: {str(exc)}"}, status_code=500)


@app.post("/auth/signin")
async def auth_signin(request: Request):
    """Đăng nhập tài khoản bằng username hoặc email."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body."}, status_code=400)

    username_or_email = (
        data.get("identifier", "")
        or data.get("username", "")
        or data.get("email", "")
        or data.get("username_or_email", "")
    )
    password = data.get("password", "")

    user = authenticate_user(username_or_email, password)
    if not user:
        return JSONResponse({
            "success": False,
            "error": "Tên đăng nhập / Email hoặc mật khẩu không chính xác."
        }, status_code=401)

    session_id = create_session(user["id"])
    response = JSONResponse({
        "success": True,
        "message": "Đăng nhập thành công!",
        "user": _serialize_user(user),
        "token": session_id, # dự phòng cho các client không hỗ trợ cookie, có thể dùng token này để gửi trong header Authorization: Bearer <token>
    })
    response.set_cookie(
        key="tit_session",
        value=session_id,
        httponly=True, # Javascript không thể truy cập cookie này, chỉ server mới đọc được
        samesite="lax", # cho phép gửi cookie trong các request cùng domain và một số request cross-site an toàn
        max_age=7 * 24 * 3600,
        path="/",
    )
    return response


@app.post("/auth/signout")
async def auth_signout(request: Request):
    """Đăng xuất và hủy bỏ phiên làm việc."""
    session_id = _session_id_from_request(request)
    if session_id:
        delete_session(session_id)

    response = JSONResponse({
        "success": True,
        "message": "Đã đăng xuất thành công."
    })
    response.delete_cookie(key="tit_session", path="/")
    return response


@app.get("/auth/me")
async def auth_me(request: Request):
    """Lấy thông tin người dùng đang đăng nhập."""
    user = get_current_user(request)
    if not user:
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": _serialize_user(user)}


@app.post("/auth/profile")
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


@app.post("/auth/avatar")
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


@app.get("/uploads/avatars/{filename}")
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


# ---------------------------------------------------------------------------
# Conversation & History APIs
# ---------------------------------------------------------------------------

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/admin")
async def admin_page(request: Request):
    """Render trang quản trị sau khi xác nhận session và role Admin."""
    user = get_current_user(request)
    if not user or user.get("role", "user") != "admin":
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="admin.html", context={})


@app.get("/admin/api/users")
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


@app.patch("/admin/api/users/{user_id}/status")
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


@app.delete("/admin/api/users/{user_id}")
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


@app.get("/admin/api/users/{user_id}/conversations")
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


@app.get("/admin/api/users/{user_id}/conversations/{thread_id}/messages")
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


@app.get("/admin/api/documents")
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


@app.delete("/admin/api/documents/{document_key:path}")
async def admin_delete_document(document_key: str, request: Request):
    """Xóa tài liệu khỏi Chroma và file upload nếu hệ thống có đường dẫn an toàn."""
    error = _admin_api_error(request)
    if error:
        return error
    if not delete_admin_document(document_key):
        return JSONResponse({"success": False, "error": "Document not found."}, status_code=404)
    return {"success": True}


@app.get("/conversations")
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


@app.post("/conversations/{thread_id}/pin")
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

# phân quyền truy cập cuộc trò chuyện dựa trên user_id của người dùng và thread_id của cuộc trò chuyện. Nếu người dùng không phải là chủ sở hữu của cuộc trò chuyện, họ sẽ nhận được lỗi 403 (Forbidden).
@app.get("/history/{thread_id}")
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


@app.delete("/conversations/{thread_id}")
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


@app.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    thread_id: str = Form(...)
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
        create_or_update_conversation(thread_id, "Uploaded document", user_id=user_id)

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


def _looks_like_image(contents: bytes, suffix: str) -> bool:
    if suffix in {".jpg", ".jpeg"}:
        return contents.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return contents.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(contents) >= 12 and contents[:4] == b"RIFF" and contents[8:12] == b"WEBP"
    return False



def sse_data(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def should_stream_chunk(chunk: Any, metadata: dict[str, Any] | None) -> bool:
    """
    This prevents raw tool/search/RAG JSON from appearing in the frontend.

    We only stream normal AI text chunks.
    We do NOT stream:
    - ToolMessage
    - messages from tool nodes
    - tool call chunks
    - raw tool outputs
    """

    metadata = metadata or {}

    node_name = str(metadata.get("langgraph_node", "")).lower()

    if "tool" in node_name:
        return False

    if isinstance(chunk, ToolMessage):
        return False

    if not isinstance(chunk, (AIMessage, AIMessageChunk)):
        return False

    if getattr(chunk, "tool_calls", None):
        return False

    if getattr(chunk, "invalid_tool_calls", None):
        return False

    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}

    if additional_kwargs.get("tool_calls"):
        return False

    return True


def extract_text_from_chunk(chunk: Any) -> str:
    content = getattr(chunk, "content", "")

    if not content:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []

        for item in content:
            if isinstance(item, str):
                text_parts.append(item)

            elif isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
                elif isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    text_parts.append(item["content"])

        return "".join(text_parts)

    return ""



def _chat_event_generator(
    agent: Any,
    thread_id: str,
    user_id: str | None,
    user_message: str,
    selected_model: str,
    config: dict[str, Any],
) -> Iterator[str]:
    set_current_thread_id(thread_id)
    set_current_user_id(user_id or "")

    final_answer = ""

    try:
        inputs = {
            "messages": [
                HumanMessage(content=user_message)
            ]
        }

        for chunk, metadata in agent.stream(
            inputs,
            config=config,
            stream_mode="messages"
        ):
            if not should_stream_chunk(chunk, metadata):
                continue

            token = extract_text_from_chunk(chunk)

            if token:
                final_answer += token
                yield sse_data({"token": token})

        if final_answer.strip():
            save_chat_message(thread_id, "assistant", final_answer, user_id=user_id)
    except Exception as e:
        err_str = str(e)

        # =========================================================================
        # XỬ LÝ CHẠM TRẦN TOKEN NGÀY (TPD): KHÔNG VỨT LỖI ĐỎ RA MÀN HÌNH
        # In kết quả đang làm dở + gắn thông báo nhẹ nhàng + lưu vào DB
        # =========================================================================
        if is_daily_token_limit(e):
            logger.warning(
                f"[DailyTokenLimit] Model '{selected_model}' hit daily limit during stream. "
                f"Recovering partial answer (length: {len(final_answer)} chars)."
            )
            full_message = handle_partial_stream(final_answer, selected_model, language="vi")

            # Nếu trước đó đã stream một phần ra màn hình -> chỉ cần stream phần thông báo đuôi
            if final_answer.strip():
                notice_suffix = full_message[len(final_answer):]
                if notice_suffix:
                    yield sse_data({"token": notice_suffix})
            else:
                # Nếu chưa sinh được chữ nào -> stream toàn bộ câu thông báo lịch sự
                yield sse_data({"token": full_message})

            # Lưu toàn bộ tin nhắn đã cứu hộ vào DB để người dùng không bị mất nội dung
            try:
                save_chat_message(thread_id, "assistant", full_message, user_id=user_id)
            except Exception as save_err:
                logger.error(f"Failed to save partial message: {save_err}")

        else:
            is_model_unavailable = (
                "model_terms_required" in err_str
                or "model_not_found" in err_str
                or "does not exist or you do not have access to it" in err_str
            )

            if is_rate_limit(e):
                logger.error(f"[RateLimit-TPM] Model '{selected_model}' hit Groq rate limit: {e}")
                yield sse_data({
                    "error": f"⚠️ Mô hình '{selected_model}' đang tạm thời chạm giới hạn tốc độ theo phút (TPM). Vui lòng thử lại sau 15–30 giây."
                })
            elif is_model_unavailable:
                logger.error(f"[ModelUnavailable] Model '{selected_model}' requires terms or is inaccessible: {e}")
                yield sse_data({
                    "error": f"⚠️ Mô hình '{selected_model}' yêu cầu chấp thuận điều khoản sử dụng tại console.groq.com."
                })
            else:
                logger.error(f"[ChatError] Error during chat streaming: {e}")
                yield sse_data({"error": str(e)})

    # Kết thúc stream ngay lập tức khi chatbot trả lời xong (0ms delay cho người dùng)
    yield sse_data({"done": True})


@app.post("/chat/stream")
async def chat_stream(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body."},
            status_code=400
        )

    user_message = data.get("message", "")
    thread_id = data.get("thread_id", "default")
    selected_model = data.get("model", "openai/gpt-oss-20b")

    if not user_message.strip():
        return JSONResponse(
            {"error": "Message is required."},
            status_code=400
        )

    user = get_current_user(request)
    user_id = user["id"] if user else None

    # Kiểm tra quyền sở hữu cuộc trò chuyện nếu thread_id đã tồn tại
    conv = get_conversation(thread_id)
    if not _can_access_conversation(conv, user):
        return JSONResponse(
            {"error": "Bạn không có quyền gửi tin nhắn trong cuộc trò chuyện này."},
            status_code=403
        )

    agent = get_agent(selected_model)
    create_or_update_conversation(thread_id, user_message, user_id=user_id)
    save_chat_message(thread_id, "user", user_message, user_id=user_id)

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    return StreamingResponse(
        _chat_event_generator(
            agent=agent,
            thread_id=thread_id,
            user_id=user_id,
            user_message=user_message,
            selected_model=selected_model,
            config=config,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/chat/followups")
async def chat_followups(request: Request):
    """Sinh câu hỏi gợi ý tiếp theo (Follow-up Suggestions) bất đồng bộ, chạy song song ngầm.

    - Không chặn luồng trả lời chính (/chat/stream), giúp giảm thời gian chờ của người dùng về 0ms.
    - Chạy trong thread pool qua asyncio.to_thread để giữ FastAPI Event Loop hoàn toàn non-blocking.
    """
    if not FOLLOWUPS_ENABLED:
        return {"success": True, "suggestions": []}

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

    user_message = (data.get("user_message") or "").strip()
    assistant_answer = (data.get("assistant_answer") or "").strip()
    selected_model = data.get("model", "openai/gpt-oss-20b")

    # Kiểm tra ngữ cảnh: nếu thiếu câu hỏi hoặc câu trả lời thì không sinh gợi ý
    if not user_message or not assistant_answer:
        return {"success": True, "suggestions": []}

    try:
        # Sử dụng asyncio.to_thread để chạy hàm đồng bộ gọi LLM trên worker thread riêng biệt,
        # giải phóng Main Thread của FastAPI để tiếp tục phục vụ các request khác
        questions = await asyncio.to_thread(
            generate_followup_questions,
            user_message=user_message,
            assistant_answer=assistant_answer,
            model_name=selected_model,
        )
        return {
            "success": True,
            "suggestions": questions if len(questions) == 2 else []
        }
    except Exception as exc:
        logger.warning(f"Parallel follow-up generation failed: {exc}")
        return {"success": False, "suggestions": []}





if __name__ == "__main__":
   
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        reload=True
    )
