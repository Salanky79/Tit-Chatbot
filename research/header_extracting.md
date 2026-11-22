# TỔNG HỢP KIẾN TRÚC BÓC TÁCH HTTP HEADER, RETRY RATE LIMIT & XỬ LÝ NGÔN NGỮ

Tài liệu này hệ thống hóa toàn bộ kiến trúc kiểm soát lưu lượng mạng (Rate Limiting), kỹ thuật bóc tách HTTP Header, chiến lược Retry đồng bộ/bất đồng bộ, cùng giải pháp xử lý ngôn ngữ đa tầng (Natural Language LLM & Request-Scoped ContextVar) trong hệ thống **Tit-Chatbot**.

---

## 1. BẢN ĐỒ TỔNG THỂ CÁC HTTP HEADERS: LẤY Ở ĐÂU VÀ ĐỂ LÀM GÌ?

Trong toàn bộ hệ thống Tit-Chatbot, các HTTP Headers chỉ đến từ **2 nguồn duy nhất** trên đường truyền mạng:

```
┌────────────────────────┐                   ┌─────────────────────────┐                   ┌────────────────────────┐
│  TRÌNH DUYỆT (CLIENT)  │ ──(Request Hdr)─► │  SERVER FASTAPI (app.py)│ ──(Gọi LLM API)─► │     MÁY CHỦ GROQ       │
│  - navigator.language  │                   │  - Đọc request.headers  │                   │  - Trả về HTTP 429     │
└────────────────────────┘                   └─────────────────────────┘                   └────────────────────────┘
            │                                             │                                             │
            ▼                                             ▼                                             ▼
  Gửi: X-Language, Accept-Lang                 Lưu vào ContextVar                            Trả: Retry-After, Date,
  👉 Nằm ở: request.headers                    👉 Dùng khi Groq sập                          x-ratelimit-reset-tokens
  👉 File: templates/index.html                👉 File: services/language.py                 👉 Nằm ở: error.response.headers
                                                                                             👉 File: token_limit_handler.py
```

### 1.1. Nhóm 1: HTTP Response Headers từ máy chủ Groq API (`token_limit_handler.py`)
Khi Groq API từ chối phục vụ do quá tải (HTTP Status Code `429 Too Many Requests`), nó trả về 3 header quan trọng nằm dán dính bên trong đối tượng ngoại lệ mạng `error.response.headers`:

| Tên Header | Ví dụ giá trị | Mục đích & Ý nghĩa trong code |
| :--- | :--- | :--- |
| **`Retry-After`** | `"4.5"` *(hoặc HTTP-Date)* | **Biết chính xác phải ngủ (sleep) bao nhiêu giây trước khi retry.** Groq trực tiếp thông báo: *"Bạn gửi quá nhanh, vui lòng chờ đúng 4.5s rồi gửi lại"*. |
| **`Date`** | `"Sun, 22 Nov 2026 04:35:00 GMT"` | **Khử độ lệch đồng hồ (Clock Skew).** Dùng làm mốc đối chiếu với `Retry-After` (nếu Groq trả về dạng ngày giờ), tránh việc máy chủ ở Mỹ và máy client ở Việt Nam lệch múi giờ. |
| **`x-ratelimit-reset-tokens`** | `"7.66s"` *(hoặc `1m30s`)* | **Phương án dự phòng.** Header đặc thù của riêng Groq báo thời gian hồi phục hạn mức token mỗi phút (TPM). Nếu không có `Retry-After`, bot sẽ đọc giá trị này để ngủ. |

👉 **Mục đích duy nhất của Nhóm 1:** Trả lời câu hỏi kỹ thuật: *"Bot cần phải ngủ (`time.sleep` hoặc `await asyncio.sleep`) đúng bao nhiêu giây để tự động gọi lại API thành công mà không làm sập server?"*.

### 1.2. Nhóm 2: HTTP Request Headers từ trình duyệt Client (`services/language.py`)
Khi người dùng bấm nút gửi tin nhắn trên giao diện web, trình duyệt gửi lên cổng API `/chat/stream` của server:

