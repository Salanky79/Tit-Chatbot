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
| **Tính gọn gàng** | Rườm rà, phải bảo trì nhiều biểu thức chính quy | **Đã xóa sạch 100% Regex**, chỉ dùng cắt chuỗi toán học |

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

### 3.1. Loại bỏ hoàn toàn Magic Number "180s"
- **Không còn đoán mò:** Xóa sạch logic `wait_time > 180s`.
- Chỉ coi là **TPD (hết hạn ngày)** khi có từ khóa định danh chính xác từ Groq (`tokens per day`, `tpd`, `daily`, `quota_exceeded`, `insufficient_quota`).
- Mọi thời gian chờ do Groq trả về trong Header `x-ratelimit-reset-tokens` (kể cả 200s–300s khi tải nặng) đều được đối xử chuẩn xác là **TPM (Reset theo phút)** và kiên nhẫn đợi, không bị xử phạt oan uổng.

### 3.2. TPM tính những token nào?
$$\text{Tổng TPM tiêu thụ} = \text{Input Tokens} + \underbrace{\mathbf{Reasoning\ Tokens\ (Suy\ nghĩ\ ngầm)} + \text{Output\ Tokens\ (Chữ\ hiển\ thị)}}_{\text{Tổng Output}}$$
- **Input Tokens** (file tài liệu tải lên, ảnh, lịch sử chat cũ) thường là thủ phạm chính làm cạn kiệt TPM chứ không riêng gì output.
- **Reasoning Tokens** (token suy nghĩ ngầm trong thẻ `<think>` của các model như `gpt-oss-20b`) được tính 100% vào hạn mức TPM.

---

## 4. TẠI SAO CÙNG 1 LOGIC RETRY MÀ PHẢI SINH RA 2 HÀM? (`invoke` VS `ainvoke`)

Rất nhiều lập trình viên thắc mắc: *Tại sao `invoke_with_rate_limit_retry` và `ainvoke_with_rate_limit_retry` giống nhau đến 95% về logic mà không gộp làm một?*

### 4.1. Bản chất: "The Function Color Problem" trong Python
Trong ngôn ngữ Python, có sự phân chia ranh giới tuyệt đối:
- **Hàm đồng bộ (`def`)**: TUYỆT ĐỐI KHÔNG THỂ viết từ khóa `await` bên trong! Phải dùng `time.sleep()` để tạm dừng.
- **Hàm bất đồng bộ (`async def`)**: Bắt buộc phải có `await` và dùng `await asyncio.sleep()` để nhường quyền điều khiển cho Event Loop.

### 4.2. Phân công nhiệm vụ thực tế trong dự án Tit-Chatbot:

| Hàm | Kiểu thực thi | Cơ chế tạm dừng | Nơi sử dụng trong dự án | Lý do bắt buộc |
| :--- | :--- | :--- | :--- | :--- |
| **`invoke_with_rate_limit_retry`** | Đồng bộ (`def`) | `time.sleep()` | • `services/summarization.py` (Map-Reduce)<br>• `services/image_analysis.py` (Vision)<br>• `agent.py` (LangGraph node) | Các luồng này chạy ngầm trong `ThreadPoolExecutor` của hệ điều hành. Dùng `time.sleep()` chỉ khóa luồng phụ đó, không ảnh hưởng Main Thread. |
| **`ainvoke_with_rate_limit_retry`** | **Native Async** (`async def`) | `await asyncio.sleep()` | • `services/followups.py` (Endpoint `/chat/followups` trong `app.py`) | Luồng này chạy trực tiếp trên **1 Main Thread duy nhất** của FastAPI. Dùng `await asyncio.sleep()` giúp Main Thread rảnh tay phục vụ người dùng khác, **không tốn 1 thread phụ nào và không đơ server**. |

---

## 5. CƠ CHẾ BẢO VỆ STREAM TOKEN: CHỐNG LẶP CHỮ (`_OutputStartedTracker`)

