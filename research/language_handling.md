# Giải thích xử lý ngôn ngữ trong project

> Tài liệu này giải thích **toàn bộ phần code mới liên quan đến xử lý ngôn ngữ Việt/Anh**: cách server chọn ngôn ngữ, cách truyền ngôn ngữ đến chatbot, cách dịch thông báo lỗi và cách xử lý các câu ngắn. Đây không phải tài liệu mô tả mọi tính năng khác của chatbot như đăng nhập, tìm kiếm web hay lưu bộ nhớ.

## 1. Ý tưởng tổng quát

Hệ thống có hai loại nội dung cần xử lý ngôn ngữ:

| Nội dung | Ví dụ | Cách xử lý |
| --- | --- | --- |
| Nội dung hội thoại | Câu trả lời của chatbot, câu hỏi gợi ý, phần phân tích ảnh | Gửi chỉ dẫn ngôn ngữ vào prompt của mô hình. Thông thường chatbot trả lời bằng ngôn ngữ người dùng vừa dùng. |
| Thông báo do chương trình tạo | Lỗi TPM, hết quota ngày, không tìm thấy ảnh/tài liệu | Lấy câu đã dịch từ `locales/vi.json` hoặc `locales/en.json`, không tự ghép câu ở nhiều nơi trong code. |
| Log kỹ thuật | Exception, chẩn đoán lỗi, thông tin retry | Ghi bằng tiếng Anh để đội phát triển có một ngôn ngữ log thống nhất. |

Luồng đơn giản:

```text
Tin nhắn người dùng
        │
        ▼
Server chọn vi hoặc en
        ├── lưu lựa chọn vào cuộc trò chuyện
        ├── gửi chỉ dẫn ngôn ngữ cho chatbot và các tác vụ LLM
        ├── dùng catalog nếu chương trình cần tự hiển thị thông báo
        └── trả Content-Language để trình duyệt dùng tiếp
```

## 2. Những khái niệm cần biết

- **Locale / mã ngôn ngữ:** `vi` là tiếng Việt, `en` là tiếng Anh. Đây là dạng ngắn dùng trong code và HTTP.
- **Detector:** thư viện đọc văn bản và ước lượng văn bản thuộc ngôn ngữ nào. Project dùng Lingua, chỉ bật hai ngôn ngữ Việt và Anh.
- **Fallback:** lựa chọn thay thế khi câu quá ngắn, detector không chắc, hoặc thư viện chưa được cài.
- **Catalog:** các câu dịch được lưu theo mã ổn định, ví dụ `rate_limit`, trong hai file JSON.
- **ContextVar:** biến theo ngữ cảnh của một request. Nó cho phép các tool đang chạy bên trong request biết ngôn ngữ của request đó mà không cần truyền thêm tham số qua mọi hàm.
- **Header:** thông tin metadata của HTTP request/response. `Accept-Language` là lựa chọn trình duyệt gửi lên; `Content-Language` là ngôn ngữ server dùng cho kết quả.

## 3. File trung tâm: `services/language.py`

File này tập trung các hàm ngôn ngữ để những phần khác của ứng dụng gọi chung.

### 3.1. Chỉ chấp nhận hai ngôn ngữ

```python
LanguageCode = Literal["vi", "en"]
DEFAULT_LANGUAGE: LanguageCode = "vi"
```

`Literal` báo cho người đọc và công cụ kiểm tra kiểu rằng giá trị hợp lệ dự kiến chỉ là `"vi"` hoặc `"en"`. `DEFAULT_LANGUAGE` được dùng khi không tìm được lựa chọn nào khác.

### 3.2. Nạp detector khi cần

```python
@lru_cache(maxsize=1)
def _detector():
    try:
        from lingua import Language, LanguageDetectorBuilder
    except ImportError:
        LOGGER.error("Lingua language detector is unavailable. Install project dependencies ...")
        return None
    return LanguageDetectorBuilder.from_languages(
        Language.VIETNAMESE,
        Language.ENGLISH,
    ).build()
```