| Tên Header | Nguồn gốc | Mục đích & Ý nghĩa trong code |
| :--- | :--- | :--- |
| **`X-Language`** | Do JavaScript trong `templates/index.html` gửi qua `navigator.language` | **Header tùy biến ưu tiên số 1.** Mang mã ngôn ngữ của thiết bị (ví dụ `"vi"`, `"en-US"`). Nếu sau này làm nút chọn cờ ngôn ngữ trên web, JS chỉ cần nhét cờ vào header này. |
| **`Accept-Language`** | Do mọi trình duyệt tự động gửi theo chuẩn RFC 7231 | **Header dự phòng số 2.** Dùng khi request đến từ Mobile App, Telegram, hoặc Postman/curl (những nơi không gửi `X-Language`). |

👉 **Mục đích duy nhất của Nhóm 2:** "Mua bảo hiểm" cho server. Trả lời câu hỏi: *"Nếu đúng lúc này Groq API bị sập/hết token ngày (TPD), LLM không thể nói chuyện được nữa, thì Server Python phải in câu thông báo lỗi bằng tiếng Việt hay tiếng Anh?"*.

---

## 2. BẢN CHẤT MẠNG: TẠI SAO CẦN HTTP HEADER THAY VÌ REGEX?

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

| Tiêu chí so sánh | Quét Regex trên Body (Cách cũ) | Bóc tách HTTP Header (Kiến trúc chuẩn) |
| :--- | :--- | :--- |
| **Độ tin cậy** | ❌ Dễ gãy khi Groq thay đổi câu chữ tiếng Anh | 🟢 Tuyệt đối 100% theo tiêu chuẩn quốc tế RFC 7231 |
| **Tốc độ xử lý** | Tốn chi phí biên dịch và quét Regex qua chuỗi text | Truy xuất trực tiếp $O(1)$ theo tên Header |
| **Độ chính xác** | Dễ nhầm lẫn giữa số phút, số giây và số token | Máy chủ API cung cấp chính xác từng mili-giây |
| **Tính tương thích** | Phụ thuộc vào từng loại model cụ thể | Chuẩn hóa chung cho toàn bộ hệ thống HTTP |

---

## 3. DUYỆT CÂY EXCEPTION: TẠI SAO CẦN CẢ `__cause__` VÀ `__context__`?

### 3.1. Phân biệt `__cause__` vs `__context__` trong Python
Khi một ngoại lệ bị bọc lồng nhau trong Python 3 (PEP 3134), nó được liên kết qua 2 sợi xích:

1. **`__cause__` (Nguyên nhân chủ động - Explicit Chaining):**
   - Sinh ra khi lập trình viên chủ động dùng cú pháp `raise ErrorB from ErrorA`.
   - Ví dụ: `groq.RateLimitError` được raise chủ động từ `httpx.HTTPStatusError`.
2. **`__context__` (Ngữ cảnh ngẫu nhiên - Implicit Chaining):**
   - Sinh ra khi một lỗi mới **vô tình nảy sinh bên trong khối `except` hoặc `finally`** mà lập trình viên **KHÔNG dùng cú pháp `from`**.
   - Ví dụ: Trong lúc bắt lỗi mạng, code của Tool hoặc LangGraph vô tình đọc một biến `None` $\rightarrow$ nổ ra lỗi `ValueError` hoặc `AttributeError`.

```
                  ┌───────────────────────────────┐
                  │      Exception hiện tại       │
                  └──────────────┬────────────────┘
                                 │
         ┌───────────────────────┴───────────────────────┐
         ▼                                               ▼
   `__cause__`                                    `__context__`
(Nguyên nhân CHỦ ĐỘNG)                          (Tai nạn NGẪU NHIÊN)
"Tôi biết lỗi A gây ra lỗi B"               "Tôi đang xử lý lỗi A thì đùng một cái
(Dùng cú pháp: raise B from A)                   phát sinh thêm lỗi B ngoài ý muốn"
```

