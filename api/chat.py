from __future__ import annotations

import contextvars
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
from storage.projects import get_membership, get_project
from services.project_context import build_project_context
from api.dependencies import (
    FOLLOWUPS_ENABLED,
    _can_access_conversation,
    create_or_update_conversation,
    generate_followup_questions,
    get_agent,
    get_conversation,
    get_current_user,
    handle_partial_stream,
    is_daily_token_limit,
    is_rate_limit,
    is_vietnamese,
    normalize_model_name,
    resolve_request_language,
    save_chat_message,
    set_current_language,
    set_current_model_name,
    set_current_personalization,
    set_current_project,
    set_project_prompt_context,
    set_current_thread_id,
    set_current_user_id,
)

router = APIRouter()

set_current_thread_id = app_callable('set_current_thread_id', set_current_thread_id)
set_current_user_id = app_callable('set_current_user_id', set_current_user_id)
set_current_model_name = app_callable('set_current_model_name', set_current_model_name)
set_current_personalization = app_callable('set_current_personalization', set_current_personalization)
set_current_language = app_callable('set_current_language', set_current_language)
is_vietnamese = app_callable('is_vietnamese', is_vietnamese)
save_chat_message = app_callable('save_chat_message', save_chat_message)
is_daily_token_limit = app_callable('is_daily_token_limit', is_daily_token_limit)
handle_partial_stream = app_callable('handle_partial_stream', handle_partial_stream)
is_rate_limit = app_callable('is_rate_limit', is_rate_limit)
normalize_model_name = app_callable('normalize_model_name', normalize_model_name)
get_current_user = app_callable('get_current_user', get_current_user)
get_conversation = app_callable('get_conversation', get_conversation)
_can_access_conversation = app_callable('_can_access_conversation', _can_access_conversation)
get_agent = app_callable('get_agent', get_agent)
create_or_update_conversation = app_callable('create_or_update_conversation', create_or_update_conversation)
resolve_request_language = app_callable('resolve_request_language', resolve_request_language)
generate_followup_questions = app_callable('generate_followup_questions', generate_followup_questions)
logger = AppValueProxy("logger", logging.getLogger("app"))
FOLLOWUPS_ENABLED = AppValueProxy("FOLLOWUPS_ENABLED", FOLLOWUPS_ENABLED)

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

# lấy reasoning từ chunk stream của LLM nếu model trả riêng, hoặc từ thẻ <think>...</think> trong chunk.content
def extract_reasoning_from_chunk(chunk: Any) -> str:
    """Trích xuất chuỗi suy luận (reasoning) từ chunk stream của LLM nếu model trả riêng."""
    additional_kwargs = getattr(chunk, "additional_kwargs", None) or {}
    reasoning = additional_kwargs.get("reasoning_content") or additional_kwargs.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return reasoning

    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"thinking", "reasoning"}:
                text = item.get("thinking") or item.get("reasoning") or item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "".join(parts)
    return ""


class _ReasoningTagSplitter:
    """Tách thẻ <think>...</think> khi model trả reasoning thô lẫn vào chunk.content."""

    _OPEN_TAG = "<think>"
    _CLOSE_TAG = "</think>"

    def __init__(self) -> None:
        self._in_think = False
        self._buffer = ""

    @staticmethod
    def _hold_partial_suffix(text: str, tag: str) -> tuple[str, str]:
        """Giữ lại phần đuôi nếu khớp tiền tố của tag mở/đóng để đợi chunk kế tiếp."""
        max_len = min(len(text), len(tag) - 1)
        for length in range(max_len, 0, -1):
            if tag.startswith(text[-length:]):
                return text[:-length], text[-length:]
        return text, ""

    def feed(self, text: str) -> tuple[str, str]:
        """Nhận một đoạn text stream và tách thành cặp (reasoning_part, answer_part)."""
        if not text:
            return "", ""
        self._buffer += text
        reasoning_parts: list[str] = []
        answer_parts: list[str] = []

        while self._buffer:
            if not self._in_think:
                idx = self._buffer.find(self._OPEN_TAG)
                if idx != -1:
                    if idx > 0:
                        answer_parts.append(self._buffer[:idx])
                    self._buffer = self._buffer[idx + len(self._OPEN_TAG):]
                    self._in_think = True
                    continue
                emit, rest = self._hold_partial_suffix(self._buffer, self._OPEN_TAG)
                if emit:
                    answer_parts.append(emit)
                self._buffer = rest
                break
            else:
                idx = self._buffer.find(self._CLOSE_TAG)
                if idx != -1:
                    if idx > 0:
                        reasoning_parts.append(self._buffer[:idx])
                    self._buffer = self._buffer[idx + len(self._CLOSE_TAG):]
                    self._in_think = False
                    continue
                emit, rest = self._hold_partial_suffix(self._buffer, self._CLOSE_TAG)
                if emit:
                    reasoning_parts.append(emit)
                self._buffer = rest
                break

        return "".join(reasoning_parts), "".join(answer_parts)

    def flush(self) -> tuple[str, str]:
        """Đẩy nốt phần buffer còn lại khi luồng stream kết thúc."""
        if not self._buffer:
            return "", ""
        leftover = self._buffer
        self._buffer = ""
        if self._in_think:
            self._in_think = False
            return leftover, ""
        return "", leftover


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


