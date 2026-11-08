"""Generate two user-prompt follow-up suggestions for each chat turn."""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from context_manager import estimate_tokens


logger = logging.getLogger(__name__)

MAX_FOLLOWUP_USER_TOKENS = 250
MAX_FOLLOWUP_ANSWER_TOKENS = 500
FOLLOWUP_OUTPUT_TOKENS = 512


def _clean_content_for_followup(text: str, max_tokens_limit: int) -> str:
    """Loại bỏ code blocks dài, tool dumps thô và giới hạn token cho followup."""
    if not text:
        return ""
    # Thay thế các khối code dài bằng placeholder ngắn gọn
    cleaned = re.sub(r"```[\w]*\n[\s\S]{100,}?```", "[Code snippet omitted]", text)
    # Loại bỏ các tag đính kèm file hoặc ảnh
    cleaned = re.sub(r"\[\s*Attached\s+(?:file|image):[^\]]+\]", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if estimate_tokens(cleaned) > max_tokens_limit:
        max_chars = max_tokens_limit * 3
        cleaned = cleaned[:max_chars] + "..."
    return cleaned


def _response_text(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            part if isinstance(part, str) else part.get("text", "")
            for part in content
            if isinstance(part, str) or (isinstance(part, dict) and part.get("type") == "text")
        ).strip()
    return ""


def _clean_prompt_suggestion(text: str) -> str:
    """Chuẩn hóa một gợi ý prompt từ góc nhìn người dùng, loại bỏ các từ mở đầu kiểu trợ lý hỏi người dùng."""
    if not text:
        return ""

    # Loại bỏ bullet points, số thứ tự ở đầu
    s = re.sub(r"^\s*(?:[-*•]+\s*|\d+[.)、\s-]\s*)", "", text).strip()
    s = s.strip("`\"' “”‘’")

    # Loại bỏ các mẫu câu hỏi/gợi ý từ góc nhìn trợ lý (Vietnamese)
    # Ví dụ: "Bạn có muốn mở rộng chương trình... không?" -> "Mở rộng chương trình..."
    s = re.sub(
        r"^(?:bạn có muốn|bạn có cần|bạn muốn|bạn có quan tâm (?:đến|tới)?|bạn có thích|bạn có tò mò|có muốn)\s+",
        "",
        s,
        flags=re.I,
    ).strip()
    # Loại bỏ đuôi nghi vấn kiểu trợ lý hỏi ý người dùng ở cuối câu
    s = re.sub(r"\s+(?:không|ko|chăng)\s*[?？]?$", "", s, flags=re.I).strip()

    # Loại bỏ các mẫu trợ lý hỏi người dùng (English)
    # Ví dụ: "Would you like to know how to..." -> "How to..."
    s = re.sub(
        r"^(?:would you like (?:to (?:know|learn|see|explore|find out))?|do you want (?:to (?:know|learn|see|explore|find out))?|are you interested in|would you prefer (?:to)?)\s+",
        "",
        s,
        flags=re.I,
    ).strip()

    # Viết hoa chữ cái đầu nếu bị viết thường sau khi cắt bỏ tiền tố
    if s and s[0].islower():
        s = s[0].upper() + s[1:]

    return s.strip()


# Alias for backward compatibility
_clean_question = _clean_prompt_suggestion


def _parse_questions(raw: str) -> list[str]:
    """Parse JSON or newline-separated suggested prompts from model output."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip(), flags=re.I)
    candidates: list[str] = []

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = (
                payload.get("prompts")
                or payload.get("suggestions")
                or payload.get("questions")
                or payload.get("followups", [])
            )
        if isinstance(payload, list):
            candidates.extend(item for item in payload if isinstance(item, str))
    except (json.JSONDecodeError, TypeError):
        pass

    if not candidates:
        # Split by non-empty lines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) >= 2:
            candidates = lines[:2]
        elif len(lines) == 1:
            split_candidates = re.split(r"(?<=[?？.!\n])\s+", lines[0])
            if len(split_candidates) >= 2:
                candidates = split_candidates[:2]
            else:
                candidates = lines

    suggestions = []
    for candidate in candidates:
        cleaned = _clean_prompt_suggestion(candidate)
        if cleaned and cleaned.casefold() not in {s.casefold() for s in suggestions}:
            suggestions.append(cleaned)
        if len(suggestions) == 2:
            break
    return suggestions if len(suggestions) == 2 else []


VIETNAMESE_ACCENTS = (
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
    "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ"
)


def fallback_followup_questions(user_message: str) -> list[str]:
    """Return two local user prompt suggestions when the follow-up model is unavailable."""
    text = re.sub(r"\[\s*Attached\s+(?:file|image):[^\]]+\]", " ", user_message or "", flags=re.I)
    text = re.sub(r"\s+", " ", text).strip().rstrip("?？.!。 ")

    vietnamese = any(c in VIETNAMESE_ACCENTS for c in text) or bool(re.search(
        r"\b(tôi|toi|bạn|ban|minh|cho tôi|cho toi|thời tiết|thoi tiet|như nào|nhu nao|làm sao|lam sao|giúp|giup|không|khong)\b",
        text,
        re.I,
    ))
    if vietnamese:
        topic = re.sub(
            r"^(?:cho (?:tôi|toi) (?:biết|biet|hỏi|hoi|xem)|(?:tôi|toi) muốn biết|giúp (?:tôi|toi)|giup (?:tôi|toi)|hãy|hay)\s+",
            "",
            text,
            flags=re.I,
        ).strip()
        topic = topic[:70].rstrip(" ,;:")
        if topic:
            return [
                f"Giải thích chi tiết hơn về {topic} kèm ví dụ minh họa cụ thể",
                f"Các lưu ý quan trọng và cách xử lý lỗi thường gặp khi làm việc với {topic}",
            ]
        return [
            "Giải thích chi tiết hơn về câu trả lời vừa rồi kèm ví dụ cụ thể",
            "Hướng dẫn các bước triển khai chi tiết và những lưu ý quan trọng",
        ]

    topic = re.sub(
        r"^(?:can you tell me|tell me|please explain|please|how do i|what is)\s+",
        "",
        text,
        flags=re.I,
    ).strip()[:70].rstrip(" ,;:")
    if topic:
        return [
            f"How can I apply {topic} in a real-world scenario with an example?",
            f"What are the best practices and common pitfalls for {topic}?",
        ]
    return [
        "How can I apply this in a real-world scenario with an example?",
        "What are the best practices and key considerations for this topic?",
    ]


def generate_followup_questions(
    user_message: str,
    assistant_answer: str,
    model_name: str,
) -> list[str]:
    """Generate exactly two actionable suggested user prompts, with a local fallback.

    Tách context & Kiểm soát token:
    - Loại bỏ code blocks lớn, tool outputs thô, attachments không cần thiết.
    - Giới hạn nghiêm ngặt token context (user ~250 tokens, assistant ~500 tokens).
    - Viết theo góc nhìn của người dùng (User Prompt), tuyệt đối không dùng câu hỏi từ phía trợ lý.
    """
    if not assistant_answer.strip():
        return fallback_followup_questions(user_message)

    clean_user = _clean_content_for_followup(user_message, MAX_FOLLOWUP_USER_TOKENS)
    clean_assistant = _clean_content_for_followup(assistant_answer, MAX_FOLLOWUP_ANSWER_TOKENS)

    context = {
        "user_message": clean_user,
        "assistant_answer": clean_assistant,
    }
    try:
        llm = ChatGroq(model=model_name, temperature=0.2, max_tokens=FOLLOWUP_OUTPUT_TOKENS)
        response = llm.invoke([
            SystemMessage(content=(
                "You are an expert prompt engineer for an AI chat assistant interface.\n\n"
                "Your task is to generate exactly two high-quality, actionable SUGGESTED USER PROMPTS "
                "based on the latest conversation turn. These suggestions appear as clickable quick prompt "
                "chips that the USER will send to the assistant as their next message.\n\n"
                "CRITICAL PERSPECTIVE RULES:\n"
                "- Write strictly from the USER'S POINT OF VIEW (prompts that the USER says TO the assistant).\n"
                "- NEVER write from the assistant's perspective asking the user questions.\n"
                "- ABSOLUTELY FORBIDDEN: Do not start with conversational fillers or chatbot inquiries directed at the user, such as:\n"
                "  * 'Bạn có muốn...', 'Bạn có cần...', 'Bạn muốn...', 'Bạn có quan tâm đến/tới...'\n"
                "  * 'Would you like...', 'Do you want...', 'Are you interested in...', 'Would you prefer...'\n"
                "- Suggested prompts MUST be direct requests, commands, or user inquiries, for example:\n"
                "  * 'Hướng dẫn cách mở rộng chương trình để nhận input từ người dùng'\n"
                "  * 'Cách tạo file thực thi (.exe) độc lập từ script Python này'\n"
                "  * 'Giải thích chi tiết hơn về các tham số và cú pháp ở trên'\n"
                "  * 'So sánh ưu và nhược điểm của giải pháp này với các cách tiếp cận khác'\n\n"
                "Guidelines:\n"
                "- Use the same language as the conversation (e.g. Vietnamese for Vietnamese conversations).\n"
                "- Make each prompt concise (under 16 words), natural, and immediately actionable as a prompt chip.\n"
                "- Make the two prompts distinct and complementary.\n"
                "- Do not repeat what has already been answered.\n\n"
                "Output format:\n"
                "- Return exactly two suggested prompts.\n"
                "- Put each suggested prompt on a separate line.\n"
                "- Do not include numbering, bullets, quotation marks, or explanations.\n"
                "- Return only the two suggested prompts."
            )),
            HumanMessage(content=json.dumps(context, ensure_ascii=False)),
        ])
        raw = _response_text(response)
        questions = _parse_questions(raw)
        if len(questions) == 2:
            return questions
        logger.warning("Follow-up model returned an unrecognized format; using fallback.")
    except Exception as exc:
        logger.warning("Follow-up generation failed: %s", exc)
    return fallback_followup_questions(user_message)