- `@lru_cache(maxsize=1)` giữ lại một detector để không tạo lại ở mỗi tin nhắn.
- Import Lingua nằm bên trong hàm. Vì vậy, nếu máy chưa cài Lingua, app vẫn có thể khởi động; khi đó server ghi log tiếng Anh và dùng fallback thay vì dừng ngay lúc import module.
- `from_languages(...)` giới hạn detector ở đúng hai ngôn ngữ project hỗ trợ.
- Nếu Lingua thiếu, tự nhận diện chưa chạy được. Cần cài dependency theo phần 10.

### 3.3. Chuẩn hóa mã ngôn ngữ

```python
def normalize_language(value: str | None) -> LanguageCode | None:
    if not value:
        return None
    normalized = value.strip().lower().replace("_", "-").split("-", 1)[0]
    normalized = {
        "vietnamese": "vi", "english": "en",
        "tiếng việt": "vi", "tieng viet": "vi",
        "tiếng anh": "en", "tieng anh": "en",
    }.get(normalized, normalized)
    return normalized if normalized in ("vi", "en") else None
```

Ví dụ:

| Đầu vào | Kết quả |
| --- | --- |
| `EN`, `en-US`, `en_US`, `English` | `en` |
| `vi`, `vi-VN`, `Vietnamese`, `tiếng Việt` | `vi` |
| `fr-FR`, chuỗi rỗng, `None` | `None` vì project chưa hỗ trợ tiếng Pháp |

Hàm này không đoán ngôn ngữ. Nó chỉ đưa các cách viết đã biết về mã chuẩn hoặc báo “không nhận diện được”.

### 3.4. Nhận diện câu đủ dài và đủ chắc

```python
def detect_language(text: str | None) -> LanguageCode | None:
    clean_text = (text or "").strip()
    if len(clean_text) < 12:
        return None
    try:
        detector = _detector()
        if detector is None:
            return None
        scores = detector.compute_language_confidence_values(clean_text)
    except Exception:
        LOGGER.exception("Language detection failed; using configured fallback.")
        return None
    if not scores:
        return None
    winner = scores[0]
    runner_up = scores[1].value if len(scores) > 1 else 0.0
    try:
        min_score = float(os.getenv("LANGUAGE_DETECTION_MIN_SCORE", "0.60"))
        min_margin = float(os.getenv("LANGUAGE_DETECTION_MIN_MARGIN", "0.15"))
    except (TypeError, ValueError):
        min_score, min_margin = 0.60, 0.15
    min_score = min(max(min_score, 0.0), 1.0)
    min_margin = min(max(min_margin, 0.0), 1.0)
    if winner.value < min_score or winner.value - runner_up < min_margin:
        return None
    code = winner.language.iso_code_639_1.name.lower()
    return normalize_language(code)
```

- Câu dưới 12 ký tự được coi là quá ngắn. Ví dụ `"Hi"`, `"OK"`, `"Thanks"` có thể không đủ thông tin.
- Lingua xếp ứng viên phù hợp nhất lên đầu. `winner` là ứng viên đầu; `runner_up` là ứng viên đứng thứ hai.
- `LANGUAGE_DETECTION_MIN_SCORE` yêu cầu điểm ứng viên đầu tối thiểu `0.60`.
- `LANGUAGE_DETECTION_MIN_MARGIN` yêu cầu ứng viên đầu hơn ứng viên thứ hai ít nhất `0.15`.
- Không đạt một trong hai ngưỡng thì trả `None`, nghĩa là “chưa đủ chắc”; code gọi tiếp fallback thay vì đoán bừa.
- Nếu detector gặp lỗi, exception được ghi vào log tiếng Anh và hàm cũng trả `None`.

### 3.5. Chọn ngôn ngữ cuối cùng

```python
def resolve_language(text, *, explicit=None, previous=None, accept_language=None):
    return (
        normalize_language(explicit)
        or detect_language(text)
        or normalize_language(previous)
        or _preferred_accept_language(accept_language)
        or normalize_language(os.getenv("DEFAULT_RESPONSE_LANGUAGE"))
        or DEFAULT_LANGUAGE
    )
```

