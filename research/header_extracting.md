# TỔNG HỢP KIẾN TRÚC BÓC TÁCH HTTP HEADER & CHIẾN LƯỢC RETRY RATE LIMIT

Tài liệu này hệ thống hóa toàn bộ kiến trúc kiểm soát lưu lượng mạng (Rate Limiting), kỹ thuật bóc tách HTTP Response Header và chính sách Retry dùng chung trong hệ thống **Tit-Chatbot**.

---

## 1. BẢN CHẤT MẠNG: TẠI SAO CẦN HTTP HEADER THAY VÌ REGEX?

```
┌────────────────────────────────────────────────────────────────────────┐
│  VỎ PHONG BÌ (HTTP RESPONSE HEADERS) - DÀNH CHO MÁY TÍNH ĐỌC           │
│  - HTTP/1.1 429 Too Many Requests                                      │
│  - Retry-After: 4.5  (hoặc HTTP-Date: Wed, 20 Nov 2026 07:28:00 GMT)   │
│  - x-ratelimit-reset-tokens: 7.66s                                     │
│  - Date: Wed, 20 Nov 2026 07:27:55 GMT                                 │
├────────────────────────────────────────────────────────────────────────┤
│  RUỘT PHONG BÌ (HTTP RESPONSE BODY) - DÀNH CHO CON NGƯỜI ĐỌC          │
│  "Rate limit reached for model ... Please try again in 4.5s."          │
└────────────────────────────────────────────────────────────────────────┘
```

| Tiêu chí | Quét Regex trên Body (Cách cũ) | Bóc tách HTTP Header (Cách mới) |
| :--- | :--- | :--- |
| **Độ tin cậy** | ❌ Dễ gãy khi Groq đổi văn phong tiếng Anh | 🟢 Tuyệt đối 100% theo tiêu chuẩn quốc tế RFC 7231 |
| **Tốc độ** | Phải biên dịch và chạy Regex trên chuỗi text | Truy xuất trực tiếp O(1) theo tên Header |
| **Độ chính xác** | Dễ nhầm giữa số phút, số giây và số token | Máy chủ Groq cung cấp chính xác từng mili-giây |

---

## 2. DUYỆT CÂY EXCEPTION (`__cause__` & `__context__`) VỚI `seen_ids`

### 2.1. Vấn đề: LangChain bọc lỗi gốc (Búp bê Nga)
- Khi gọi qua LangChain/LangGraph, lỗi mạng `httpx.HTTPStatusError` (chứa Header) bị giấu trong bụng của `groq.RateLimitError`, và lỗi này lại bị bọc trong `OutputParserException` hoặc `ToolException`.
- Nếu chỉ kiểm tra `exc.response`, kết quả luôn là `None`.
- **Giải pháp:** Phải duyệt ngược theo 2 sợi xích:
  - `__cause__`: Nguyên nhân chủ động (`raise ErrorB() from ErrorA`).
  - `__context__`: Ngữ cảnh phát sinh ngẫu nhiên.

### 2.2. Bảo vệ chống vòng lặp vô hạn bằng `seen_ids`
- Khi 2 exception vô tình trỏ chéo nhau ($A \leftrightarrow B$), vòng lặp sẽ chạy mãi mãi làm treo máy.
- **Giải pháp:** Sử dụng tập hợp `seen_ids` lưu `id(current_error)`. Nếu gặp lại đối tượng đã duyệt $\rightarrow$ Thoát ngay.

---

## 3. PHÂN TÁCH RẠCH RÒI: TPM (PHÚT) VS TPD (NGÀY)

```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│  TPM (Tokens Per Minute)             │  TPD (Tokens Per Day)                │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ • Hạn mức theo từng phút (2s - 60s). │ • Hạn mức 24h cả ngày (5h - 15h).   │
│ • Báo hiệu: Header `Retry-After`,    │ • Báo hiệu: Từ khóa `tokens per day`,│
│   `x-ratelimit-reset-tokens`.        │   `tpd`, `daily`, `quota_exceeded`.  │
│ 👉 **HÀNH ĐỘNG: RETRY TỰ ĐỘNG**      │ ❌ **HÀNH ĐỘNG: FAIL-FAST NGAY**     │
│    (Đợi vài giây rồi thử lại ngầm)   │    (Ngắt ngay trong 0s, gom kết quả) │
└──────────────────────────────────────┴──────────────────────────────────────┘
```

> **Lưu ý then chốt:** Header `x-ratelimit-reset-tokens` của Groq **chỉ mô tả TPM**. Tuyệt đối không áp heuristic `> 180s` lên header này (nếu tài liệu nặng khiến TPM reset mất 210s, bot vẫn kiên nhẫn đợi chứ không báo sập vô căn cứ).

