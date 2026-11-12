"""Quản lý ngữ cảnh hội thoại: Giữ các lượt chat gần nhất và loại bỏ tool protocol cũ."""

from __future__ import annotations

import os
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


# Giữ lại số lượt trò chuyện gần nhất (mặc định 10 lượt, thoải mái trong context window lớn của LLM)
DEFAULT_MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))


def group_messages_into_turns(messages: Sequence[BaseMessage]) -> list[list[BaseMessage]]:
    """Gom các tin nhắn theo từng lượt hỏi-đáp bắt đầu từ HumanMessage."""
    turns: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, HumanMessage) and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns


def prune_old_tool_results(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Loại bỏ chi tiết ToolMessage trung gian của các lượt cũ đã hoàn thành,
    chỉ giữ lại toàn bộ tool message của lượt đang hoạt động (active turn).
    """
    if not messages:
        return []

    last_human_index = next(
        (index for index in range(len(messages) - 1, -1, -1)
         if isinstance(messages[index], HumanMessage)),
        -1,
    )
    if last_human_index < 0:
        return list(messages)

    past = messages[:last_human_index]
    active = messages[last_human_index:]
    cleaned_past: list[BaseMessage] = []

    for message in past:
        if isinstance(message, ToolMessage):
            continue
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            content = getattr(message, "content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            cleaned_past.append(AIMessage(content=content))
            continue
        cleaned_past.append(message)

    return cleaned_past + list(active)


def get_model_token_budget(model_name: str | None = None) -> dict[str, int]:
    """Hàm tương thích cho các module khác nếu có gọi."""
    return {
        "max_context_tokens": 128000,
        "max_tool_result_tokens": 8000,
        "expected_output_tokens": 2048,
    }


def prepare_context_for_llm(
    messages: Sequence[BaseMessage],
    system_prompt: str,
    tools: Sequence[Any] | None = None,
    existing_summary: str = "",
    model_name: str | None = None,
    max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS,
    **kwargs: Any,
) -> tuple[list[BaseMessage], str]:
    """Chuẩn bị ngữ cảnh cho LLM:
    - Loại bỏ kết quả tool trung gian của các lượt cũ.
    - Giữ lại các lượt chat gần nhất theo max_history_turns (mặc định 10 lượt).
    - Ghép SystemMessage ở đầu.
    - Hoàn toàn KHÔNG tính toán giới hạn token nhân tạo hay ném lỗi ContextBudgetError.
    """
    cleaned_messages = prune_old_tool_results(messages)

    # Giữ lại số lượt chat gần nhất nếu vượt quá max_history_turns
    if max_history_turns > 0:
        turns = group_messages_into_turns(cleaned_messages)
        if len(turns) > max_history_turns:
            turns = turns[-max_history_turns:]
            cleaned_messages = [msg for turn in turns for msg in turn]

    full_system = system_prompt.strip()
    if existing_summary.strip():
        full_system += (
            "\n\n[Earlier conversation notes]\n"
            f"{existing_summary.strip()}\n"
            "[End earlier conversation notes]\n"
            "Use these notes only when relevant to the latest request."
        )

    return [SystemMessage(content=full_system), *cleaned_messages], existing_summary
