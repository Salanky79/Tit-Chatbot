"""Vision model calls for images uploaded to a conversation."""

from __future__ import annotations

import config as cfg
from config import (SUPPORTED_IMAGE_TYPES, MAX_IMAGE_BYTES, MAX_IMAGE_QUESTION_TOKENS, VISION_OUTPUT_TOKENS, VISION_RATE_LIMIT_MAX_RETRIES)

import base64
import os
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

from utils.token_utils import estimate_tokens
from utils.usage_logging import log_token_usage
from services.token_limit_handler import (
    format_tool_limit_notice,
    invoke_with_rate_limit_retry,
    is_daily_token_limit,
)


# =============================================================================
# 01. Phân tích ảnh bằng model thị giác
# =============================================================================

def detect_image_format(contents: bytes) -> tuple[str, str] | None:
    """Detect real JPG, PNG, or WEBP image format from binary magic bytes."""
    if contents.startswith(b"\xff\xd8\xff"):
        return ".jpg", SUPPORTED_IMAGE_TYPES[".jpg"]
    if contents.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", SUPPORTED_IMAGE_TYPES[".png"]
    if len(contents) >= 12 and contents[:4] == b"RIFF" and contents[8:12] == b"WEBP":
        return ".webp", SUPPORTED_IMAGE_TYPES[".webp"]
    return None


def analyze_image(image_path: str, question: str, llm=None) -> str:
    """Answer a question about an uploaded image with a Groq vision model.

    Tách context theo tác vụ:
    - Chỉ tiếp nhận câu hỏi trực quan và dữ liệu hình ảnh, hoàn toàn cô lập khỏi lịch sử chat.
    - Giới hạn token trước khi gọi: kiểm soát độ dài câu hỏi để không vượt ngân sách vision model.
    """
    path = Path(image_path).resolve()
    upload_root = Path(cfg.UPLOAD_DIR).resolve()
    if not path.is_relative_to(upload_root):
        raise ValueError("Image path is outside the upload directory.")
    if not path.is_file():
        raise ValueError("The uploaded image is no longer available on the server.")

    image_bytes = path.read_bytes()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too large for vision analysis (maximum 14 MB).")
    detected = detect_image_format(image_bytes)
    mime_type = detected[1] if detected else SUPPORTED_IMAGE_TYPES.get(path.suffix.lower())
    if not mime_type:
        raise ValueError("Unsupported image type. Upload a JPG, PNG, or WEBP image.")

    # Bước 3 & 4: Cô lập context và kiểm soát token cho câu hỏi thị giác
    clean_question = (question or "").strip()
    if estimate_tokens(clean_question) > MAX_IMAGE_QUESTION_TOKENS:
        max_chars = MAX_IMAGE_QUESTION_TOKENS * 3
        clean_question = (
            clean_question[:max_chars]
            + " [câu hỏi được rút gọn để đảm bảo ngân sách token]"
        )

    if llm is None:
        llm = ChatGroq(
            model=os.getenv("GROQ_VISION_MODEL", cfg.VISION_MODEL),
            temperature=cfg.VISION_TEMPERATURE,
            max_tokens=VISION_OUTPUT_TOKENS,
        )
    # lưu dạng chuỗi base64 để gửi đến model thị giác, tránh gửi file nhị phân trực tiếp
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    prompt = (
        "Answer the user's question using the image. Describe visible evidence and "
        "readable text when relevant. If details are unclear or unreadable, say so; "
        "do not guess. Respond in the same language as the question.\n\n"
        f"Question: {clean_question or 'Describe this image.'}"
    )
    try:
        response = invoke_with_rate_limit_retry(
            lambda: llm.invoke([HumanMessage(content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ])]),
            max_retries=VISION_RATE_LIMIT_MAX_RETRIES,
            operation="Image Analysis",
        )
        vision_model = getattr(llm, "model_name", None) or getattr(llm, "model", "Groq-Vision")
        log_token_usage(str(vision_model), "Image Analysis", response)
    except Exception as exc:
        if is_daily_token_limit(exc):
            return format_tool_limit_notice("analyze_uploaded_image")
        raise RuntimeError(f"Vision model could not analyze the image: {exc}") from exc

    content = getattr(response, "content", response)
    if isinstance(content, str):
        answer = content.strip()
    elif isinstance(content, list):
        answer = "".join(
            part if isinstance(part, str) else part.get("text", "")
            for part in content
            if isinstance(part, str) or (isinstance(part, dict) and part.get("type") == "text")
        ).strip()
    else:
        answer = ""
    if not answer:
        raise RuntimeError("Vision model returned an empty response.")
    return answer