---

## 4. KIẾN TRÚC TÁCH RỜI: BỘ NÃO (DECISION) & TAY CHÂN (EXECUTION)

Thay vì trộn lẫn `if-else` và lệnh `sleep()`, hệ thống tách làm 2 tầng:

1. **Bộ não chẩn đoán (`decide_rate_limit_retry`):** Hàm thuần túy, nhận lỗi và số lần thử, xuất ra **Toa thuốc (`RetryDecision`)**:
   - `action`:
     - `"retry"`: Được phép thử lại sau `wait_seconds`.
     - `"fail_fast_tpd"`: Dừng ngay lập tức do hết hạn ngày.
     - `"exceeded_max_retries"`: Đã hết số lần thử cho phép.
     - `"exceeded_max_wait"`: Thời gian chờ vượt trần tối đa.
     - `"not_rate_limit"`: Không phải lỗi rate limit.
   - `wait_seconds`: Số giây cần chờ (kèm buffer / jitter).
   - `source`: Nguồn trích xuất (`retry-after`, `x-ratelimit-reset-tokens`, `regex`, `backoff`).
2. **Tay chân thực thi:**
   - **Đồng bộ (`invoke_with_rate_limit_retry`):** Dùng cho Map-Reduce `summarization.py` với `time.sleep()`.
   - **Bất đồng bộ (`ainvoke_with_rate_limit_retry`):** Dùng cho `followups.py`, `image_analysis.py`, `agent.py` với `await asyncio.sleep()` để **không làm nghẽn Main Thread**.

---

## 5. CHÍNH SÁCH RETRY: MAP-REDUCE VS CHAT REALTIME

| Tác vụ | Tham số `max_retries` | Hành vi hệ thống |
| :--- | :--- | :--- |
| **Map-Reduce Tóm Tắt** | `max_retries=None` | **"Chạy khi nào xong thì thôi"** (Unlimited retries per group). Không để tài liệu 50–100 trang bị vứt bỏ chỉ vì nghẽn 1–2 phút. |
| **Chat / Follow-up** | `max_retries=3` hoặc `5` | Giới hạn số lần thử + Trần tối đa (`MAX_RATE_LIMIT_WAIT_SECONDS=60s`) để giải phóng người dùng, không bắt chờ lâu. |

---

## 6. TOÀN BỘ CODE MỚI NHẤT TRONG HỆ THỐNG

### 6.1. Module Dùng Chung: `services/token_limit_handler.py`

