# Bootstrap: nạp môi trường và chứng chỉ trước các module ứng dụng.
from dotenv import load_dotenv
import os
import certifi

load_dotenv()
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

import json
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk, ToolMessage

# Các import công khai được giữ để facade/patch tương thích tiếp tục hoạt động.
from agent import get_agent, delete_user_checkpoints, normalize_model_name
from services.followups import FOLLOWUPS_ENABLED, generate_followup_questions
from services.token_limit_handler import handle_partial_stream, is_daily_token_limit, is_rate_limit
from storage.database import (
    init_db,
    save_chat_message,
    get_chat_history,
    create_or_update_conversation,
    list_conversations,
    get_conversation,
    delete_conversation,
    toggle_pin_conversation,
    update_conversation_title,
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
from services.authentication import (
    allow_immediate_resend, cancel_challenge, get_challenge, resend_challenge,
    start_challenge, verify_challenge,
)
from services.email_service import send_verification_code
from services.rag import (
    add_document_to_rag, add_image_to_rag, list_admin_documents,
    delete_admin_document, delete_user_documents,
)
from services.image_analysis import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_TYPES
from services.language import resolve_request_language, set_current_language, is_vietnamese
from tools import set_current_thread_id, set_current_user_id, set_current_model_name

from api.dependencies import (
    AUTH_CHALLENGE_COOKIE, _admin_api_error, _begin_email_verification,
    _can_access_conversation, _clear_challenge_cookie, _conversation_owner_id,
    _delete_user_avatars, _masked_email, _serialize_conversation, _serialize_user,
    _session_id_from_request, _set_challenge_cookie, get_current_user,
)
from api.auth import (
    router as _auth_router, auth_avatar, auth_cancel_challenge, auth_challenge_status,
    auth_me, auth_resend, auth_signin, auth_signout, auth_signup, auth_update_profile,
    auth_verify, get_avatar_file,
)
from api.pages import router as _pages_router, _default_templates, admin_page, home
from api.admin import (
    router as _admin_router, admin_delete_document, admin_delete_user,
    admin_get_conversation_messages, admin_list_documents, admin_list_user_conversations,
    admin_list_users, admin_set_user_status,
)
from api.conversations import (
    router as _conversations_router, conversations, history, pin_conversation,
    remove_conversation, rename_conversation,
)
from api.documents import router as _documents_router, _looks_like_image, upload_document
from api.chat import (
    router as _chat_router, FollowupRequest, _chat_event_generator, chat_followups,
    chat_stream, extract_text_from_chunk, should_stream_chunk, sse_data,
)
from api.projects import router as _projects_router
from api.personalization import router as _personalization_router

# =============================================================================
# Khởi tạo ứng dụng, template và thư mục dữ liệu
# =============================================================================
logger = logging.getLogger(__name__)
app = FastAPI()
templates = _default_templates
Path("uploads").mkdir(exist_ok=True)
Path("uploads/avatars").mkdir(exist_ok=True, parents=True)
Path("data").mkdir(exist_ok=True)



# =============================================================================
# 01. Khởi động ứng dụng và tạo index
# =============================================================================

@app.on_event("startup")
def startup_event() -> None:
    if os.getenv("TESTING", "").lower() in ("1", "true"):
        return
    try:
        init_db()
    except Exception as exc:
        logger.warning("Could not initialize MongoDB indexes on startup: %s", exc)


# =============================================================================
# Tài nguyên giao diện và đăng ký router theo thứ tự hiện tại
# =============================================================================
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)
app.include_router(_auth_router, prefix="/api")
app.include_router(_pages_router)
app.include_router(_admin_router, prefix="/api")
app.include_router(_conversations_router, prefix="/api")
app.include_router(_documents_router, prefix="/api")
app.include_router(_chat_router, prefix="/api")
app.include_router(_projects_router, prefix="/api")
app.include_router(_personalization_router, prefix="/api")
# Alias tương thích ngược cho các URL ảnh đại diện cũ chưa có tiền tố /api
app.add_api_route("/uploads/avatars/{filename}", get_avatar_file, methods=["GET"])


# Điểm chạy trực tiếp cho môi trường phát triển.
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)
