"""Centralized handler for token limits and graceful partial recovery.

Xử lý tập trung các tình huống khi LLM chạm ngưỡng giới hạn token trong ngày (TPD):
- Không ném exception hoặc vứt lỗi đỏ ra màn hình.
- Thu thập và in toàn bộ kết quả đang làm dở (Partial Result).
- Gắn thêm thông báo lịch sự, rõ ràng để người dùng vẫn sử dụng được dữ liệu đã sinh ra.
"""

from __future__ import annotations

import config as cfg
from config import (MAX_RATE_LIMIT_WAIT_SECONDS, RATE_LIMIT_BACKOFF_BASE_SECONDS, RATE_LIMIT_BACKOFF_JITTER_SECONDS)

import asyncio
import email.utils
import logging
import math
import random
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence, TypeVar

from services.language import is_vietnamese

logger = logging.getLogger(__name__)
T = TypeVar("T")



# =============================================================================
# 02. Quyết định retry và phân tích lỗi API
# =============================================================================

@dataclass(frozen=True) # frozen để đảm bảo tính bất biến của quyết định retry, tránh thay đổi ngoài ý muốn
class RetryDecision:
    """One normalized decision for a failed provider request."""

    action: Literal[ # hành động quyết định sau khi phân loại lỗi
        "fail_fast_tpd",
        "retry",
        "exceeded_max_retries",
        "exceeded_max_wait",
        "not_rate_limit",
    ]
    wait_seconds: float | None # số giây chờ trước khi retry, None nếu không retry
    reason: str # lý do quyết định, dùng để log hoặc debug
    source: Literal[ # nguồn thông tin quyết định retry, dùng để log hoặc debug
        "retry-after",
        "x-ratelimit-reset-tokens",
        "backoff",
        "none",
    ]

# dò tìm header và chống lặp vô hạn
def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    """Walk wrapped exceptions safely, including unusual cyclic cause/context links."""
    pending = [error]
    seen_ids: set[int] = set()
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen_ids: # tránh lặp vô hạn nếu có vòng lặp trong __cause__ hoặc __context__
            continue
        seen_ids.add(current_id)
        yield current
        # Prefer explicit cause while still checking context for provider details.
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)

