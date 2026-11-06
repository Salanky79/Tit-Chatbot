"""Generate optional follow-up prompts for a completed assistant response."""

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


def generate_followup_questions(
    user_message: str,
    assistant_answer: str,
    model_name: str,
) -> list[str]:
    """Ask the selected chat model for exactly two relevant follow-up questions.

    Suggestions are optional: API, parsing, or model errors return an empty list
    and never fail the completed chat response.
    """
    # Không gọi thêm LLM nếu agent không tạo được câu trả lời.
    if not assistant_answer.strip():
        return []

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
        # Lấy text từ phản hồi model, rồi kiểm tra/chuẩn hóa thành list hai câu.
        raw = _response_text(response)

        questions = [
            line.strip().lstrip("-•0123456789.) ")
            for line in raw.splitlines()
            if line.strip()
        ]

        questions = questions[:2] if len(questions) >= 2 else []

        if len(questions) != 2:
            logger.warning("Follow-up model returned an unrecognized format.")
        return questions
    except Exception as exc:
        # Gợi ý là tính năng phụ: nếu API/model lỗi thì trả [] để không làm hỏng
        # câu trả lời chính đã được gửi cho người dùng.
        logger.warning("Follow-up generation failed: %s", exc)
        return []
