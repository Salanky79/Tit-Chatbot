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
    list_conversations)

from rag import add_document_to_rag, add_image_to_rag
from image_analysis import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_TYPES
from tools import set_current_thread_id


app = FastAPI() # tạo ứng dụng backend FastAPI

templates = Jinja2Templates(directory="templates") # giao diện người dùng (UI) được lưu trong thư mục templates

Path("uploads").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)


init_db()


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )



@app.get("/conversations")
async def conversations():
    items = list_conversations()

    return {
        "conversations": [
            {
                "thread_id": item.thread_id,
                "title": item.title,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat()
            }
            for item in items
        ]
    }



@app.get("/history/{thread_id}")
async def history(thread_id: str):
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




@app.post("/upload")
async def upload_document(
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

        create_or_update_conversation(thread_id, "Uploaded document")

        if suffix in SUPPORTED_IMAGE_TYPES:
            result = add_image_to_rag(file_path, thread_id, filename)
            return JSONResponse({
                "success": True,
                "message": f"Uploaded image {result['filename']}. You can now ask questions about it.",
            })

        result = add_document_to_rag(
            file_path=file_path,
            thread_id=thread_id
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

    agent = get_agent(selected_model)
    followup_model = normalize_model_name(selected_model)

    create_or_update_conversation(thread_id, user_message)
    save_chat_message(thread_id, "user", user_message)

    set_current_thread_id(thread_id)

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
                save_chat_message(thread_id, "assistant", final_answer)
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
                        save_chat_message(thread_id, "assistant", final_answer)
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
