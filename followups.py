"""Generate two follow-up suggestions for each chat turn."""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq


logger = logging.getLogger(__name__)


def _response_text(response) -> str:
    # LangChain thường trả AIMessage có thuộc tính content, nhưng nội dung
    # đôi khi là chuỗi, đôi khi là danh sách các phần text. Chuẩn hóa cả hai
    # dạng về một chuỗi để bước parse JSON phía sau xử lý thống nhất.
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


def _clean_question(question: str) -> str:
    question = re.sub(r"^\s*(?:[-*•]+\s*|\d+[.)、]\s*)", "", question)
    question = question.strip().strip("`\"' “”‘’")
    if question and not question.endswith(("?", "？")):
        question += "?"
    return question


def _parse_questions(raw: str) -> list[str]:
    """Parse JSON, numbered lines, or two plain questions from model output."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip(), flags=re.I)
    candidates: list[str] = []

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("questions", payload.get("followups", []))
        if isinstance(payload, list):
            candidates.extend(item for item in payload if isinstance(item, str))
    except (json.JSONDecodeError, TypeError):
        pass

    if not candidates:
        # This also handles two questions that the model puts on one line.
        candidates = re.findall(r"[^?？\n]*[?？]", text)

    if len(candidates) < 2:
        question_lines = [
            line for line in text.splitlines()
            if line.strip().endswith(("?", "？"))
        ]
        if len(question_lines) >= 2:
            candidates = question_lines

    if len(candidates) < 2:
        # Accept exactly two non-empty lines even if punctuation was omitted.
        plain_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(plain_lines) == 2:
            candidates = plain_lines

    questions = []
    for candidate in candidates:
        question = _clean_question(candidate)
        if question and question.casefold() not in {q.casefold() for q in questions}:
            questions.append(question)
        if len(questions) == 2:
            break
    return questions if len(questions) == 2 else []


def fallback_followup_questions(user_message: str) -> list[str]:
    """Return two local suggestions when the follow-up model is unavailable."""
    text = re.sub(r"\[\s*Attached\s+(?:file|image):[^\]]+\]", " ", user_message or "", flags=re.I)
    text = re.sub(r"\s+", " ", text).strip().rstrip("?？.!。 ")

    vietnamese = bool(re.search(
        r"[À-ỹ]|\b(tôi|toi|bạn|ban|minh|cho tôi|cho toi|thời tiết|thoi tiet|như nào|nhu nao|làm sao|lam sao|giúp|giup|không|khong)\b",
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
                f"Bạn có thể giải thích cụ thể hơn về {topic} bằng một ví dụ không?",
                f"Có lưu ý hoặc trường hợp đặc biệt nào liên quan đến {topic}?",
            ]
        return [
            "Bạn có thể giải thích kỹ hơn câu trả lời vừa rồi không?",
            "Bạn có thể đưa ra một ví dụ cụ thể liên quan đến câu hỏi này không?",
        ]

    topic = re.sub(
        r"^(?:can you tell me|tell me|please explain|please|how do i|what is)\s+",
        "",
        text,
        flags=re.I,
    ).strip()[:70].rstrip(" ,;:")
    if topic:
        return [
            f"Could you explain {topic} with a concrete example?",
            f"What important details or exceptions should I know about {topic}?",
        ]
    return [
        "Could you explain the answer in more detail?",
        "Could you give a concrete example related to this question?",
    ]


def generate_followup_questions(
    user_message: str,
    assistant_answer: str,
    model_name: str,
) -> list[str]:
    """Generate exactly two relevant follow-up questions, with a local fallback."""
    if not assistant_answer.strip():
        return fallback_followup_questions(user_message)

    # Giới hạn độ dài context để lượt sinh câu hỏi không gửi lại toàn bộ lịch sử
    # dài. Giữ phần cuối vì thường chứa nội dung câu trả lời mới nhất.
    context = {
        "user_message": user_message[-4000:],
        "assistant_answer": assistant_answer[-6000:],
    }
    try:
        # Dùng cùng model chat mà người dùng đang chọn; max_tokens thấp vì chỉ
        # cần hai câu ngắn. Đây là một lượt gọi bổ sung sau câu trả lời chính.
        # Tăng giới hạn để model có đủ chỗ cho reasoning ngắn và hai câu hỏi.
        llm = ChatGroq(model=model_name, temperature=0.2, max_tokens=512)
        response = llm.invoke([
            SystemMessage(content=(
                "You are an expert follow-up question generator for a helpful, "
                "professional AI assistant.\n\n"

                "Your task is to generate exactly two high-quality follow-up "
                "questions based on the user's latest message and the assistant's "
                "response.\n\n"

                "Guidelines:\n"
                "- Make each question relevant to the conversation and useful "
                "for exploring the topic further.\n"
                "- Prefer specific, insightful questions over generic suggestions.\n"
                "- Keep each question concise, natural, and easy to understand.\n"
                "- Use the same language as the conversation.\n"
                "- Make the two questions distinct and complementary.\n"
                "- Consider the user's likely next step, such as understanding a "
                "concept more deeply, seeing a practical example, comparing "
                "alternatives, or solving a related problem.\n"
                "- Do not ask for information the user has already provided.\n"
                "- Do not repeat questions that have already been answered.\n"
                "- Do not begin with conversational filler such as "
                "'Bạn có muốn...', 'Bạn có cần...', 'Bạn muốn biết thêm...', "
                "'Would you like...', or 'Do you want...'.\n"
                "- Phrase questions directly and naturally, as suggested prompts "
                "in a modern AI chatbot interface.\n\n"

                "Output requirements:\n"
                "- Return exactly two questions.\n"
                "- Put each question on a separate line.\n"
                "- Do not include numbering, bullets, introductions, explanations, "
                "or quotation marks.\n"
                "- Return only the two questions."
            )),
            # Đóng gói câu hỏi và câu trả lời vừa tạo thành context JSON.
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