# Normalize exception text for explicit quota/rate-limit markers and logging.
def _exception_text(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        return " ".join(str(item) for item in _exception_chain(error)).lower()
    return str(error).lower()

# trích xuất tất cả header từ chuỗi exception, bao gồm cả header trực tiếp và header trong response nếu có
def _response_headers(error: BaseException) -> list[Mapping[str, Any]]:
    headers_list: list[Mapping[str, Any]] = []
    for item in _exception_chain(error):
        direct_headers = getattr(item, "headers", None)
        response = getattr(item, "response", None)
        response_headers = getattr(response, "headers", None) if response is not None else None
        for headers in (response_headers, direct_headers):
            if isinstance(headers, Mapping):
                headers_list.append(headers)
    return headers_list

# trích xuất giá trị header từ mapping, không phân biệt chữ hoa/chữ thường, bỏ qua giá trị None
def _header_value(headers: Mapping[str, Any], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == target and value is not None:
            return str(value).strip()
    return None

# Parse duration strings like "7.66s" or "1m30.5s" into seconds.

# =============================================================================
# 03. Phân tích thời lượng và header Retry-After
# =============================================================================

def _parse_duration(value: str) -> float | None:
    """Parse structured provider durations such as 7.66s or 1m30.5s."""
    clean = value.strip().lower()
    if not clean.endswith("s"):
        return None

    number = clean[:-1].strip()
    try:
        if "m" in number:
            if number.count("m") != 1:
                return None
            minutes_text, seconds_text = number.split("m", 1)
            total_seconds = float(minutes_text.strip()) * 60.0 + float(seconds_text.strip())
        else:
            total_seconds = float(number)
    except ValueError:
        return None
    return total_seconds if math.isfinite(total_seconds) and total_seconds >= 0 else None

# Parse Retry-After header values, which can be either a number of seconds or an HTTP date.
def _parse_retry_after(value: str, server_date: str | None) -> float | None:
    clean_value = value.strip()
    # Nếu là số giây thuần (ví dụ: '5')
    try:
        seconds = float(clean_value)
    except ValueError:
        seconds = None
    if seconds is not None:
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    try:
        target = email.utils.parsedate_to_datetime(clean_value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        base = None
        if server_date:
            base = email.utils.parsedate_to_datetime(server_date)
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
        if base is None:
            base = datetime.now(timezone.utc)
        return max(0.0, (target - base).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _header_retry_delay(error: BaseException) -> tuple[float, str] | None:
    for headers in _response_headers(error):
        retry_after = _header_value(headers, "Retry-After")
        if retry_after is not None:
            server_date = _header_value(headers, "Date")
            seconds = _parse_retry_after(retry_after, server_date)
            if seconds is not None:
                return seconds, "retry-after"

        reset_tokens = _header_value(headers, "x-ratelimit-reset-tokens")
        if reset_tokens is not None:
            seconds = _parse_duration(reset_tokens)
            if seconds is not None:
                return seconds, "x-ratelimit-reset-tokens"
    return None


def extract_retry_delay(error: BaseException | str) -> tuple[float, str] | None:
    """Return a delay from structured HTTP headers, if the exception exposes one."""
    if isinstance(error, BaseException):
        return _header_retry_delay(error)
    return None



# =============================================================================
# 04. Phân loại giới hạn ngày và rate limit
# =============================================================================

def _has_daily_limit_marker(error: BaseException | str) -> bool:
    text = _exception_text(error)
    return any(marker in text for marker in (
        "tokens per day", "tpd", "daily", "per day", "quota_exceeded", "insufficient_quota"
    ))


def _is_rate_limit_exception(error: BaseException | str) -> bool:
    text = _exception_text(error)
    if any(marker in text for marker in (
        "429", "rate_limit_exceeded", "tokens per minute", "tokens per day",
        "tpm", "tpd", "rate limit", "ratelimit",
    )):
        return True
    return isinstance(error, BaseException) and any(
        getattr(item, "status_code", None) == 429
        or getattr(getattr(item, "response", None), "status_code", None) == 429
        for item in _exception_chain(error)
    )



# =============================================================================
# 05. Chính sách retry và cách chờ
# =============================================================================

def decide_rate_limit_retry(
    error: BaseException | str,
    *,
    attempt: int,
    max_retries: int | None,
    max_wait_seconds: float = MAX_RATE_LIMIT_WAIT_SECONDS,
) -> RetryDecision:
    """Classify a failed call and choose a bounded or unlimited retry action.

    ``attempt`` is one-based. ``max_retries`` counts retries after the initial call;
    ``None`` intentionally means retry without an attempt limit.
    """
    # 1. Nếu là TPD (Hết hạn ngày) -> FAIL-FAST NGAY LẬP TỨC
    if _has_daily_limit_marker(error):
        return RetryDecision("fail_fast_tpd", None, "Daily token quota exhausted.", "none")

    delay = extract_retry_delay(error)
    if not _is_rate_limit_exception(error):
        return RetryDecision("not_rate_limit", None, "Error is not a rate limit.", "none")

    retries_used = max(0, attempt - 1)
    if max_retries is not None and retries_used >= max(0, max_retries):
        return RetryDecision(
            "exceeded_max_retries", None,
            f"Retry limit ({max_retries}) reached after {retries_used} retries.",
            delay[1] if delay else "none",
        )

    if delay is None:
        exponent = max(0, attempt - 1)
        wait_seconds = min(RATE_LIMIT_BACKOFF_BASE_SECONDS * (2 ** min(exponent, cfg.MAX_BACKOFF_EXPONENT)), max_wait_seconds)
        wait_seconds = min(
            max_wait_seconds,
            wait_seconds + random.uniform(0.0, RATE_LIMIT_BACKOFF_JITTER_SECONDS),
        )
        source = "backoff"
    else:
        wait_seconds, source = delay

    # Avoid a hot retry loop if a provider sends a zero-second hint.
    wait_seconds = max(cfg.MIN_RETRY_WAIT_SECONDS, wait_seconds)
    if max_retries is not None and wait_seconds > max_wait_seconds:
        return RetryDecision(
            "exceeded_max_wait", wait_seconds,
            f"Server requested {wait_seconds:.2f}s, above configured maximum {max_wait_seconds:.2f}s.",
            source,
        )
    return RetryDecision("retry", wait_seconds, "Transient rate limit; retry is allowed.", source)


def wait_for_retry(wait_seconds: float, *, max_sleep_chunk_seconds: float = MAX_RATE_LIMIT_WAIT_SECONDS) -> None:
    """Wait the full requested delay in chunks; chunks avoid one opaque long sleep."""
    remaining = max(0.0, wait_seconds)
    while remaining > 0:
        chunk = min(remaining, max_sleep_chunk_seconds)
        time.sleep(chunk)
        remaining -= chunk



# =============================================================================
# 06. Wrapper retry đồng bộ và bất đồng bộ
# =============================================================================

def invoke_with_rate_limit_retry(
    invoke: Callable[[], T],
    *,
    max_retries: int | None,
    operation: str,
    can_retry: Callable[[], bool] | None = None,
) -> T:
    """Run a synchronous model call with the shared rate-limit policy."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return invoke()
        except Exception as exc:
            if can_retry is not None and not can_retry():
                logger.warning("[%s] Skipping retry because output has already started.", operation)
                raise
            decision = decide_rate_limit_retry(exc, attempt=attempt, max_retries=max_retries)
            if decision.action == "not_rate_limit":
                raise
            if decision.action != "retry":
                logger.warning("[%s] %s: %s", operation, decision.action, decision.reason)
                raise
            logger.warning(
                "[%s] Rate limited (source=%s, attempt=%d, retries=%s); waiting %.2fs",
                operation, decision.source, attempt,
                "unlimited" if max_retries is None else max_retries,
                decision.wait_seconds or 0.0,
            )
            wait_for_retry(decision.wait_seconds or 0.0)


async def ainvoke_with_rate_limit_retry(
    invoke: Callable[[], Awaitable[T]], #type hint
    *,
    max_retries: int | None,
    operation: str,
) -> T:
    """Async counterpart to :func:`invoke_with_rate_limit_retry`."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await invoke()
        except Exception as exc:
            decision = decide_rate_limit_retry(exc, attempt=attempt, max_retries=max_retries)
            if decision.action == "not_rate_limit":
                raise
            if decision.action != "retry":
                logger.warning("[%s] %s: %s", operation, decision.action, decision.reason)
                raise
            logger.warning(
                "[%s] Rate limited (source=%s, attempt=%d, retries=%s); waiting %.2fs",
                operation, decision.source, attempt,
                "unlimited" if max_retries is None else max_retries,
                decision.wait_seconds or 0.0,
            )
            await asyncio.sleep(decision.wait_seconds or 0.0)



# =============================================================================
# 07. Thông báo giới hạn và kết quả dang dở
# =============================================================================

def format_tool_display_name(tool_name: str) -> str:
    """Format technical tool identifiers (snake_case) into a clean, human-readable display string.

    Tự động chuẩn hóa mọi tên tool (ví dụ 'analyze_uploaded_image' -> 'Analyze Uploaded Image'),
    không cần duy trì bất kỳ từ điển hardcode nào, hỗ trợ không giới hạn số lượng công cụ.
    """
    clean = (tool_name or "").strip().replace("_", " ").strip()
    return clean.title() if clean else "Tool"


# Ngoại lệ mang theo các nhóm tóm tắt đã hoàn thành khi chạm trần token ngày.
class DailyTokenLimitInterrupted(Exception):
    """Exception mang theo kết quả của các nhóm đã hoàn thành trước khi chạm trần token ngày."""

    def __init__(
        self,
        completed_groups: list[tuple[int, str]],
        total_groups: int,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(str(original_error or "Daily token limit reached"))
        self.completed_groups = completed_groups
        self.total_groups = total_groups
        self.original_error = original_error


# Kiểm tra xem ngoại lệ có phải do chạm trần token trong ngày (TPD / daily quota) hay không.
def is_daily_token_limit(exc: Exception | str) -> bool:
    """Kiểm tra xem lỗi có phải do chạm trần token trong ngày (TPD / daily quota) hay không.

    Chỉ dựa trên thông báo quota ngày rõ ràng; không suy luận từ thời gian trong message.
    """
    return _has_daily_limit_marker(exc)


# Kiểm tra nhanh xem ngoại lệ có phải là lỗi rate limit từ Groq hay không.
def is_rate_limit(exc: Exception | str) -> bool:
    """Kiểm tra xem lỗi có phải là bất kỳ dạng rate limit nào từ Groq (TPM, RPM, TPD)."""
    return _is_rate_limit_exception(exc)


# Xử lý câu trả lời Stream chat dở dang kèm thông báo lịch sự khi chạm trần token ngày.
def handle_partial_stream(
    accumulated_text: str,
    model_name: str,
    language: str | None = None,
) -> str:
    """Xử lý câu trả lời Stream chat đang làm dở khi chạm trần token ngày.

    - Tự động lấy ngôn ngữ từ ContextVar của request nếu không truyền tham số language.
    - Nếu đã có accumulated_text -> giữ nguyên và gắn thông báo đuôi.
    - Nếu accumulated_text rỗng -> trả về câu thông báo lịch sự hướng dẫn chọn model khác.
    """
    is_vi = is_vietnamese(language)
    clean_accumulated = (accumulated_text or "").strip()

    if clean_accumulated:
        if is_vi:
            notice = (
                f"\n\n---\n"
                f"*⚠️ **Thông báo gián đoạn:** Câu trả lời tạm dừng do mô hình `{model_name}` "
                f"đã đạt hạn mức token trong ngày (TPD limit) của Groq API. "
                f"Phần nội dung phía trên là kết quả đã được xử lý thành công. "
                f"Bạn có thể chọn mô hình khác ở danh sách góc dưới để tiếp tục trò chuyện.*"
            )
        else:
            notice = (
                f"\n\n---\n"
                f"*⚠️ **Notice:** The response was interrupted because model `{model_name}` "
                f"reached its daily token limit (TPD limit) on Groq API. "
                f"The content above is what was successfully generated. "
                f"You can switch to another model from the bottom selector to continue.*"
            )
        return f"{clean_accumulated}{notice}"

    # Trường hợp chưa sinh được chữ nào
    if is_vi:
        return (
            f"*⚠️ **Thông báo:** Mô hình `{model_name}` hiện tại đã đạt giới hạn hạn mức token trong ngày "
            f"(Daily Token Limit) của Groq API. "
            f"Bạn vui lòng chuyển sang mô hình khác (ví dụ: Qwen 3.8 27B hoặc GPT-OSS 120B) ở góc dưới "
            f"để tiếp tục trò chuyện nhé!*"
        )
    return (
        f"*⚠️ **Notice:** Model `{model_name}` has reached its daily token limit on Groq API. "
        f"Please select another model from the bottom selector to continue chatting!*"
    )


# Ghép các phần tóm tắt đã hoàn thành thành một bản tóm tắt bán phần (Partial Draft) hoàn chỉnh.
def assemble_partial_summary(
    completed_groups: Sequence[tuple[int, str]], total_groups: int, filename: str = "",
    language: str | None = None, *, stage: str = "map", map_count: int = 0,
) -> str:
    """Describe source coverage separately from the pending synthesis."""
    vi = language == "Vietnamese" or is_vietnamese(None if language == "auto" else language)
    if stage.startswith("reduce:") or stage == "final":
        progress = (f"Đã đọc {map_count}/{map_count} nhóm, còn bước tổng hợp"
                    if vi else f"Read {map_count}/{map_count} groups, synthesis pending")
    else:
        done = len(completed_groups)
        progress = f"Đã xử lý {done}/{total_groups} nhóm" if vi else f"Processed {done}/{total_groups} groups"
    title = "Bản tóm tắt của" if vi else "Summary of"
    parts = [f'{title} "{filename}" ({progress})']
    section = "Phần" if vi else "Section"
    parts.extend(f"### {section} {i}/{total_groups}\n{text}" for i, text in sorted(completed_groups))
    parts.append("Đã hết hạn mức token trong ngày. Bạn có thể đổi model rồi yêu cầu tiếp tục."
                 if vi else "Daily token quota reached. Switch models or resume when quota is available.")
    return "\n\n".join(parts)


def format_tool_limit_notice(
    tool_name: str,
    partial_info: str = "",
    language: str | None = None,
) -> str:
    """Tạo câu trả lời cứu trợ thân thiện khi các tool (Vision, RAG Search) gặp giới hạn ngày."""
    is_vi = is_vietnamese(language)
    clean_partial = partial_info.strip()
    display_tool = format_tool_display_name(tool_name)

    if is_vi:
        msg = (
            f"⚠️ Tính năng `{display_tool}` tạm thời không thể hoàn thành do mô hình "
            f"đã đạt giới hạn hạn mức token trong ngày của Groq API. "
        )
        if clean_partial:
            msg += f"\n\nDưới đây là thông tin thu thập được trước khi gián đoạn:\n{clean_partial}"
        else:
            msg += "\n\nBạn vui lòng thử lại sau hoặc chọn mô hình khác để tiếp tục."
        return msg

    msg = (
        f"⚠️ Feature `{display_tool}` could not complete because the model "
        f"reached its daily token limit on Groq API. "
    )
    if clean_partial:
        msg += f"\n\nHere is the information gathered before interruption:\n{clean_partial}"
    else:
        msg += "\n\nPlease try again later or switch to another model."
    return msg