### 3.2. Ví dụ thực tế: Điều gì xảy ra nếu bỏ quên `__context__`?
Xét chuỗi lỗi 3 tầng điển hình khi gọi qua LangGraph:
```python
# Tầng 1 (Ruột trong cùng): HTTPStatusError từ httpx (chứa Header Retry-After và text 'tokens per day')
# Tầng 2 (Ở giữa): groq.RateLimitError (bọc Tầng 1 qua __cause__: raise e2 from e1)
# Tầng 3 (Vỏ ngoài cùng): ValueError của Tool (nảy sinh trong khối except, bọc Tầng 2 qua __context__)
```
- Nếu chỉ kiểm tra `error.__cause__`: Vì ở Tầng 3 lập trình viên không dùng `from`, nên `error.__cause__` là **`None`**. Sợi dây xích **bị đứt ngay ở vỏ ngoài cùng**!
- Hậu quả: Hệ thống hoàn toàn không chạm tới được Tầng 1, không thấy `tokens per day`, không thấy `Retry-After: 4.5`, tưởng đây là lỗi linh tinh và làm sập toàn bộ bot!
- **Giải pháp chuẩn:** Duyệt **CẢ HAI** (`__cause__` và `__context__`), kết hợp tập hợp `seen_ids: set[int]` lưu `id(current_error)` để chống lặp vô hạn nếu 2 exception vô tình trỏ chéo nhau.

---

## 4. PHÂN TÁCH RẠCH RÒI: TPM (PHÚT) VS TPD (NGÀY)

```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│  TPM (Tokens Per Minute)             │  TPD (Tokens Per Day)                │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ • Hạn mức làm mới theo từng phút (2s - 60s). │ • Hạn mức cộng dồn 24h cả ngày (5h - 15h).   │
│ • Báo hiệu: Header `Retry-After`,    │ • Báo hiệu: Từ khóa `tokens per day`,│
│   `x-ratelimit-reset-tokens`.        │   `tpd`, `daily`, `quota_exceeded`.  │
│ 👉 **HÀNH ĐỘNG: RETRY TỰ ĐỘNG**      │ ❌ **HÀNH ĐỘNG: FAIL-FAST NGAY**     │
│    (Đợi vài giây rồi thử lại ngầm)   │    (Ngắt ngay trong 0s, gom kết quả) │
└──────────────────────────────────────┴──────────────────────────────────────┘
```

> **Lưu ý then chốt:** Header `x-ratelimit-reset-tokens` của Groq **chỉ mô tả TPM**. Tuyệt đối không áp heuristic thời gian `> 180s` lên header này (nếu tài liệu nặng khiến TPM reset mất 210s, bot vẫn kiên nhẫn đợi chứ không báo sập vô căn cứ).

---

## 5. KIẾN TRÚC TÁCH RỜI: BỘ NÃO (DECISION) & TAY CHÂN (EXECUTION)

1. **Bộ não chẩn đoán (`decide_rate_limit_retry`):**
   - Là một hàm thuần túy (Pure Function), nhận vào Exception và số lần thử hiện tại (`attempt`), trả về một đối tượng bất biến **`RetryDecision`** (Toa thuốc).
   - Xác định chính xác hành động: `"retry"` (thử lại sau $N$ giây), `"fail_fast_tpd"` (ngắt ngay lập tức), `"exceeded_max_retries"` (vượt quá số lần thử), `"exceeded_max_wait"` (thời gian chờ vượt trần), hoặc `"not_rate_limit"` (lỗi khác).
2. **Tay chân thực thi:**
   - **Đồng bộ (`invoke_with_rate_limit_retry`):** Dành cho Map-Reduce tóm tắt tài liệu nặng (`services/summarization.py`), chia nhỏ giấc ngủ `wait_for_retry` để dễ kiểm soát luồng.
   - **Bất đồng bộ (`ainvoke_with_rate_limit_retry`):** Dành cho luồng Chat Realtime, Follow-up, Agent Tool (`services/followups.py`, `services/image_analysis.py`, `agent.py`) sử dụng `await asyncio.sleep()` để **giải phóng Event Loop, tuyệt đối không làm nghẽn Main Thread**.