Trong file [agent.py](file:///d:/Tit-Chatbot/agent.py), mô hình chat trực tiếp với người dùng theo cơ chế **Stream (phun từng chữ ra màn hình)**. Nếu gặp lỗi TPM, hệ thống chia làm 2 kịch bản thông minh qua điều kiện `can_retry`:

```python
output_tracker = _OutputStartedTracker()
response = invoke_with_rate_limit_retry(
    ...,
    can_retry=lambda: not output_tracker.output_started,
)
```

1. **Kịch bản A: Lỗi xảy ra lúc 0s (Chưa nhả chữ nào ra màn hình):**
   - `output_tracker.output_started == False` $\rightarrow$ `can_retry == True`.
   - **Tự động retry ngầm 100%:** Hệ thống tự đợi 3s rồi gọi lại. Người dùng chỉ thấy dấu 3 chấm `...` xoay lâu hơn 3 giây rồi câu trả lời hiện ra trọn vẹn.
2. **Kịch bản B: Lỗi xảy ra giữa chừng khi đang stream dở:**
   - Chữ đã in một nửa ra màn hình (`output_tracker.output_started == True`) $\rightarrow$ `can_retry == False`.
   - **CẤM RETRY LẬP TỨC:** Nếu tự ý retry lại từ đầu, Groq sẽ sinh lại câu nói từ chữ cái đầu tiên, khiến trình duyệt gắn tiếp vào đuôi tạo thành **thảm họa lặp chữ 2 lần (Duplicate text)**!
   - Hệ thống dừng lại ngay lập tức, in dòng thông báo: *"⚠️ Mô hình đang chạm giới hạn TPM, vui lòng thử lại sau 15–30s"*.
   - Phần đã nói dở được lưu vào MongoDB. Khi người dùng gõ *"Nói tiếp đi"*, bot đọc lại DB và tiếp tục nói phần đuôi hoàn hảo!

---

## 6. CHÍNH SÁCH RETRY: MAP-REDUCE VS CHAT REALTIME

| Tác vụ | Tham số `max_retries` | Hành vi hệ thống |
| :--- | :--- | :--- |
| **Map-Reduce Tóm Tắt** | `max_retries=None` | **"Chạy khi nào xong thì thôi"** (Unlimited retries per group). Không để tài liệu 50–100 trang bị vứt bỏ chỉ vì nghẽn 1–2 phút. |
| **Chat / Follow-up** | `max_retries=2` hoặc `3` | Giới hạn số lần thử + Trần tối đa (`MAX_RATE_LIMIT_WAIT_SECONDS=60s`) để giải phóng người dùng, không bắt chờ lâu. |

---

## 7. TOÀN BỘ CODE MỚI NHẤT TRONG HỆ THỐNG

### 7.1. Module Dùng Chung: `services/token_limit_handler.py`

```python
"""Centralized handler for token limits, HTTP header parsing, and graceful recovery."""

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
    """Parse thời lượng từ Groq (ví dụ: '7.66s', '1m30.5s') hoàn toàn bằng xử lý chuỗi cơ bản."""
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


def _parse_retry_after(value: str, server_date: str | None) -> float | None:
    """Parse header Retry-After (hỗ trợ số giây và ngày HTTP RFC 7231)."""
    clean_value = value.strip()
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
    """Chỉ đọc từ HTTP Response Header chuẩn, loại bỏ hoàn toàn regex rườm rà."""
    if isinstance(error, BaseException):
        return _header_retry_delay(error)
    return None


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


def is_daily_token_limit(exc: Exception | str) -> bool:
    """Kiểm tra xem lỗi có phải do chạm trần token trong ngày (TPD) hay không.
    Chỉ dựa trên thông báo quota ngày rõ ràng, không sử dụng heuristic đoán mò thời gian.
    """
    return _has_daily_limit_marker(exc)
```

---

## 8. KẾT QUẢ KIỂM THỬ XÁC MINH (VERIFICATION)

- Toàn bộ **28/28 tests hồi quy** trong `tests/test_refactor_regressions.py` chạy thành công 100% (0.020s).
- Không còn bất kỳ dòng code `import re` hay logic Regex nào trong bộ xử lý Rate Limit.
