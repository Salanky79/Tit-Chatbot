"""Shared, deliberately approximate token utilities for LLM requests."""

from __future__ import annotations

import json
import math
import os
from typing import Any, Sequence

from langchain_core.messages import BaseMessage, ToolMessage


def _float_setting(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


# This is a sizing heuristic, not a tokenizer. Keep a safety margin in the
# configured request budget rather than treating the estimate as exact.
CHARS_PER_TOKEN = _float_setting("TOKEN_ESTIMATE_CHARS_PER_TOKEN", 2.5, 0.5)
PROMPT_CHARS_PER_TOKEN = _float_setting("TOKEN_ESTIMATE_PROMPT_CHARS_PER_TOKEN", 4.0, 0.5)


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(str(text)) / CHARS_PER_TOKEN))


def estimate_prompt_tokens(text: str | None) -> int:
    """Estimate mostly-static system/tool prompt text with its own configurable ratio."""
    if not text:
        return 0
    return max(1, math.ceil(len(str(text)) / PROMPT_CHARS_PER_TOKEN))


def estimate_message_tokens(message: BaseMessage) -> int:
    """Estimate message content, tool-call arguments, and message overhead."""
    total = 4
    content = getattr(message, "content", "")

    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                total += estimate_tokens(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    total += estimate_tokens(str(text))

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        try:
            serialized = json.dumps(tool_calls, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            serialized = str(tool_calls)
        total += estimate_tokens(serialized)

    if isinstance(message, ToolMessage):
        total += 2
    return total


def _tool_schema(tool: Any) -> dict[str, Any]:
    args = getattr(tool, "args", None)
    schema = getattr(tool, "args_schema", None)
    if schema is not None and hasattr(schema, "model_json_schema"):
        try:
            args = schema.model_json_schema()
        except Exception:
            pass
    elif schema is not None and hasattr(schema, "schema"):
        try:
            args = schema.schema()
        except Exception:
            pass

    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", ""),
        "parameters": args or {},
    }


def estimate_tool_schemas_tokens(tool_list: Sequence[Any] | None) -> int:
    if not tool_list:
        return 0

    total = 0
    for tool in tool_list:
        schema = _tool_schema(tool)
        serialized = json.dumps(schema, ensure_ascii=False, default=str)
        total += estimate_prompt_tokens(serialized) + 12
    return total


def truncate_text_to_tokens(text: str, max_tokens: int, marker: str = "\n[…đã rút gọn…]\n") -> str:
    """Keep the beginning and ending of text when an approximate cap is needed."""
    if max_tokens <= 0:
        return ""
    max_chars = max(1, int(max_tokens * CHARS_PER_TOKEN))
    if len(text) <= max_chars:
        return text

    marker = marker[:max_chars]
    remaining = max(0, max_chars - len(marker))
    head_len = int(remaining * 0.7)
    tail_len = remaining - head_len
    if tail_len == 0:
        return text[:head_len]
    return f"{text[:head_len]}{marker}{text[-tail_len:]}"