---

## 6. CHI TIẾT KỸ THUẬT: `services/token_limit_handler.py`

### 6.1. Duyệt cây Exception an toàn (`_exception_chain`)

```python
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
```

- **Mục đích:** Bóc tách lỗi mạng gốc bị giấu trong các tầng bọc (wrapper) của LangChain/LangGraph.
- **Cách hoạt động:** Dùng ngăn xếp `pending` duyệt lần lượt cả hai thuộc tính `__cause__` và `__context__`.
- **Điểm mấu chốt:** Tập hợp `seen_ids` ghi lại địa chỉ bộ nhớ `id()` của từng đối tượng ngoại lệ để chống lặp vô tận.

### 6.2. Ghép chuỗi thông báo lỗi (`_exception_text`)

```python
def _exception_text(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        return " ".join(str(item) for item in _exception_chain(error)).lower()
    return str(error).lower()
```

- **Mục đích:** Gom toàn bộ câu chữ của tất cả các tầng lỗi thành một chuỗi duy nhất viết thường.
- **Tác dụng:** Dùng làm đầu vào cho `_has_daily_limit_marker` (quét từ khóa TPD: `"tokens per day"`, `"daily"`, `"quota_exceeded"`) và `_is_rate_limit_exception` (quét từ khóa TPM: `"429"`, `"rate limit"`, `"tpm"`).

### 6.3. Bóc tách Header phản hồi mạng (`_response_headers`)

```python
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
```

- **Tool có header không?:** **Hoàn toàn KHÔNG!** Các ngoại lệ của Tool (`ToolException`, `ValueError`) không dính dáng đến mạng nên `getattr` trả về `None` và tự động bị bỏ qua ngay lập tức.
- **Tại sao cần duyệt chuỗi?:** Một số thư viện gắn header trực tiếp ở `error.headers` (`groq.RateLimitError`), một số gắn trong `error.response.headers` (`httpx.HTTPStatusError`). Hàm thu thập an toàn từ mọi tầng mà không bỏ sót.

### 6.4. Bóc tách thời lượng chờ chuẩn RFC 7231 & định dạng Groq

```python
def _parse_retry_after(value: str, server_date: str | None) -> float | None:
    clean_value = value.strip()
    # 1. Số giây thuần (ví dụ: "4.5", "10")
    if re.fullmatch(r"\d+(?:\.\d+)?", clean_value):
        seconds = float(clean_value)
        return seconds if math.isfinite(seconds) else None
    # 2. Định dạng ngày giờ RFC 7231 (HTTP-Date)
    try:
        target = email.utils.parsedate_to_datetime(clean_value)
        base = email.utils.parsedate_to_datetime(server_date) if server_date else datetime.now(timezone.utc)
        return max(0.0, (target - base).total_seconds())
    except Exception:
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
```

- **Mục đích:** Chuyển đổi mọi định dạng thời gian máy chủ về số giây thực (`float`).
- **Điểm mấu chốt:** So sánh `target` với `server_date` từ header `Date` để khử hoàn toàn rủi ro lệch đồng hồ (Clock Skew).

### 6.5. Định dạng tên Tool động (0% Từ điển Hardcode) & Cứu hộ dở dang

```python
def format_tool_display_name(tool_name: str) -> str:
    """Format technical tool identifiers (snake_case) into a clean, human-readable display string."""
    clean = (tool_name or "").strip().replace("_", " ").strip()
    return clean.title() if clean else "Tool"

def format_tool_limit_notice(
    tool_name: str,
    partial_info: str = "",
    language: str | None = None,
) -> str:
    is_vi = is_vietnamese(language)
    clean_partial = partial_info.strip()
    display_tool = format_tool_display_name(tool_name)
    if is_vi:
        msg = f"⚠️ Tính năng `{display_tool}` tạm thời không thể hoàn thành do mô hình đã đạt giới hạn hạn mức token trong ngày."
        if clean_partial:
            msg += f"\n\nDưới đây là thông tin thu thập được trước khi gián đoạn:\n{clean_partial}"
        return msg
    msg = f"⚠️ Feature `{display_tool}` could not complete because the model reached its daily token limit on Groq API."
    if clean_partial:
        msg += f"\n\nHere is the information gathered before interruption:\n{clean_partial}"
    return msg
```

