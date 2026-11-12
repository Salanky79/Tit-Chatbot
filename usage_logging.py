"""Provider token-usage extraction and per-call logging."""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


def extract_token_usage(response: Any) -> dict[str, int]:
    if response is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        prompt = int(usage.get("input_tokens") or 0)
        completion = int(usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}

    response_metadata = getattr(response, "response_metadata", {}) or {}
    token_usage = response_metadata.get("token_usage", {}) or {}
    if isinstance(token_usage, dict) and token_usage:
        prompt = int(token_usage.get("prompt_tokens") or 0)
        completion = int(token_usage.get("completion_tokens") or 0)
        total = int(token_usage.get("total_tokens") or (prompt + completion))
        return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}

    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def log_token_usage(model_name: str, task: str, response: Any) -> dict[str, int]:
    usage = extract_token_usage(response)
    logger.info(
        "[LLMUsage] model=%s task=%s prompt_tokens=%d completion_tokens=%d total_tokens=%d",
        model_name,
        task,
        usage["prompt_tokens"],
        usage["completion_tokens"],
        usage["total_tokens"],
    )
    return usage