Python thử lần lượt từ trái sang phải; kết quả hợp lệ đầu tiên sẽ được chọn. Thứ tự cụ thể:

1. `explicit`: API client chủ động truyền `response_language="en"` hoặc `"vi"`.
2. `text`: nhận diện ngôn ngữ câu người dùng vừa gõ.
3. `previous`: ngôn ngữ đã lưu cho thread, hữu ích khi tin mới là `"OK"` hoặc câu ngắn.
4. `Accept-Language`: lựa chọn trình duyệt gửi lên.
5. `DEFAULT_RESPONSE_LANGUAGE`: cấu hình của server.
6. `DEFAULT_LANGUAGE`: cuối cùng là tiếng Việt.

**Lưu ý:** giao diện web hiện gửi `language_text` để detector đọc, nhưng không gửi `response_language` do người dùng tự chọn. `explicit` dành cho API client hoặc giao diện có lựa chọn ngôn ngữ nếu được bổ sung sau. Nếu người dùng viết tiếng Việt nhưng yêu cầu nội dung được dịch sang tiếng Anh, model có thể làm theo yêu cầu dịch; các thông báo tự tạo của server vẫn theo ngôn ngữ đã giải quyết cho request.

### 3.6. Đọc `Accept-Language`

Trình duyệt có thể gửi header dạng:

```text
Accept-Language: en-US,en;q=0.9,vi;q=0.8
```

`_preferred_accept_language()` tách danh sách theo dấu phẩy, chuẩn hóa từng mục về `en`/`vi`, rồi chọn mục có `q` cao nhất. `q` là mức ưu tiên do trình duyệt gửi. Ngôn ngữ không hỗ trợ bị bỏ qua.

Code chính của hàm:

```python
def _preferred_accept_language(header: str | None) -> LanguageCode | None:
    if not header:
        return None
    choices = []
    for index, item in enumerate(header.split(",")):
        parts = item.strip().split(";")
        language = normalize_language(parts[0])
        quality = 1.0
        for parameter in parts[1:]:
            key, separator, value = parameter.strip().partition("=")
            if separator and key.lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if language and quality > 0:
            choices.append((quality, -index, language))
    return max(choices, default=(0.0, 0, None))[2]
```

`-index` giúp giữ thứ tự ban đầu nếu hai ngôn ngữ có cùng `q`: mục xuất hiện trước được ưu tiên.

### 3.7. Giữ ngôn ngữ trong phạm vi một request

```python
_current_language: ContextVar[LanguageCode] = ContextVar(
    "response_language", default=DEFAULT_LANGUAGE
)

token = set_current_language("en")
try:
    # Xử lý request và chạy tools
    ...
finally:
    reset_current_language(token)
```

`set_current_language()` lưu ngôn ngữ vào context hiện tại và trả về `token`, là dấu mốc để khôi phục giá trị cũ. `reset_current_language()` được gọi trong `finally`, nên vẫn chạy khi request kết thúc bình thường hoặc gặp lỗi. Cách này quan trọng khi nhiều người chat cùng lúc: request tiếng Anh không được làm request tiếng Việt khác bị đổi ngôn ngữ.

Tool có thể gọi `get_current_language()` để biết request hiện tại là `vi` hay `en`.

### 3.8. Lấy câu từ catalog

```python
def tr(key: str, language: str | None = None, **values) -> str:
    locale = normalize_language(language) or get_current_language()
    template = _catalog(locale).get(key)
    if template is None:
        LOGGER.error("Missing localization key '%s' for language '%s'.", key, locale)
        template = _catalog(DEFAULT_LANGUAGE).get(key, key)
    return template.format(**values)
```

Ví dụ `tr("rate_limit", "en", model="model-x")` lấy mẫu tiếng Anh rồi thay `{model}` bằng `model-x`. `tr()` là điểm tra cứu chung; các câu dịch nằm trong JSON, không rải điều kiện `if Vietnamese ... else English` qua nhiều service.

`_catalog()` mở file đúng một lần cho mỗi ngôn ngữ nhờ cache:

```python
@lru_cache(maxsize=2)
def _catalog(language: LanguageCode) -> dict[str, str]:
    path = _LOCALE_DIR / f"{language}.json"
    with path.open(encoding="utf-8") as catalog_file:
        return json.load(catalog_file)
```

`maxsize=2` vừa bằng số locale đang hỗ trợ, nên lần tra sau không cần đọc lại file JSON từ đĩa.

## 4. Server nhận tin nhắn và xác định ngôn ngữ (`app.py`)

### 4.1. Vì sao gửi riêng `language_text`?

Frontend gửi hai trường:

```javascript
body: JSON.stringify({
    message: submittedMessage,
    language_text: message,
    thread_id: targetThreadId,
    model: selectedModel
})
```

`message` có thể chứa thêm nhãn file đính kèm như `[Attached file: report.pdf]`. `language_text` chỉ chứa phần người dùng tự gõ, nên detector không bị tên file hoặc nhãn kỹ thuật làm nhiễu. Server vẫn gửi `message` đầy đủ cho chatbot để chatbot biết ngữ cảnh file.

### 4.2. Resolve, lưu và gửi cho agent

```python
response_language = resolve_language(
    data.get("language_text") or user_message,
    explicit=data.get("response_language"),
    previous=conv.get("language") if conv else None,
    accept_language=request.headers.get("accept-language"),
)

create_or_update_conversation(
    thread_id, user_message, user_id=user_id, language=response_language
)

config = {"configurable": {
    "thread_id": thread_id,
    "response_language": response_language,
}}
```

Diễn giải:

1. Lấy câu người dùng vừa viết, ngôn ngữ đã lưu, header trình duyệt và lựa chọn explicit nếu có.
2. `resolve_language()` chọn đúng một mã: `vi` hoặc `en`.
3. Lưu mã đó trên document conversation trong MongoDB để lần chat sau dùng làm fallback.
4. Đưa nó vào `configurable.response_language` của LangGraph. `configurable` là vùng cấu hình theo lần chạy, không phải biến global dùng chung cho mọi người.

Các conversation cũ chưa có trường `language` vẫn dùng được: `conv.get("language")` trả `None`, và server đi tiếp theo các fallback còn lại.

### 4.3. Báo ngôn ngữ cho trình duyệt

```python
headers={
    "Content-Language": response_language,
    ...
}
```

Header này nói rằng phản hồi hiện tại là tiếng Việt hay tiếng Anh. Frontend đọc header sau khi nhận response và giữ lại giá trị đó để tạo follow-up và chọn ngôn ngữ nhận dạng giọng nói.

### 4.4. Lỗi trong stream

Khi stream lỗi, `app.py` phân loại lỗi như TPM, model không khả dụng hoặc lỗi chung. Ví dụ:

```python
yield sse_data({
    "error": tr("rate_limit", response_language, model=selected_model)
})
```

`sse_data()` đóng gói dữ liệu thành sự kiện SSE để frontend nhận được. `tr()` dịch thông báo theo `response_language`. Exception chi tiết vẫn được đưa vào log tiếng Anh; người dùng nhận câu dễ hiểu, không bị lộ nguyên nội dung lỗi từ provider.

Khi chạm quota ngày (TPD), `handle_partial_stream()` giữ câu đã stream được và thêm phần thông báo bằng đúng ngôn ngữ của request.

```python
def handle_partial_stream(accumulated_text, model_name, language="vi"):
    clean_accumulated = (accumulated_text or "").strip()
    if clean_accumulated:
        return clean_accumulated + tr(
            "daily_stream_partial", language, model=model_name
        )
    return tr("daily_stream_empty", language, model=model_name)
```

Nếu đã có chữ stream ra, code nối thêm cảnh báo. Nếu model chưa tạo ra chữ nào, code trả về câu báo quota riêng. Cả hai mẫu đều lấy từ catalog.

## 5. Chatbot nhận chỉ dẫn (`agent.py` và `prompts/agent.py`)

Node `chatbot_node` lấy ngôn ngữ từ LangGraph config và thêm chỉ dẫn ngôn ngữ vào system prompt:

```python
response_language = (config.get("configurable") or {}).get(
    "response_language", "vi"
)
localized_system_prompt = (
    f"{SYSTEM_PROMPT}\n\n"
    f"{response_language_instruction(response_language)}"
)
```

Sau đó `localized_system_prompt` được truyền vào `prepare_context_for_llm()` cùng lịch sử chat. System prompt là hướng dẫn cấp cao để model biết cần trả lời bằng ngôn ngữ nào. Nếu thiếu config, node dùng `vi` làm dự phòng.

`prompts/agent.py` vẫn giữ các hướng dẫn nghiệp vụ chung: gọi tool phù hợp, giữ ngôn ngữ gốc khi tóm tắt file, không bịa thông tin. Chỉ dẫn vi/en của từng request được ghép lúc chạy; không cần tạo hai bản agent riêng.

## 6. Các tác vụ LLM khác

### 6.1. Follow-up (`services/followups.py`, `prompts/followups.py`)

Service nhận thêm `response_language` và gửi một `SystemMessage` riêng trước nội dung trao đổi:

```python
language_instruction = response_language_instruction(
    normalize_language(response_language) or "vi"
)
response = await structured_model.ainvoke([
    SystemMessage(content=language_instruction),
    HumanMessage(content=prompt),
])
```

`SystemMessage` nói ngôn ngữ phải dùng; `HumanMessage` chứa câu hỏi và câu trả lời để model dựa vào tạo đúng hai gợi ý. Prompt trong `prompts/followups.py` yêu cầu làm theo chỉ dẫn system, không còn ví dụ mặc định nghiêng về tiếng Việt.

Frontend gửi `response_language` lấy từ header `Content-Language`. Vì vậy gợi ý theo sau cùng ngôn ngữ với lượt chat vừa hoàn thành.

### 6.2. Phân tích ảnh (`services/image_analysis.py` và `tools.py`)

`analyze_uploaded_image` lấy ngôn ngữ request qua `get_current_language()` và truyền vào `analyze_image()`. Service tạo chỉ dẫn ngôn ngữ bằng `response_language_instruction()` rồi chèn vào prompt gửi cho mô hình vision. Nếu image model chạm TPD, thông báo lấy từ locale catalog.

Phần tạo prompt có dạng:

```python
language_instruction = response_language_instruction(
    response_language or get_current_language()
)
prompt = (
    "Answer the user's question using the image. "
    f"Do not guess. {language_instruction}\n\n"
    f"Question: {clean_question or 'Describe this image.'}"
)
```

Như vậy model vision nhận cả câu hỏi lẫn chỉ dẫn bằng ngôn ngữ kết quả cần dùng.

Các thông báo tool khác cũng được dịch bằng key, ví dụ không có ảnh, tên ảnh không rõ, hoặc lỗi phân tích. Exception chi tiết được ghi log tiếng Anh; câu trả về cho model/chat là câu dịch gọn.

### 6.3. Tóm tắt tài liệu (`tools.py` và `services/token_limit_handler.py`)

Tool tóm tắt có ba trường hợp:

1. Nếu model/tool nhận được `target_language` cụ thể là Vietnamese/English, dùng ngôn ngữ đó.
2. Nếu `target_language="auto"`, ghép nội dung các chunk rồi dùng detector nhận diện ngôn ngữ tài liệu.
3. Nếu detector chưa chắc hoặc thiếu Lingua, dùng ngôn ngữ hội thoại hiện tại.

Lý do detect tài liệu riêng: yêu cầu nghiệp vụ là mặc định tóm tắt theo **ngôn ngữ gốc của tài liệu**, không nhất thiết theo ngôn ngữ câu hỏi.

Đoạn quyết định ngôn ngữ tài liệu trong `tools.py`:

```python
requested_target = normalize_language(target_language)
normalized_target = requested_target or get_current_language()

chunks = get_document_chunks(selected, get_current_thread_id(), user_id=user_id)
if requested_target is None and target_language.lower() in ("auto", "original", "default"):
    document_text = "\n".join(chunk.page_content for chunk in chunks)
    normalized_target = detect_language(document_text) or get_current_language()

summary_language = "Vietnamese" if normalized_target == "vi" else "English"
summary = summarize_documents(chunks, target_language=summary_language)
```

`chunks` là những đoạn văn bản nhỏ được trích từ file. Code ghép phần chữ của các đoạn để detector có đủ nội dung; nếu kết quả không rõ, nó dùng ngôn ngữ thread. Sau đó tên ngôn ngữ đầy đủ được gửi cho service summarization hiện có.

Nếu TPD ngắt quy trình map-reduce giữa chừng, `assemble_partial_summary()` tạo bản một phần:

- Tiêu đề và nhãn phần dùng ngôn ngữ tóm tắt/tài liệu.
- Phần cảnh báo cuối dùng ngôn ngữ hội thoại.

Các mẫu nằm trong `locales/vi.json` và `locales/en.json`, được ghép qua `tr()`.

Hàm tạo bản một phần thực hiện ba bước: sắp xếp các phần đã hoàn thành theo thứ tự, tạo tiêu đề và từng đoạn bằng ngôn ngữ tóm tắt, rồi thêm footer/cảnh báo bằng ngôn ngữ conversation. Ví dụ phần lấy nội dung dịch:

```python
prefix = tr("daily_summary_prefix", summary_language, ...)
body = "\n\n".join(
    tr("daily_summary_section", summary_language, ...)
    for index, summary_text in sorted_groups
)
footer = tr("daily_summary_footer", user_language, ...)
return f"{prefix}{body}{footer}"
```

## 7. Các file locale

Hai file `locales/vi.json` và `locales/en.json` có cùng các key. Ví dụ:

```json
{
  "rate_limit": "⚠️ Model `{model}` has temporarily reached its requests-per-minute limit (TPM). Please try again shortly.",
  "summary_error": "Could not summarize \"{filename}\". Please try again."
}
```

Trong tiếng Việt, cùng key có nội dung tiếng Việt:

```json
{
  "rate_limit": "⚠️ Mô hình `{model}` đang tạm thời chạm giới hạn tốc độ theo phút (TPM). Vui lòng thử lại sau.",
  "summary_error": "Không thể tóm tắt \"{filename}\". Vui lòng thử lại."
}
```

`{model}` và `{filename}` là chỗ trống. Khi gọi `tr()`, code phải truyền đủ giá trị để lấp chỗ trống. Muốn thêm câu hiển thị mới thì thêm cùng một key vào cả hai JSON rồi gọi `tr("key_moi", language, ...)` trong code.

## 8. Giao diện và giọng nói (`templates/index.html`)

Frontend có biến:

```javascript
let currentResponseLanguage =
    navigator.language?.toLowerCase().startsWith("en") ? "en" : "vi";
```

Trước khi server trả header, biến này khởi tạo từ ngôn ngữ trình duyệt. Sau khi nhận response, frontend cập nhật theo `Content-Language`. Speech recognition dùng biến này để chọn `en-US` hoặc `vi-VN`.

Đối với follow-up, hàm `fetchParallelFollowups()` gửi biến đó trong JSON body. Như vậy luồng chính và gợi ý không tự quyết định hai ngôn ngữ khác nhau.

## 9. Lưu ngôn ngữ trên conversation (`storage/database.py`)

`create_or_update_conversation()` nhận thêm tham số `language`. Chỉ `vi` và `en` mới được đưa vào `$set` của MongoDB:

```python
set_fields = {"updated_at": now}
if language in {"vi", "en"}:
    set_fields["language"] = language
```

`$set` cập nhật trường đó khi conversation đã tồn tại, hoặc lưu nó khi tạo mới. Chỉ lưu hai mã hợp lệ giúp dữ liệu thread nhất quán. Conversation cũ không cần migrate trước; chúng chỉ chưa có giá trị fallback cho đến tin nhắn tiếp theo.

## 10. Cài Lingua và cấu hình

Dependency được khai báo trong `requirements.txt`:

```text
lingua-language-detector==2.1.1
```

Cài vào **đúng Python environment dùng để chạy app**:

```powershell
conda activate tit
python -m pip install -r requirements.txt
```

Nếu chưa cài Lingua, app vẫn có thể chạy nhưng tự nhận diện bỏ qua và dùng fallback. Cấu hình tùy chọn:

| Biến môi trường | Mặc định | Tác dụng |
| --- | --- | --- |
| `DEFAULT_RESPONSE_LANGUAGE` | `vi` | Ngôn ngữ cuối cùng nếu không có lựa chọn nào khác; chỉ nên là `vi` hoặc `en`. |
| `LANGUAGE_DETECTION_MIN_SCORE` | `0.60` | Điểm tối thiểu của ngôn ngữ đứng đầu. |
| `LANGUAGE_DETECTION_MIN_MARGIN` | `0.15` | Khoảng cách tối thiểu giữa ứng viên đầu và ứng viên thứ hai. |

Nếu giá trị ngưỡng không chuyển được thành số, code trở về `0.60` và `0.15`; ngưỡng cũng được giới hạn trong khoảng 0–1.

## 11. Ví dụ từ đầu đến cuối

### Ví dụ A: Người dùng hỏi bằng tiếng Anh

1. Trình duyệt gửi message và `language_text`.
2. Server gọi `resolve_language()`.
3. Lingua nhận diện `en` với đủ độ tin cậy.
4. Server lưu `language: "en"` trong conversation, gửi `response_language: "en"` cho LangGraph và trả `Content-Language: en`.
5. System prompt yêu cầu model trả lời tiếng Anh.
6. Nếu gặp lỗi TPM, server lấy câu `rate_limit` từ `locales/en.json`.
7. Frontend lưu `en`, dùng tiếng Anh cho follow-up và `en-US` cho speech recognition.

### Ví dụ B: Thread đang tiếng Việt, tin nhắn mới là “OK”

1. “OK” ngắn hơn 12 ký tự nên detector không đoán.
2. Server đọc `language: "vi"` đã lưu trong conversation.
3. Lượt chat tiếp tục dùng tiếng Việt.

### Ví dụ C: Máy chưa cài Lingua

1. App vẫn khởi động vì Lingua chỉ được import khi cần nhận diện.
2. Log tiếng Anh ghi rằng detector không có và hướng dẫn cài dependency.
3. Request dùng ngôn ngữ thread, `Accept-Language` hoặc mặc định.
4. Sau khi cài Lingua vào đúng environment và khởi động lại app, detector được dùng tự động.

## 12. Khi thêm một ngôn ngữ mới

Không chỉ thêm một câu `if/else`. Cần cập nhật đồng bộ:

1. `LanguageCode` và `normalize_language()` trong `services/language.py`.
2. Danh sách ngôn ngữ khi tạo Lingua detector.
3. Một catalog JSON hoàn chỉnh cho ngôn ngữ mới, dùng cùng key với hai catalog hiện tại.
4. Giá trị fallback và kiểm tra input ở API/database.
5. Cấu hình speech recognition ở frontend.
6. Tài liệu và các luồng follow-up, ảnh, tóm tắt, thông báo lỗi.

## 13. Những giới hạn cần nhớ

- Project hiện chỉ hỗ trợ tiếng Việt và tiếng Anh; ngôn ngữ khác sẽ không được Lingua chọn.
- Tin nhắn quá ngắn hoặc trộn hai ngôn ngữ có thể bị coi là chưa rõ. Đây là chủ ý để dùng fallback thay vì đoán sai.
- Khi thiếu Lingua, app không tự nhận diện câu mới; cần cài requirements vào environment đang chạy.
- Detector chọn ngôn ngữ câu/tài liệu; nó không phân tích ý định dịch một cách riêng biệt. Model vẫn nhận được câu người dùng và có thể làm theo yêu cầu dịch trong nội dung hội thoại.
- Log kỹ thuật giữ tiếng Anh. Câu hiển thị cho người dùng lấy từ catalog theo ngôn ngữ request.