- **Xóa sạch 100% từ điển:** Đã xóa bỏ hoàn toàn `_TOOL_NAMES_VI` và `_TOOL_NAMES_EN`.
- **Khả năng mở rộng:** Dù hệ thống có **100 hay 1.000 tools**, tên công cụ đều tự động chuyển đổi từ `snake_case` sang `Title Case` (`analyze_uploaded_image` $\rightarrow$ `Analyze Uploaded Image`).
- **Phân công trách nhiệm:** Tool chỉ trả về lỗi kỹ thuật; LLM là người đọc lỗi đó và tự giải thích lại cho người dùng bằng ngôn ngữ của họ. Chỉ khi cần sinh câu thông báo cứu hộ fallback, hàm này mới được dùng.

---

## 7. CHI TIẾT KỸ THUẬT: `services/language.py`

### 7.1. Chuẩn BCP 47 & Thuật toán `extract_primary_language`

```python
def extract_primary_language(raw_lang: str | None) -> str:
    """Extract primary language subtag (BCP 47), preserving 2-letter or 3-letter ISO codes."""
    if not raw_lang:
        return "vi"
    clean = str(raw_lang).strip().split(",")[0].strip()
    primary = clean.split("-")[0].split("_")[0].strip().lower()
    return primary or "vi"
```

- **Tại sao không cắt 2 ký tự `[:2]`:** Các ngôn ngữ có mã 3 ký tự theo ISO 639-2/3 như `fil-PH` (tiếng Filipino) nếu cắt `[:2]` sẽ bị biến thành `fi` (tiếng Phần Lan - Finnish), dẫn đến kết quả sai hoàn toàn.
- **Thuật toán chuẩn:** Tách trước dấu phẩy `,` và trước dấu gạch ngang `-` hoặc `_`, bảo toàn cả mã 2 ký tự (`vi`, `en`, `es`) và mã 3 ký tự (`fil`, `yue`).

### 7.2. Thứ tự ưu tiên & `X-Language` thay đổi khi nào?

```python
def resolve_request_language(headers: Mapping[str, Any] | None) -> str:
    """Resolve user language from request headers (X-Language preferred, then Accept-Language)."""
    if not headers:
        return "vi"
    for key, val in headers.items():
        if str(key).casefold() == "x-language" and val:
            return extract_primary_language(str(val))
    for key, val in headers.items():
        if str(key).casefold() == "accept-language" and val:
            return extract_primary_language(str(val))
    return "vi"
```

- **Tại sao có `X-Language`?** Trình duyệt web (Chrome, Firefox) **cấm** mã JavaScript của trang web tự ý sửa đổi header `Accept-Language`. Vì vậy, khi lập trình viên muốn truyền ngôn ngữ chính xác của giao diện (hoặc khi người dùng bấm nút đổi ngôn ngữ trên web), ta bắt buộc phải dùng header tùy biến `X-Language`.
- **`X-Language` thay đổi khi nào?**
  1. Khi người dùng ở các quốc gia khác nhau mở web: `navigator.language` tự mang mã ngôn ngữ của máy họ (`vi-VN`, `en-US`, `fil-PH`).
  2. Khi người dùng bấm vào nút chọn ngôn ngữ trên giao diện web (ví dụ chọn cờ Anh hay cờ Việt).

### 7.3. Cách ly ngữ cảnh đa người dùng với `ContextVar`

```python
_current_language: ContextVar[str] = ContextVar("current_language", default="vi")

def set_current_language(lang: str) -> None:
    _current_language.set(extract_primary_language(lang))

def get_current_language() -> str:
    return _current_language.get()

def is_vietnamese(language: str | None = None) -> bool:
    code = extract_primary_language(language) if language is not None else get_current_language()
    return code == "vi"
```

