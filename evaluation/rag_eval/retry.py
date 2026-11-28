"""Retry transient model/API errors while honoring provider retry-after hints."""

from __future__ import annotations

import config as cfg
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")



# =============================================================================
# Đọc thời gian chờ từ lỗi
# =============================================================================

def _parse_retry_hint(message: str) -> float | None:
    lower = message.casefold()
    marker = "try again in "
    if marker in lower:
        idx = lower.find(marker) + len(marker)
        chunk = lower[idx:].strip()
        num_chars = []
        for c in chunk:
            if c.isdigit() or c == ".":
                num_chars.append(c)
            elif c in ("s", " ", ","):
                break
            else:
                break
        if num_chars:
            try:
                return float("".join(num_chars))
            except ValueError:
                return None
    return None



# =============================================================================
# Retry có giới hạn cho bước đánh giá
# =============================================================================

def retry_with_backoff(
    operation: Callable[[], T],
    *,
    max_attempts: int = cfg.EVAL_ATTEMPTS,
    max_wait_seconds: float = cfg.EVAL_MAX_WAIT_SECONDS,
    sleep: Callable[[float], Any] = time.sleep,
) -> T:
    """Retry failures with exponential backoff or a provider-specified delay."""
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= max_attempts:
                break
            hint_val = _parse_retry_hint(str(exc))
            if hint_val is not None:
                hint = hint_val + cfg.EVAL_RETRY_MARGIN_SECONDS
                wait_seconds = max(hint, float(2 ** attempt))
            else:
                wait_seconds = float(2 ** attempt)
            sleep(min(wait_seconds, max_wait_seconds))
    assert last_error is not None
    raise last_error