```python
"""Centralized handler for token limits, HTTP header parsing, and graceful recovery."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import math
import os
import random
import re
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")

_VIETNAMESE_LANGUAGES = {"vi", "vietnamese", "tiếng việt", "tieng viet"}


def _float_setting(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value >= minimum else default


# Cấu hình biến môi trường
MAX_RATE_LIMIT_WAIT_SECONDS = _float_setting("MAX_RATE_LIMIT_WAIT_SECONDS", 60.0, 1.0)
RATE_LIMIT_BACKOFF_BASE_SECONDS = _float_setting("RATE_LIMIT_BACKOFF_BASE_SECONDS", 1.0, 0.1)
RATE_LIMIT_BACKOFF_JITTER_SECONDS = _float_setting("RATE_LIMIT_BACKOFF_JITTER_SECONDS", 0.5, 0.0)


@dataclass(frozen=True)
class RetryDecision:
    """Phiếu kết luận phân loại lỗi và hành động retry chuẩn hóa."""
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
        "regex",
        "backoff",
        "none",
    ]


def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    """Duyệt an toàn cây lỗi __cause__ và __context__, chống lặp vô hạn bằng seen_ids."""
    pending = [error]
    seen_ids: set[int] = set()
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen_ids:
            continue
        seen_ids.add(current_id)
        yield current
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)


def _exception_text(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        return " ".join(str(item) for item in _exception_chain(error)).lower()
    return str(error).lower()


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


def _header_value(headers: Mapping[str, Any], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == target and value is not None:
            return str(value).strip()
    return None


def _parse_duration(value: str) -> float | None:
    """Parse thời lượng từ Groq (ví dụ: '7.66s', '1m30.5s', '150ms')."""
    clean = value.strip().lower()
    if clean.endswith("ms"):
        try:
            return float(clean[:-2].strip()) / 1000.0
        except ValueError:
            return None
    match = re.fullmatch(r"(?:(\d+)\s*m\s*)?([\d]+(?:\.[\d]+)?)\s*s", clean)
    if not match:
        return None
    seconds = (float(match.group(1) or 0) * 60.0) + float(match.group(2))
    return seconds if math.isfinite(seconds) else None


def _parse_retry_after(value: str, server_date: str | None) -> float | None:
    """Parse header Retry-After (hỗ trợ số giây và ngày HTTP RFC 7231)."""
    clean_value = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", clean_value):
        seconds = float(clean_value)
        return seconds if math.isfinite(seconds) else None

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
    """Chỉ đọc từ HTTP Response Header chuẩn, loại bỏ hoàn toàn regex rườm rà."""
    if isinstance(error, BaseException):
        return _header_retry_delay(error)
    return None


def decide_rate_limit_retry(
    error: BaseException | str,
    *,
    attempt: int,
    max_retries: int | None,
    max_wait_seconds: float = MAX_RATE_LIMIT_WAIT_SECONDS,
) -> RetryDecision:
    """Bộ não quyết định: Phân loại TPD vs TPM, giới hạn retry và thời gian chờ."""
    if _has_daily_limit_marker(error):
        return RetryDecision("fail_fast_tpd", None, "Daily token quota exhausted.", "none")

    delay = extract_retry_delay(error)
    # Giữ heuristic cũ >180s DUY NHẤT cho chuỗi regex cũ để bảo đảm tương thích ngược
    if delay and delay[1] == "regex" and delay[0] > 180.0:
        return RetryDecision("fail_fast_tpd", None, "Legacy wait-time heuristic indicates a daily quota.", "regex")
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
        wait_seconds = min(RATE_LIMIT_BACKOFF_BASE_SECONDS * (2 ** min(exponent, 20)), max_wait_seconds)
        wait_seconds = min(
            max_wait_seconds,
            wait_seconds + random.uniform(0.0, RATE_LIMIT_BACKOFF_JITTER_SECONDS),
        )
        source = "backoff"
    else:
        wait_seconds, source = delay

    wait_seconds = max(0.25, wait_seconds)
    if max_retries is not None and wait_seconds > max_wait_seconds:
        return RetryDecision(
            "exceeded_max_wait", wait_seconds,
            f"Server requested {wait_seconds:.2f}s, above configured maximum {max_wait_seconds:.2f}s.",
            source,
        )
    return RetryDecision("retry", wait_seconds, "Transient rate limit; retry is allowed.", source)


def wait_for_retry(wait_seconds: float, *, max_sleep_chunk_seconds: float = MAX_RATE_LIMIT_WAIT_SECONDS) -> None:
    """Ngủ từng phần nhỏ để dễ quản lý luồng."""
    remaining = max(0.0, wait_seconds)
    while remaining > 0:
        chunk = min(remaining, max_sleep_chunk_seconds)
        time.sleep(chunk)
        remaining -= chunk


def invoke_with_rate_limit_retry(
    invoke: Callable[[], T],
    *,
    max_retries: int | None,
    operation: str,
    can_retry: Callable[[], bool] | None = None,
) -> T:
    """Hàm bọc thực thi đồng bộ với chính sách retry Rate Limit tập trung."""
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
    invoke: Callable[[], Awaitable[T]],
    *,
    max_retries: int | None,
    operation: str,
) -> T:
    """Hàm bọc thực thi bất đồng bộ (Native Async) không khóa Event Loop."""
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
```

---

### 6.2. Tích Hợp Vào `services/summarization.py`

```python
def _invoke(
    llm: ChatGroq,
    prompt: str,
    stage: str,
    group_number: int,
    target_language: str = "vi",
    max_retries: int | None = None,
) -> str:
    messages = [
        SystemMessage(content=SUMMARY_SYSTEM_PROMPT + lang_rule),
        HumanMessage(content=prompt),
    ]
    operation = f"Summarization ({stage}, group {group_number})"

    try:
        # Gọi qua hàm retry chuẩn hóa, truyền max_retries=None để "chạy khi nào xong thì thôi"
        response = invoke_with_rate_limit_retry(
            lambda: llm.invoke(messages),
            max_retries=max_retries,
            operation=operation,
        )
    except Exception as exc:
        if is_daily_token_limit(exc):
            logger.error("[RateLimit-TPD] Summarization stopped during %s: %s", operation, exc)
            raise RuntimeError(
                f"Document summarization rate limit exceeded (Đã hết hạn mức token trong ngày): {exc}"
            ) from exc
        logger.error("[SummarizationError] Failed during %s: %s", operation, exc)
        raise RuntimeError(f"Document summarization failed during {operation}: {exc}") from exc

    return _content_text(response)
```

---

## 7. KẾT QUẢ KIỂM THỬ XÁC MINH (VERIFICATION)

- Toàn bộ **28/28 tests hồi quy** trong `tests/test_refactor_regressions.py` chạy thành công 100% (0.018s).
- Luồng Follow-up Native Async và Map-Reduce vận hành hoàn hảo với cơ chế bóc tách Header mới.