def _apply_chat_context(
    *,
    thread_id: str,
    user_id: str | None,
    selected_model: str,
    user_lang: str,
    project_id: str | None,
    project_memory_mode: str,
    project_instructions: str,
    project_context: str,
    personalization: dict[str, Any] | None,
) -> None:
    """Thiết lập toàn bộ ContextVar cho request chat hiện tại."""
    set_current_thread_id(thread_id)
    set_current_user_id(user_id or "")
    set_current_model_name(selected_model)
    set_current_language(user_lang)
    set_current_project(project_id, project_memory_mode)
    set_project_prompt_context(project_instructions, project_context)
    pers = personalization or {}
    set_current_personalization(pers.get("base_style"), pers.get("custom_instructions"))


def _chat_event_generator(
    agent: Any,
    thread_id: str,
    user_id: str | None,
    user_message: str,
    selected_model: str,
    config: dict[str, Any],
    user_lang: str = "vi",
    project_id: str | None = None,
    project_memory_mode: str = "",
    project_instructions: str = "",
    project_context: str = "",
    personalization: dict[str, Any] | None = None,
) -> Iterator[str]:
    """Stream câu trả lời và giữ ContextVar xuyên suốt mọi lần yield qua threadpool."""
    # Starlette iterate_in_threadpool chạy mỗi bước next() qua anyio.to_thread.run_sync (copy context mới mỗi lần).
    # Gói agent.stream vào một stream_ctx cố định để user_id/project/personalization không bị reset sau yield đầu tiên.
    stream_ctx = contextvars.copy_context()
    stream_ctx.run(
        _apply_chat_context,
        thread_id=thread_id,
        user_id=user_id,
        selected_model=selected_model,
        user_lang=user_lang,
        project_id=project_id,
        project_memory_mode=project_memory_mode,
        project_instructions=project_instructions,
        project_context=project_context,
        personalization=personalization,
    )

    final_answer = ""
    reasoning_splitter = _ReasoningTagSplitter()
    stream_sentinel = object()

    try:
        inputs = {
            "messages": [
                HumanMessage(content=user_message)
            ]
        }

        stream_iter = stream_ctx.run(
            lambda: iter(agent.stream(inputs, config=config, stream_mode="messages"))
        )
        while True:
            item = stream_ctx.run(next, stream_iter, stream_sentinel)
            if item is stream_sentinel:
                break
            chunk, metadata = item
            if not should_stream_chunk(chunk, metadata):
                continue

            parsed_reasoning = extract_reasoning_from_chunk(chunk)
            raw_text = extract_text_from_chunk(chunk)
            tag_reasoning, token = reasoning_splitter.feed(raw_text) if raw_text else ("", "")

            reasoning_token = parsed_reasoning + tag_reasoning
            if reasoning_token:
                # Chỉ stream reasoning live ra frontend; không lưu vào final_answer hay DB.
                yield sse_data({"reasoning": reasoning_token})

            if token:
                final_answer += token
                yield sse_data({"token": token})

        rem_reasoning, rem_token = reasoning_splitter.flush()
        if rem_reasoning:
            yield sse_data({"reasoning": rem_reasoning})
        if rem_token:
            final_answer += rem_token
            yield sse_data({"token": rem_token})

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
            full_message = stream_ctx.run(handle_partial_stream, final_answer, selected_model)

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
                err_msg = (
                    f"⚠️ Mô hình '{selected_model}' đang tạm thời chạm giới hạn tốc độ theo phút (TPM). Vui lòng thử lại sau 15–30 giây."
                    if is_vietnamese(user_lang)
                    else f"⚠️ Model '{selected_model}' has temporarily reached its per-minute rate limit (TPM). Please try again in 15–30 seconds."
                )
                yield sse_data({"error": err_msg})
            elif is_model_unavailable:
                logger.error(f"[ModelUnavailable] Model '{selected_model}' requires terms or is inaccessible: {e}")
                err_msg = (
                    f"⚠️ Mô hình '{selected_model}' yêu cầu chấp thuận điều khoản sử dụng tại console.groq.com."
                    if is_vietnamese(user_lang)
                    else f"⚠️ Model '{selected_model}' requires accepting terms of use at console.groq.com."
                )
                yield sse_data({"error": err_msg})
            else:
                logger.error(f"[ChatError] Error during chat streaming: {e}")
                yield sse_data({"error": str(e)})

    # Kết thúc stream ngay lập tức khi chatbot trả lời xong (0ms delay cho người dùng)
    yield sse_data({"done": True})


