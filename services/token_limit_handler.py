"""Centralized handler for token limits and graceful partial recovery.

Xử lý tập trung các tình huống khi LLM chạm ngưỡng giới hạn token trong ngày (TPD):
- Không ném exception hoặc vứt lỗi đỏ ra màn hình.
- Thu thập và in toàn bộ kết quả đang làm dở (Partial Result).
- Gắn thêm thông báo lịch sự, rõ ràng để người dùng vẫn sử dụng được dữ liệu đã sinh ra.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import math
import os
import random
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence, TypeVar
from services.language import get_current_language, normalize_language, tr

logger = logging.getLogger(__name__)
T = TypeVar("T")



# Đọc cấu hình số thực từ biến môi trường với giá trị mặc định và ngưỡng tối thiểu.
def _float_setting(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value >= minimum else default


# Các hằng số cấu hình thời gian chờ tối đa và exponential backoff khi bị rate limit.
MAX_RATE_LIMIT_WAIT_SECONDS = _float_setting("MAX_RATE_LIMIT_WAIT_SECONDS", 60.0, 1.0)
RATE_LIMIT_BACKOFF_BASE_SECONDS = _float_setting("RATE_LIMIT_BACKOFF_BASE_SECONDS", 1.0, 0.1)
RATE_LIMIT_BACKOFF_JITTER_SECONDS = _float_setting("RATE_LIMIT_BACKOFF_JITTER_SECONDS", 0.5, 0.0)


# Đóng gói quyết định retry bất biến (hành động, số giây chờ, lý do, nguồn dữ liệu).
@dataclass(frozen=True)
class RetryDecision:
    """One normalized decision for a failed provider request."""

    action: Literal[
        "fail_fast_tpd",
        "retry",
        "exceeded_max_retries",
        "exceeded_max_wait",
        "not_rate_limit",
    ]
    wait_seconds: float | None
    reason: str
    source: Literal[
        "retry-after",
        "x-ratelimit-reset-tokens",
        "backoff",
        "none",
    ]

# Duyệt đệ quy chuỗi exception (__cause__, __context__) an toàn, chống lặp vô hạn.
def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    """Walk wrapped exceptions safely, including unusual cyclic cause/context links."""
    pending = [error]
    seen_ids: set[int] = set()
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen_ids:
            continue
        seen_ids.add(current_id)
        yield current
        # Thêm __context__ và __cause__ vào ngăn xếp để duyệt tiếp
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)


# Gom và chuẩn hóa toàn bộ chuỗi thông báo lỗi thành chữ thường để nhận diện từ khóa.
def _exception_text(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        return " ".join(str(item) for item in _exception_chain(error)).lower()
    return str(error).lower()


# Trích xuất danh sách headers từ tất cả response hoặc exception con trong chuỗi lỗi.
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


# Lấy giá trị header theo tên không phân biệt hoa thường và loại bỏ khoảng trắng thừa.
def _header_value(headers: Mapping[str, Any], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == target and value is not None:
            return str(value).strip()
    return None


# Đổi chuỗi thời gian của provider (vd: '7.66s', '1m30.5s') sang số giây kiểu float.
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


# Đổi giá trị header Retry-After (dạng số giây hoặc chuẩn HTTP-date) sang số giây chờ.
def _parse_retry_after(value: str, server_date: str | None) -> float | None:
    clean_value = value.strip()
    # Nếu là số giây thuần (ví dụ: '5')
    try:
        seconds = float(clean_value)
    except ValueError:
        seconds = None
    if seconds is not None:
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    # Nếu là định dạng ngày tháng HTTP-date (RFC 7231)
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


# Tìm thời gian chờ từ header Retry-After hoặc x-ratelimit-reset-tokens trong chuỗi lỗi.
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


# Trích xuất thời gian chờ (delay) và tên nguồn từ HTTP headers của exception nếu có.
def extract_retry_delay(error: BaseException | str) -> tuple[float, str] | None:
    """Return a delay from structured HTTP headers, if the exception exposes one."""
    if isinstance(error, BaseException):
        return _header_retry_delay(error)
    return None


# Kiểm tra xem lỗi có chứa từ khóa tường minh về giới hạn token ngày (TPD) hay không.
def _has_daily_limit_marker(error: BaseException | str) -> bool:
    text = _exception_text(error)
    return any(marker in text for marker in (
        "tokens per day", "tpd", "daily", "per day", "quota_exceeded", "insufficient_quota"
    ))


# Nhận diện lỗi rate limit qua HTTP status 429 hoặc các từ khóa rate limit phổ biến.
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


# Phân tích lỗi và đưa ra quyết định có retry hay không, thời gian chờ và nguồn tính toán.
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
    # 1. Nếu là lỗi hết hạn mức ngày (TPD) thì dừng ngay lập tức (fail-fast).
    if _has_daily_limit_marker(error):
        return RetryDecision("fail_fast_tpd", None, "Daily token quota exhausted.", "none")

    delay = extract_retry_delay(error)
    # 2. Nếu không phải lỗi rate limit (429/TPM) thì không retry.
    if not _is_rate_limit_exception(error):
        return RetryDecision("not_rate_limit", None, "Error is not a rate limit.", "none")

    retries_used = max(0, attempt - 1)
    # 3. Nếu đã vượt quá số lần retry tối đa cho phép thì dừng.
    if max_retries is not None and retries_used >= max(0, max_retries):
        return RetryDecision(
            "exceeded_max_retries", None,
            f"Retry limit ({max_retries}) reached after {retries_used} retries.",
            delay[1] if delay else "none",
        )

    # 4. Tính toán thời gian chờ: ưu tiên header, nếu không có thì exponential backoff kèm jitter.
    if delay is None:
        exponent = max(0, attempt - 1)
        wait_seconds = min(RATE_LIMIT_BACKOFF_BASE_SECONDS * (2 ** min(exponent, 20)), max_wait_seconds)
        wait_seconds = min(
            max_wait_seconds,
            wait_seconds + random.uniform(0.0, RATE_LIMIT_BACKOFF_JITTER_SECONDS),
        )
        source = "backoff"
    else:
        wait_seconds, source = delay

    # 5. Đảm bảo thời gian chờ tối thiểu 0.25s để tránh vòng lặp retry quá nhanh.
    wait_seconds = max(0.25, wait_seconds)
    # 6. Nếu thời gian chờ yêu cầu vượt ngưỡng cho phép thì không chờ tiếp.
    if max_retries is not None and wait_seconds > max_wait_seconds:
        return RetryDecision(
            "exceeded_max_wait", wait_seconds,
            f"Server requested {wait_seconds:.2f}s, above configured maximum {max_wait_seconds:.2f}s.",
            source,
        )
    # 7. Cho phép retry với thời gian chờ đã xác định.
    return RetryDecision("retry", wait_seconds, "Transient rate limit; retry is allowed.", source)


# Tạm dừng luồng theo từng khoảng ngắn (chunk) để chờ retry an toàn.
def wait_for_retry(wait_seconds: float, *, max_sleep_chunk_seconds: float = MAX_RATE_LIMIT_WAIT_SECONDS) -> None:
    """Wait the full requested delay in chunks; chunks avoid one opaque long sleep."""
    remaining = max(0.0, wait_seconds)
    while remaining > 0:
        chunk = min(remaining, max_sleep_chunk_seconds)
        time.sleep(chunk)
        remaining -= chunk


# Thực thi hàm sync với cơ chế tự động retry khi gặp lỗi rate limit (dùng time.sleep).
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
            # Không retry nếu output stream đã bắt đầu gửi token về client
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


# Thực thi hàm async với cơ chế tự động retry non-blocking khi gặp rate limit (dùng asyncio.sleep).
async def ainvoke_with_rate_limit_retry(
    invoke: Callable[[], Awaitable[T]],
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
    language: str = "vi",
) -> str:
    """Xử lý câu trả lời Stream chat đang làm dở khi chạm trần token ngày.

    - Nếu đã có accumulated_text -> giữ nguyên và gắn thông báo đuôi.
    - Nếu accumulated_text rỗng -> trả về câu thông báo lịch sự hướng dẫn chọn model khác.
    """
    clean_accumulated = (accumulated_text or "").strip()

    if clean_accumulated:
        return f"{clean_accumulated}{tr('daily_stream_partial', language, model=model_name)}"

    return tr("daily_stream_empty", language, model=model_name)


# Ghép các phần tóm tắt đã hoàn thành thành một bản tóm tắt bán phần (Partial Draft) hoàn chỉnh.
def assemble_partial_summary(
    completed_groups: Sequence[tuple[int, str]],
    total_groups: int,
    filename: str = "",
    language: str = "auto",
    notice_language: str | None = None,
) -> str:
    """Ghép các phần tóm tắt đã hoàn thành thành một bản tóm tắt bán phần (Partial Draft) hoàn chỉnh.

    Bắt đầu bằng tiền tố hợp lệ để agent.py trả thẳng kết quả cho người dùng qua emit_summary.
    """
    summary_language = normalize_language(language) or get_current_language()
    user_language = normalize_language(notice_language) or get_current_language()
    safe_filename = filename.strip() or tr("daily_summary_empty", summary_language)

    # Sắp xếp theo đúng thứ tự nhóm ban đầu (1-based index)
    sorted_groups = sorted(completed_groups, key=lambda item: item[0])
    completed_count = len(sorted_groups)

    prefix = tr("daily_summary_prefix", summary_language, filename=safe_filename,
                completed=completed_count, total=total_groups)
    body = "\n\n".join(
        tr("daily_summary_section", summary_language, index=idx, total=total_groups,
           text=summary_text.strip())
        for idx, summary_text in sorted_groups
    )
    footer = tr("daily_summary_footer", user_language, completed=completed_count, total=total_groups)
    return f"{prefix}{body}{footer}"


# Tạo câu trả lời cứu trợ thân thiện khi các tool (Vision, RAG Search) gặp giới hạn ngày.
def format_tool_limit_notice(
    tool_name: str,
    partial_info: str = "",
    language: str = "vi",
) -> str:
    """Tạo câu trả lời cứu trợ thân thiện khi các tool (Vision, RAG Search) gặp giới hạn ngày."""
    clean_partial = partial_info.strip()
    msg = tr("daily_tool_notice", language, tool=tool_name)
    if clean_partial:
        return msg + tr("daily_tool_partial", language, partial=clean_partial)
    return msg + tr("daily_tool_retry", language)