- **Tại sao không dùng biến toàn cục (`global`)?** Biến toàn cục dùng chung cho cả tiến trình. Nếu 100 người chat cùng lúc, request của người này sẽ ghi đè ngôn ngữ của người kia (Race Condition).
- **Tại sao không chuyền tay biến qua từng hàm?** Cửa vào API (`app.py`) cách nơi xảy ra lỗi (`token_limit_handler.py`) 5 tầng hàm qua LangGraph. `ContextVar` đóng vai trò như một ngăn kéo dữ liệu độc lập của riêng từng request, tự động cô lập tuyệt đối và giải phóng khi request kết thúc.
- **Chính sách ngôn ngữ:** Người dùng `vi` nhận thông báo tiếng Việt; mọi người dùng quốc tế khác (`en`, `es`, `fil`, `fr`, `zh`...) quy về tiếng Anh chuẩn (Fallback).

---

## 8. SỰ PHÂN CÔNG GIỮA LLM VÀ SERVER KHI NGƯỜI DÙNG CHAT ĐA NGÔN NGỮ

Một câu hỏi quan trọng: *"Nếu người dùng đang gõ tiếng Việt, sau đó 2 câu sau lại gõ tiếng Anh thì hệ thống xử lý thế nào?"*

Hệ thống phân định 2 tầng xử lý hoàn toàn độc lập:

1. **Tầng trò chuyện của LLM (Bình thường - 99% thời gian):**
   - LLM đọc trực tiếp nội dung tin nhắn trong `body`.
   - Trong `prompts/agent.py` đã cài đặt nguyên tắc tự nhiên: *"Always respond in the exact same language as the user's latest message"*.
   - Khách hỏi tiếng Việt $\rightarrow$ LLM trả lời tiếng Việt. Khách bất ngờ đổi sang tiếng Anh $\rightarrow$ LLM lập tức chuyển phắt sang tiếng Anh. **LLM tự xử lý 100% đa ngôn ngữ, không cần can thiệp Header.**
2. **Tầng thông báo sự cố của Server (Khi Groq bị sập 429 TPD / TPM):**
   - Khi sập API, LLM đã chết, không thể tự sinh chữ được nữa.
   - Server Python đọc mã ngôn ngữ từ `ContextVar` (lấy từ Header của request đó) để in câu thông báo cứu hộ: tiếng Việt cho người dùng `vi`, và tiếng Anh cho người dùng quốc tế.

---

## 9. BÁO CÁO KIỂM THỬ XÁC MINH TOÀN DIỆN (VERIFICATION REPORT)

Kiến trúc mới được kiểm chứng thông qua 2 bộ test suite độc lập:

### 9.1. Bộ kiểm thử ngôn ngữ mới (`tests/test_language.py`)
Bao gồm 10 ca kiểm thử đơn vị bao quát toàn diện các khía cạnh kỹ thuật:

| Tên bài kiểm thử | Mục đích xác minh | Kết quả |
| :--- | :--- | :--- |
| `test_extract_primary_language_preserves_two_and_three_letter_codes` | Kiểm tra bóc tách BCP 47 bảo toàn mã 2 ký tự (`vi`, `en`, `es`) và mã 3 ký tự (`fil`, `yue`). | 🟢 PASS |
| `test_resolve_request_language_prefers_x_language` | Kiểm tra thứ tự ưu tiên: `X-Language` ghi đè `Accept-Language`. | 🟢 PASS |
| `test_resolve_request_language_falls_back_to_accept_language` | Kiểm tra fallback sang `Accept-Language` khi không có header tùy biến. | 🟢 PASS |
| `test_resolve_request_language_defaults_to_vi_on_empty` | Kiểm tra giá trị mặc định an toàn (`vi`) khi headers rỗng hoặc `None`. | 🟢 PASS |
| `test_context_var_set_and_get` | Kiểm tra tính cô lập và lưu trữ dữ liệu của `ContextVar`. | 🟢 PASS |
| `test_handle_partial_stream_uses_context_language` | Kiểm tra thông báo cứu hộ stream dở dang tự động đổi tiếng Việt / tiếng Anh. | 🟢 PASS |
| `test_handle_partial_stream_empty_text_uses_context_language` | Kiểm tra thông báo khi chưa có token nào được sinh ra. | 🟢 PASS |
| `test_format_tool_limit_notice_uses_context_language` | Kiểm tra thông báo lỗi tool dùng tên chuẩn hóa động và ngôn ngữ từ ContextVar. | 🟢 PASS |
| `test_format_tool_limit_notice_dynamic_for_any_tool_without_hardcoded_dictionary` | Kiểm tra 100+ tools bất kỳ đều tự động định dạng mà không cần từ điển. | 🟢 PASS |
| `test_route_after_tools_recognizes_english_and_vietnamese_prefixes` | Kiểm tra router tóm tắt nhận diện cả tiền tố tiếng Anh và tiếng Việt. | 🟢 PASS |

> **Thống kê:** 10/10 tests passed trong 0.001s.

### 9.2. Bộ kiểm thử hồi quy (`tests/test_refactor_regressions.py`)
Kiểm tra tính tương thích ngược của toàn bộ hệ thống (Map-Reduce, Native Async Follow-ups, Rate Limit Handler):
> **Thống kê:** 28/28 tests passed trong 0.050s. Không có bất kỳ lỗi hồi quy hay xung đột nào với kiến trúc hiện hữu.

---

## 10. TỔNG KẾT ĐỐI CHIẾU KIẾN TRÚC TRƯỚC VÀ SAU REFACTOR

| Thành phần | Kiến trúc cũ | Kiến trúc mới chuẩn hóa |
| :--- | :--- | :--- |
| **Bóc tách Rate Limit** | Dùng Regex quét chuỗi lỗi tiếng Anh | Bóc tách trực tiếp HTTP Response Header chuẩn RFC 7231 |
| **Duyệt Exception** | Kiểm tra bề mặt `exc.response` $\rightarrow$ Thường xuyên bị `None` | Duyệt đệ quy cây lỗi `__cause__` & `__context__` kèm `seen_ids` chống lặp |
| **Xử lý TPD vs TPM** | Chờ mù quáng hoặc nhầm lẫn giữa TPD và TPM | Phân tách rạch ròi: TPM retry tự động, TPD fail-fast trong 0s gom kết quả |
| **Luồng Async** | Nguy cơ dùng `time.sleep()` làm nghẽn Main Thread | Tách biệt `invoke_...` (đồng bộ) và `ainvoke_...` (`asyncio.sleep()`) |
| **Từ điển tên Tool** | Hardcode `_TOOL_NAMES_VI` và `_TOOL_NAMES_EN` | Xóa sạch từ điển, dùng `format_tool_display_name` động cho 100+ tools |
| **Xử lý ngôn ngữ Chat** | Không có chỉ thị rõ ràng cho LLM | LLM tự hiểu và chuyển ngữ linh hoạt theo câu hỏi mới nhất (0% regex) |
| **Xử lý ngôn ngữ Lỗi** | Hardcode cố định `language="vi"` ở khắp mọi nơi | Request-scoped qua `ContextVar`, đọc từ HTTP Header BCP 47 (`X-Language`) |
| **Mã ngôn ngữ quốc tế** | Nguy cơ cắt 2 ký tự làm hỏng mã 3 ký tự (e.g. `fil` $\rightarrow$ `fi`) | BCP 47 chuẩn tách trước `-` hoặc `_`, bảo toàn mọi mã ngôn ngữ ISO |
| **Định tuyến tóm tắt** | Chỉ kiểm tra tiền tố tiếng Anh khiến tóm tắt tiếng Việt bị gọi lại chatbot | Kiểm tra cả `SUMMARY_RESULT_PREFIX` và `SUMMARY_RESULT_PREFIX_VI`, trả thẳng kết quả |
