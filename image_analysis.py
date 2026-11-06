"""Vision model calls for images uploaded to a conversation."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq


SUPPORTED_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
MAX_IMAGE_BYTES = 14 * 1024 * 1024


def analyze_image(image_path: str, question: str, llm=None) -> str:
    """Answer a question about an uploaded image with a Groq vision model."""
    path = Path(image_path).resolve()
    upload_root = Path("uploads").resolve()
    if not path.is_relative_to(upload_root):
        raise ValueError("Image path is outside the upload directory.")
    if not path.is_file():
        raise ValueError("The uploaded image is no longer available on the server.")

    mime_type = SUPPORTED_IMAGE_TYPES.get(path.suffix.lower())
    if not mime_type:
        raise ValueError("Unsupported image type. Upload a JPG, PNG, or WEBP image.")
    image_bytes = path.read_bytes()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too large for vision analysis (maximum 14 MB).")

    if llm is None:
        llm = ChatGroq(
            model=os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b"),
            temperature=0.2,
            max_tokens=1200,
        )

    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    prompt = (
        "Answer the user's question using the image. Describe visible evidence and "
        "readable text when relevant. If details are unclear or unreadable, say so; "
        "do not guess. Respond in the same language as the question.\n\n"
        f"Question: {question.strip() or 'Describe this image.'}"
    )
    try:
        response = llm.invoke([HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ])])
    except Exception as exc:
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
