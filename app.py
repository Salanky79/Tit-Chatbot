from dotenv import load_dotenv
import os
import certifi

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

import json
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

import uvicorn
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    AIMessageChunk,
    ToolMessage
)

from agent import get_agent, normalize_model_name
from followups import generate_followup_questions, fallback_followup_questions
from database import (
    init_db,
    save_chat_message,
    get_chat_history,
    create_or_update_conversation,
    list_conversations,
    get_conversation,
    delete_conversation,
    create_user,
    authenticate_user,
    get_user_by_id,
    create_session,
    get_session,
    delete_session,
)

from rag import add_document_to_rag, add_image_to_rag
from image_analysis import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_TYPES
from tools import set_current_thread_id, set_current_user_id


app = FastAPI() # tạo ứng dụng backend FastAPI

templates = Jinja2Templates(directory="templates") # giao diện người dùng (UI) được lưu trong thư mục templates

Path("uploads").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)


@app.on_event("startup")
def startup_event():
    if os.getenv("TESTING", "").lower() in ("1", "true"):
        return
    try:
        init_db()
    except Exception as exc:
        logger.warning("Could not initialize MongoDB indexes on startup: %s", exc)


def _serialize_user(user: dict | None) -> dict | None:
    """Chuyển đổi các trường datetime của user sang định dạng ISO string để serialize JSON an toàn."""
    if not user:
        return None
    d = dict(user)
    if hasattr(d.get("created_at"), "isoformat"):
        d["created_at"] = d["created_at"].isoformat()
    if hasattr(d.get("updated_at"), "isoformat"):
        d["updated_at"] = d["updated_at"].isoformat()
    return d


def get_current_user(request: Request) -> dict | None:
    """Trích xuất thông tin người dùng từ Cookie, Header Authorization hoặc Query Param."""
    session_id = request.cookies.get("tit_session")
    if not session_id:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_id = auth_header[7:].strip()
    if not session_id:
        session_id = request.query_params.get("session_id")
    if not session_id:
        return None

    session = get_session(session_id)
    if not session:
        return None

    return get_user_by_id(session.get("user_id", ""))


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

    username_or_email = data.get("username", "") or data.get("email", "")
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


@app.post("/auth/signout")
async def auth_signout(request: Request):
    """Đăng xuất và hủy bỏ phiên làm việc."""
    session_id = request.cookies.get("tit_session")
    if not session_id:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_id = auth_header[7:].strip()
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
                "user_id": getattr(item, "user_id", None)
            }
            for item in items
        ]
    }


@app.get("/history/{thread_id}")
async def history(thread_id: str, request: Request):
    user = get_current_user(request)
    conv = get_conversation(thread_id)
    conv_user_id = conv.get("user_id") if isinstance(conv, dict) else getattr(conv, "user_id", None)
    if conv and conv_user_id:
        if not user or str(user["id"]) != str(conv_user_id):
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
    conv_user_id = conv.get("user_id") if isinstance(conv, dict) else getattr(conv, "user_id", None)
    if conv and conv_user_id:
        if not user or str(user["id"]) != str(conv_user_id):
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
            result = add_image_to_rag(file_path, thread_id, filename, user_id=user_id)
            return JSONResponse({
                "success": True,
                "message": f"Uploaded image {result['filename']}. You can now ask questions about it.",
            })

        result = add_document_to_rag(
            file_path=file_path,
            thread_id=thread_id,
            user_id=user_id
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



def sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def should_stream_chunk(chunk, metadata) -> bool:
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


def extract_text_from_chunk(chunk) -> str:
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
    conv_user_id = conv.get("user_id") if isinstance(conv, dict) else getattr(conv, "user_id", None)
    if conv and conv_user_id:
        if not user or str(user["id"]) != str(conv_user_id):
            return JSONResponse(
                {"error": "Bạn không có quyền gửi tin nhắn trong cuộc trò chuyện này."},
                status_code=403
            )

    agent = get_agent(selected_model)
    followup_model = normalize_model_name(selected_model)

    create_or_update_conversation(thread_id, user_message, user_id=user_id)
    save_chat_message(thread_id, "user", user_message, user_id=user_id)

    set_current_thread_id(thread_id)
    set_current_user_id(user_id or "")

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    def event_generator():
        final_answer = ""
        stream_error = None

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
            is_rate_limit = "429" in err_str or "rate_limit_exceeded" in err_str.lower()
            if is_rate_limit and selected_model == "openai/gpt-oss-120b":
                logger.warning("Groq Rate Limit 429 on 120b. Falling back to openai/gpt-oss-20b.")
                fallback_msg = (
                    "\n\n> ⚠️ **Hệ thống:** Mô hình *gpt-oss-120b* tạm thời đạt giới hạn 8,000 TPM của Groq. "
                    "Tit đang tự động chuyển tiếp sang mô hình *gpt-oss-20b* để tiếp tục phản hồi cho bạn mà không bị ngắt quãng...\n\n"
                )
                yield sse_data({"token": fallback_msg})
                final_answer += fallback_msg
                try:
                    fallback_agent = get_agent("openai/gpt-oss-20b")
                    for chunk, metadata in fallback_agent.stream(
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
                    stream_error = None
                except Exception as fallback_exc:
                    stream_error = fallback_exc
                    yield sse_data({"error": f"Lỗi giới hạn tốc độ (Rate Limit 429): {str(fallback_exc)}"})
            else:
                stream_error = e
                yield sse_data({"error": str(e)})

        # Mỗi lượt chat hợp lệ luôn kết thúc bằng đúng hai gợi ý.
        # Luôn dùng gpt-oss-20b cho câu hỏi gợi ý để bảo tồn ngân sách token TPM.
        yield sse_data({"suggestions_pending": True})
        if stream_error:
            questions = fallback_followup_questions(user_message)
        else:
            try:
                questions = generate_followup_questions(
                    user_message=user_message,
                    assistant_answer=final_answer,
                    model_name="openai/gpt-oss-20b",
                )
            except Exception:
                questions = fallback_followup_questions(user_message)

        if len(questions) != 2:
            questions = fallback_followup_questions(user_message)
        yield sse_data({"suggestions": questions})
        yield sse_data({"done": True})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )





if __name__ == "__main__":
   
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        reload=True
    )
