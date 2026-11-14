"""Retry transient model/API errors while honoring provider retry-after hints."""

from __future__ import annotations

import re
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")
_RETRY_AFTER = re.compile(r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)


def retry_with_backoff(
    operation: Callable[[], T],
    *,
    max_attempts: int = 8,
    max_wait_seconds: float = 65.0,
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
            message = str(exc)
            match = _RETRY_AFTER.search(message)
            if match:
                hint = float(match.group(1)) + 0.5
                wait_seconds = max(hint, float(2 ** attempt))
            else:
                wait_seconds = float(2 ** attempt)
            sleep(min(wait_seconds, max_wait_seconds))
    assert last_error is not None
    raise last_error
