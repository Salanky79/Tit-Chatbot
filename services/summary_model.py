"""Model invocation and failover for document summaries."""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from prompts.summarization import (
    CUSTOM_LANGUAGE_RULE, ORIGINAL_LANGUAGE_RULE, SUMMARY_SYSTEM_PROMPT,
    VIETNAMESE_LANGUAGE_RULE,
)
from services.summary_result import normalize_language
from services.token_limit_handler import is_daily_token_limit, invoke_with_rate_limit_retry
from utils.usage_logging import log_token_usage

logger = logging.getLogger(__name__)
MAP_SUMMARY_OUTPUT_TOKENS = 350
FINAL_SUMMARY_OUTPUT_TOKENS = 900

def _content_text(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            item if isinstance(item, str) else item.get("text", "")
            for item in content
            if isinstance(item, str) or (isinstance(item, dict) and item.get("type") == "text")
        ).strip()
    return ""


def _invoke(
    llm: Any,
    prompt: str,
    stage: str,
    group_number: int,
    max_retries: int | None = None,
    target_language: str = "auto",
) -> str:
    """Invoke the summarization LLM for a prompt unit.

    Retry rate limit theo chính sách hiện có; nếu model lỗi hoặc hết quota ngày,
    lần lượt thử các model dự phòng đã cấu hình.
    """
    target_language = normalize_language(target_language)
    if target_language == "Vietnamese":
        lang_rule = VIETNAMESE_LANGUAGE_RULE
    elif target_language == "auto":
        lang_rule = ORIGINAL_LANGUAGE_RULE
    else:
        lang_rule = CUSTOM_LANGUAGE_RULE.format(language=target_language)

    messages = [
        SystemMessage(content=SUMMARY_SYSTEM_PROMPT + lang_rule),
        HumanMessage(content=prompt),
    ]
    operation = f"Summarization ({stage}, group {group_number})"
    candidates = [llm]
    primary_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
    if isinstance(llm, ChatGroq):
        configured = os.getenv(
            "SUMMARIZATION_FALLBACK_MODELS",
            "openai/gpt-oss-20b,qwen/qwen3.8-27b,openai/gpt-oss-120b",
        )
        fallback_names = [name.strip() for name in configured.split(",") if name.strip()]
        fallback_names = [name for name in dict.fromkeys(fallback_names) if name != primary_name]
        candidates.extend(
            _build_groq_llm(name, max_tokens=getattr(llm, "max_tokens", MAP_SUMMARY_OUTPUT_TOKENS))
            for name in fallback_names
        )

    last_error: Exception | None = None
    for index, candidate in enumerate(candidates):
        model_name = getattr(candidate, "model_name", None) or getattr(candidate, "model", "Groq-LLM")
        try:
            response = invoke_with_rate_limit_retry(
                lambda candidate=candidate: candidate.invoke(messages),
                max_retries=max_retries,
                operation=operation,
            )
        except Exception as exc:
            last_error = exc
            if index + 1 < len(candidates):
                logger.warning(
                    "[ModelFailover] %s failed on %s; trying the next configured model: %s",
                    operation,
                    model_name,
                    exc,
                )
            continue

        result = _content_text(response)
        if result:
            log_token_usage(str(model_name), operation, response)
            return result
        last_error = RuntimeError("the model returned an empty result")

    if last_error is None:
        raise RuntimeError(f"Document summarization failed during {operation}.")
    if is_daily_token_limit(last_error):
        logger.error("[RateLimit-TPD] All configured models stopped during %s: %s", operation, last_error)
        raise RuntimeError(
            f"Document summarization rate limit exceeded (Đã hết hạn mức token trong ngày): {last_error}"
        ) from last_error
    logger.error("[SummarizationError] All configured models failed during %s: %s", operation, last_error)
    raise RuntimeError(f"Document summarization failed during {operation}: {last_error}") from last_error


def _build_groq_llm(model_name: str | None = None, max_tokens: int = FINAL_SUMMARY_OUTPUT_TOKENS) -> ChatGroq:
    model = model_name or os.getenv("SUMMARIZATION_MODEL") or "openai/gpt-oss-20b"
    return ChatGroq(
        model=model,
        temperature=0.1,
        max_tokens=max_tokens,
    )