@router.post("/chat/stream")
async def chat_stream(request: Request):
    """Xác thực scope Project, lưu user turn và bắt đầu stream chat."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body."},
            status_code=400
        )

    user_message = data.get("message", "")
    thread_id = data.get("thread_id", "default")
    selected_model = normalize_model_name(data.get("model", "openai/gpt-oss-20b"))

    if not user_message.strip():
        return JSONResponse(
            {"error": "Message is required."},
            status_code=400
        )

    user = get_current_user(request)
    user_id = user["id"] if user else None
    project_id = (data.get("project_id") or "").strip() or None

    # Kiểm tra quyền sở hữu cuộc trò chuyện nếu thread_id đã tồn tại
    conv = get_conversation(thread_id)
    if not _can_access_conversation(conv, user):
        return JSONResponse(
            {"error": "Bạn không có quyền gửi tin nhắn trong cuộc trò chuyện này."},
            status_code=403
        )

    if conv and conv.get("project_id"):
        existing_project_id = str(conv.get("project_id"))
        if project_id and project_id != existing_project_id:
            return JSONResponse({"error": "Conversation is linked to another project."}, status_code=403)
        project_id = existing_project_id

    project_memory_mode = ""
    project_instructions = ""
    project_context = ""
    if project_id:
        if not user:
            return JSONResponse({"error": "Sign in to use project chats."}, status_code=401)
        membership = get_membership(project_id, user_id)
        project = get_project(project_id)
        if not membership or not project:
            return JSONResponse({"error": "Project not found or access denied."}, status_code=404)
        if conv and not conv.get("project_id"):
            return JSONResponse({"error": "This conversation is not part of that project."}, status_code=403)
        project_memory_mode = project.get("memory_mode", "project_only")
        project_instructions = project.get("instructions", "")
        project_context = build_project_context(project_id, thread_id, user_message, project_memory_mode)

    is_temporary = bool(data.get("is_temporary", False))
    if project_id and is_temporary:
        return JSONResponse({"error": "Temporary chat is unavailable inside a project."}, status_code=400)
    if not is_temporary and conv and conv.get("is_temporary"):
        is_temporary = True

    agent = get_agent(selected_model)
    create_or_update_conversation(
        thread_id, user_message, user_id=user_id,
        is_temporary=is_temporary, project_id=project_id,
    )
    save_chat_message(thread_id, "user", user_message, user_id=user_id)

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    user_lang = resolve_request_language(request.headers)
    personalization = user.get("personalization") if user else None
    _apply_chat_context(
        thread_id=thread_id,
        user_id=user_id,
        selected_model=selected_model,
        user_lang=user_lang,
        project_id=project_id,
        project_memory_mode=project_memory_mode,
        project_instructions=project_instructions,
        project_context=project_context,
        personalization=personalization,
    )

    return StreamingResponse(
        _chat_event_generator(
            agent=agent,
            thread_id=thread_id,
            user_id=user_id,
            user_message=user_message,
            selected_model=selected_model,
            config=config,
            user_lang=user_lang,
            project_id=project_id,
            project_memory_mode=project_memory_mode,
            project_instructions=project_instructions,
            project_context=project_context,
            personalization=personalization,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


class FollowupRequest(BaseModel):
    user_message: str = ""
    assistant_answer: str = ""
    model: str = "openai/gpt-oss-20b"


@router.post("/chat/followups")
async def chat_followups(payload: FollowupRequest):
    """Sinh câu hỏi gợi ý tiếp theo (Follow-up Suggestions).

    - Gọi service Native Async để chờ API LLM mà không chiếm worker thread.
    - Không chặn luồng trả lời chính (/chat/stream), giúp giảm thời gian chờ của người dùng về 0ms.
    """
    if not FOLLOWUPS_ENABLED:
        return {"success": True, "suggestions": []}

    # Kiểm tra dữ liệu đầu vào dùng cho sinh gợi ý: nếu thiếu câu hỏi hoặc câu trả lời thì không sinh gợi ý
    # payload là một đối tượng FollowupRequest được FastAPI tự động parse từ JSON body của request.
    user_message = (payload.user_message or "").strip()
    assistant_answer = (payload.assistant_answer or "").strip()
    selected_model = payload.model or "openai/gpt-oss-20b"

    # Kiểm tra ngữ cảnh: nếu thiếu câu hỏi hoặc câu trả lời thì không sinh gợi ý
    if not user_message or not assistant_answer:
        return {"success": True, "suggestions": []}

    try:
        questions = await generate_followup_questions(
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
