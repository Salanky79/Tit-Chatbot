
# MỤC 2: LUỒNG FOLLOW-UP SUGGESTIONS & KIẾN TRÚC BẤT ĐỒNG BỘ TRONG FASTAPI

## 2.1. Bản Chất Luồng Follow-up Hiện Tại

Hệ thống đang phục vụ gợi ý câu hỏi tiếp theo thông qua endpoint độc lập:
`POST /chat/followups` ([app.py dòng 1110](file:///d:/Tit-Chatbot/app.py#L1110)).

### Thời điểm kích hoạt:
* **Không phải khi người dùng vừa gõ prompt.**
* **Chỉ kích hoạt SAU KHI chatbot stream xong toàn bộ câu trả lời** (nằm trong khối `finally` của JavaScript frontend tại `templates/index.html` dòng 3588-3592).
* **Lý do:** Hàm `generate_followup_questions` bắt buộc phải có cả:
  1. `user_message`: Câu hỏi của người dùng.
  2. `assistant_answer`: Câu trả lời hoàn chỉnh của chatbot.
  *(Nếu bot chưa trả lời xong thì không thể biết ngữ cảnh để gợi ý câu hỏi tiếp theo).*

---

## 2.2. Thảo Luận: Có Nên Đưa Follow-up Thành Node Song Song Trong LangGraph Agent?

### A. Tại sao không thể chạy song song từ đầu (`START`)?
Vì quan hệ phụ thuộc dữ liệu (Data Dependency): Gợi ý follow-up phụ thuộc vào kết quả trả lời cuối cùng (`final answer`) của node `chatbot`. Khi user vừa hỏi, bot còn phải tra cứu RAG, phân tích ảnh, tính toán... nên node follow-up bắt buộc phải đứng sau node `chatbot`.

### B. Bảng so sánh giữa 2 phương án:

| Tiêu chí | API Riêng Biệt (Hiện tại - Decoupled) | Node trong LangGraph Graph |
| :--- | :--- | :--- |
| **Độ trễ người dùng cảm nhận (UX Latency)** | **0ms (Tối ưu tuyệt đối)**<br>Bot stream xong chữ cuối cùng là ô nhập chat mở khóa ngay (`Ready`), user chat tiếp được luôn. Gợi ý nảy lên sau đó 0.5s - 1s mà không làm gián đoạn người dùng. | **Bị trễ thêm 0.5s - 1.5s**<br>Vì SSE stream phải giữ kết nối mở để đợi cả `followup_node` chạy xong mới phát tín hiệu `[DONE]`. Người dùng sẽ thấy trạng thái "Thinking..." lâu hơn. |
| **Khả năng Hủy (Abort Control)** | **Rất linh hoạt**<br>Nếu người dùng gõ câu tiếp theo quá nhanh, Frontend dùng `AbortController` hủy ngay request `/chat/followups` đang chạy dở để tiết kiệm token API. | **Rất khó hủy**<br>Khi đồ thị LangGraph đã kích hoạt chạy node, việc hủy ngang một branch chạy ngầm từ phía frontend phức tạp hơn nhiều. |
| **Cô lập lỗi (Fault Isolation)** | **Tuyệt đối an toàn**<br>Nếu Groq sinh gợi ý bị lỗi/timeout/rate limit $\rightarrow$ chỉ đơn giản là không hiện nút bấm gợi ý, câu trả lời chính của chatbot không bị ảnh hưởng. | **Rủi ro đồ thị**<br>Nếu node followup gặp ngoại lệ mà không xử lý kỹ, có thể làm fail cả lượt chạy của LangGraph hoặc làm treo stream SSE. |
| **Lưu trữ State (Checkpointer)** | **Sạch sẽ**<br>Gợi ý chỉ là dữ liệu tạm thời trên giao diện (UI state), không bị lưu đè vào MongoDB checkpoint của Agent. | **Phình to State**<br>State của LangGraph sẽ phải thêm trường `suggestions`, lưu cả vào DB checkpointer dù sau đó không dùng lại. |

👉 **Kết luận kiến trúc:** Kiến trúc API riêng hiện tại tương đồng với cách các sản phẩm như ChatGPT, Perplexity hay Claude vận hành luồng gợi ý phụ. Luồng phản hồi chính luôn được ưu tiên tối đa.

---

## 2.3. Bản Chất Concurrency Trong FastAPI: Sự Thật Về Event Loop

FastAPI chạy trên nền **Uvicorn**, và cốt lõi của nó là **một Event Loop chạy trên một luồng đơn (Single Thread)**.

> Ẩn dụ kinh điển: **Anh bồi bàn trong nhà hàng (Event Loop)**.
> Nhà hàng chỉ có 1 anh bồi bàn duy nhất phục vụ hàng chục bàn ăn (các request).

---

### Mổ xẻ 3 Trường Hợp Lập Trình Concurrency Trong FastAPI:

```
[Request tới FastAPI]
       │
       ├── TH1: async def + hàm sync (blocking)  ──► Đứng chết trân tại chỗ (ĐƠ TOÀN SERVER)
       │
       ├── TH2: async def + asyncio.to_thread   ──► Đẩy việc cho phụ bếp (Worker Thread)
       │
       └── TH3: async def + native await        ──► Đặt chuông hẹn giờ ở OS Kernel (KHÔNG CẦN THREAD PHỤ, KHÔNG ĐƠ)
```

---

### 🔴 Trường hợp 1: Chạy hàm đồng bộ (Sync/Blocking) trong `async def` (TỆ NHẤT)

```python
@app.post("/chat/followups")
async def chat_followups(request: Request):
    data = await request.json()
    # GỌI TRỰC TIẾP HÀM BLOCKING:
    questions = generate_followup_questions(...) 
    return {"suggestions": questions}
```

* **Cơ chế:** Hàm `generate_followup_questions` dùng `model.invoke()` (lệnh gọi mạng đồng bộ tới Groq mất 1-2s). Trong lúc này, Python gọi `socket.recv()` ở chế độ blocking.
* **Hậu quả:** Anh bồi bàn (Event Loop) tự mình đứng canh nồi súp trong 2 giây. **Toàn bộ server bị đóng băng!** Các user khác gửi tin nhắn đến, hoặc stream token của câu chat khác đều bị khựng lại gián đoạn cho đến khi hàm này chạy xong!

---

### 🟡 Trường hợp 2: Dùng `asyncio.to_thread` (ĐẨY SANG LUỒNG PHỤ)

```python
questions = await asyncio.to_thread(
    generate_followup_questions,
    ...
)
```

* **Cơ chế:** Python lấy một luồng phụ (Worker Thread) trong ThreadPool của hệ điều hành và giao việc chạy `generate_followup_questions` cho luồng đó. Luồng phụ này sẽ chịu cảnh "đứng đợi" Groq.
* **Vì sao không bị đơ?** Từ khóa `await` giải phóng anh bồi bàn (Main Event Loop) ngay lập tức. Anh bồi bàn quay sang tiếp tục stream chat cho user khác. Khi luồng phụ nhận xong kết quả từ Groq, nó báo lại cho anh bồi bàn trả kết quả về.
* **Đặc điểm:** Cực kỳ hữu ích để tái sử dụng mã nguồn đồng bộ sẵn có (`def`) mà không cần đập đi viết lại. Tuy nhiên, nó tốn thêm tài nguyên bộ nhớ và chi phí chuyển đổi ngữ cảnh (Context Switching) của hệ điều hành khi tạo/quản lý thread.

---

### 🟢 Trường hợp 3: Native Async (`await model.ainvoke()`) (KHÔNG CẦN LUỒNG PHỤ MÀ VẪN KHÔNG BỊ ĐƠ)

Nhiều người thắc mắc: *Tại sao Trường hợp 3 vẫn chạy trên Main Thread, không dùng luồng phụ, mà lại KHÔNG HỀ BỊ ĐƠ?*

```python
async def generate_followup_questions(...):
    # DÙNG AINVOKE THAY VÌ INVOKE:
    response = await structured_model.ainvoke([HumanMessage(content=prompt)])
    return response
```

#### Bí mật nằm ở: Non-blocking Socket & I/O Multiplexing ở tầng Kernel Hệ Điều Hành
Bên dưới tầng mạng của máy tính, khi gửi HTTP request lên Groq:
1. **Gửi gói tin qua Socket Non-blocking:** Thư viện async (`httpx` / `aiohttp`) mở một TCP Socket tới Groq ở chế độ **Non-blocking (không khóa)** và gửi byte dữ liệu đi.
2. **Đăng ký sự kiện với Kernel:** Thay vì ngồi đợi, Python nói với Hệ điều hành (thông qua `IOCP` trên Windows hoặc `epoll` trên Linux):
   > *"Tôi vừa gửi request đến Groq trên cổng Socket #42. Khi nào server Groq gửi gói tin phản hồi về tới card mạng, hãy phát tín hiệu đánh thức tôi nhé!"*
3. **Từ khóa `await` lập tức buông tay:** Main Thread **hoàn toàn không phải chờ đợi gì cả**! Nó quay ngoắt sang xử lý hàng nghìn việc khác của các request khác.
4. **Không có bất kỳ luồng nào bị đóng băng:** Việc chờ đợi dữ liệu mạng bay từ Mỹ về Việt Nam hoàn toàn do **phần cứng card mạng và Kernel của Hệ điều hành** theo dõi thụ động. CPU không tốn 1 chu kỳ tính toán nào.
5. **Đánh thức khi có dữ liệu:** 1 giây sau, khi server Groq phản hồi, card mạng nhận byte $\rightarrow$ Kernel gửi tín hiệu ngắt (Event) tới Event Loop: *"Socket #42 đã có dữ liệu!"* $\rightarrow$ Event Loop quay lại đúng dòng code sau chữ `await` để đọc dữ liệu và xử lý tiếp.

---

### 💡 Bảng So Sánh 3 Trường Hợp Trong Đời Thực:

| Trường hợp | Ẩn dụ thực tế (Nhận hàng Shipper) | Trạng thái CPU & Luồng | Hiệu năng / Khả năng mở rộng |
| :--- | :--- | :--- | :--- |
| **TH1: Sync Blocking trong Async** | Bạn đặt đồ ăn. Bạn ra cổng đứng im bất động 30 phút chờ shipper. Ai gọi bạn cũng không trả lời. | Main Thread bị khóa 100%. Mọi request khác bị nghẽn. | ❌ Cực kỳ kém. Chỉ cần vài request là sập server. |
| **TH2: `asyncio.to_thread`** | Bạn thuê một ông bảo vệ ra cổng đứng chờ shipper hộ bạn. Bạn ở trong nhà tiếp tục làm việc. | Tốn thêm 1 Worker Thread cho mỗi request chờ mạng. | 🟡 Tốt, nhưng giới hạn bởi số lượng ThreadPool (thường 40-100 threads). |
| **TH3: Native Async (`await ainvoke`)** | Bạn dán tờ giấy trước cổng: *"Khi nào tới thì bấm chuông cửa"*. Bạn ở trong nhà làm việc. Chuông reo thì bạn ra lấy. | **1 Thread duy nhất** xử lý hàng chục ngàn kết nối đồng thời nhờ cơ chế Socket Event của Kernel. | 🟢 **Đỉnh cao kiến trúc**. Tốn cực ít RAM/CPU, chịu tải khổng lồ (C10K problem). |

---

## 2.4. Bản Chất Của `await`: Tại Sao Phải Có `await` Mới Lấy Được Kết Quả?

Nhiều người thường nhầm lẫn: *"Đã dùng `await` tức là phải đợi, vậy sao lại gọi là song song được? Bỏ `await` đi được không?"*

### A. "Song song" ở đây là song song với cái gì?
1. **Bản thân hàm `chat_followups` BẮT BUỘC PHẢI ĐỢI (`await`):** Điều này hiển nhiên vì hàm cần lấy 2 câu hỏi từ Groq thì mới có dữ liệu để đóng gói trả về cho Client.
2. **"Song song" với UI của Người Dùng:** Luồng chat chính (`/chat/stream`) đã kết thúc từ trước, ô gõ phím đã mở khóa. Người dùng đang ung dung đọc câu trả lời hoặc gõ câu hỏi mới, trong khi request gợi ý đang chạy ngầm song song bên dưới.
3. **"Song song" trên Máy Chủ:** Nhờ từ khóa `await`, Main Thread không bị khóa mà tiếp tục phục vụ các request chat khác song song với luồng phụ.

### B. Nếu BỎ chữ `await` ở dòng đó đi thì sao?
```python
# BỎ CHỮ AWAIT:
questions = asyncio.to_thread(generate_followup_questions, ...)
```
* **Hậu quả:** `asyncio.to_thread(...)` là hàm khởi tạo bất đồng bộ trả về một **đối tượng Task (Phiếu hẹn)**, chứ không phải dữ liệu thực tế:
  `questions = <Task pending coro=<to_thread()>>`
* Đến dòng kiểm tra `len(questions) == 2`, Python sẽ **ném ngoại lệ Crash server ngay lập tức**:
  `TypeError: object of type 'Task' has no len()`
* **Ẩn dụ đời thực:** Bạn mang quần áo đi giặt. Người ta đưa bạn **tờ giấy hẹn**. Bạn không đợi giặt xong mà cầm luôn tờ giấy hẹn nhét vào tủ quần áo! Rõ ràng là bạn chưa có chiếc áo nào để mặc.

---

## 2.5. Cách Để FastAPI Tự Động Quản Lý Luồng Phụ 100% (FastAPI Idiomatic Pattern)

Nhiều lập trình viên thắc mắc: *"Nếu FastAPI có sẵn ThreadPool, tại sao lại phải tự tay viết `asyncio.to_thread`?"*

### A. Nguyên nhân bị "trói tay" trong code cũ:
Trong [app.py dòng 1121](file:///d:/Tit-Chatbot/app.py#L1121), code cũ viết:
```python
@app.post("/chat/followups")
async def chat_followups(request: Request):
    data = await request.json()  # <--- BẮT BUỘC PHẢI DÙNG AWAIT
```
* Vì dùng `request: Request`, hàm `request.json()` đọc stream mạng dạng Async nên **bắt buộc phải có `await`**.
* Mà theo luật của Python: **Cứ có chữ `await` trong thân hàm thì đầu hàm BẮT BUỘC phải khai báo là `async def`!**
* Một khi đã là `async def`, hàm bị kéo về chạy trên Main Thread, thế là lại phải tự gọi `asyncio.to_thread` bằng tay.

### B. Giải pháp chuẩn FastAPI: Dùng `def` thường kết hợp Pydantic Model
Nếu thay `request: Request` bằng một Pydantic Model:
1. FastAPI sẽ tự động phân tích cú pháp JSON ở tầng ASGI trước.
2. Khi khai báo hàm bằng **`def` bình thường (bỏ hẳn `async`)**, FastAPI sẽ **tự động gọi `anyio.to_thread.run_sync` để ném cả hàm vào ThreadPool phụ**.
3. **Kết quả:** Vứt sạch `async`, vứt sạch `await` và `asyncio.to_thread`. Code sạch sẽ và hoàn toàn tự động!

---

## 2.6. ThreadPool Starvation & Đỉnh Cao Kiến Trúc "Native Async"

### A. Chuyện gì xảy ra nếu có quá nhiều request làm cạn kiệt luồng phụ (ThreadPool Starvation)?
* Mặc định, FastAPI (`anyio`) cấp sẵn một hồ bơi gồm **40 luồng phụ (Worker Threads)**.
* Mỗi request gọi hàm `def` đồng bộ sẽ chiếm dụng 1 luồng trong 1-2 giây để nói chuyện với Groq.
* **Nếu có 100 người dùng cùng lúc:**
  - 40 request đầu tiên chiếm trọn 40 luồng.
  - **Từ request thứ 41 đến 100:** Phải **xếp hàng chờ trong bộ nhớ đệm (Queue)**.
  - Server KHÔNG sập, Main Event Loop KHÔNG đơ (luồng chat chính `/chat/stream` vẫn mượt). Nhưng các câu hỏi gợi ý từ user 41 trở đi sẽ bị hiện ra chậm hơn (latency tăng).

### B. "Native Async" là gì? Tại sao Native Async giải quyết triệt để vấn đề này?
* **Native Async** là kỹ thuật viết code bất đồng bộ thuần túy từ gốc:
  - Khi gửi request qua mạng, nó mở **Non-blocking Socket** và đăng ký sự kiện trực tiếp với Kernel Hệ điều hành.
  - **Tốn chính xác 0 LUỒNG PHỤ!**
  - **1 Luồng chính duy nhất (Single Thread)** có thể gánh 10.000 kết nối cùng lúc mà không cần ThreadPool.
* **So sánh code trong dự án:**
  - *Cách dùng ThreadPool (Hiện tại):* `def generate_followup_questions(...)` $\rightarrow$ dùng `model.invoke()` $\rightarrow$ tốn 1 luồng phụ.
  - *Cách Native Async:* `async def generate_followup_questions(...)` $\rightarrow$ dùng `await model.ainvoke()` $\rightarrow$ **tốn 0 luồng phụ**.

---

## 2.7. Quy Tắc Vàng Khi Viết Code FastAPI Trong Dự Án

1. Nếu một hàm là **Native Async** (hỗ trợ `await` từ gốc như async database client, `ainvoke`):
   $\rightarrow$ Viết `async def` và gọi `await func()`.
2. Nếu muốn FastAPI **tự lo đa luồng cho code đồng bộ (blocking)**:
   $\rightarrow$ Viết hàm **`def` thường** (không có `async`), truyền tham số bằng **Pydantic Model**. FastAPI sẽ tự động ném vào ThreadPool.
3. Nếu bắt buộc phải dùng `async def` (do cần `await` một coroutine khác như `request.json()`) nhưng lại phải gọi hàm đồng bộ:
   $\rightarrow$ Bắt buộc phải bọc qua **`await asyncio.to_thread(func, *args)`**.
4. Tuyệt đối không bao giờ gọi hàm đồng bộ dạng blocking trực tiếp bên trong `async def`.

---

# MỤC 3: KẾ HOẠCH & KẾT QUẢ TRIỂN KHAI THỰC TẾ (EXECUTION & VERIFICATION)

Hệ thống đã triển khai thành công việc tái cấu trúc endpoint `/chat/followups` theo đúng kế hoạch:

### 3.1. Mã nguồn đã cập nhật trong `app.py`:
1. **Khai báo Pydantic Model:**
   ```python
   class FollowupRequest(BaseModel):
       user_message: str = ""
       assistant_answer: str = ""
       model: str = "openai/gpt-oss-20b"
   ```
2. **Chuyển đổi Endpoint sang `def` thường:**
   ```python
   @app.post("/chat/followups")
   def chat_followups(payload: FollowupRequest):
       if not FOLLOWUPS_ENABLED:
           return {"success": True, "suggestions": []}

       user_message = (payload.user_message or "").strip()
       assistant_answer = (payload.assistant_answer or "").strip()
       selected_model = payload.model or "openai/gpt-oss-20b"

       if not user_message or not assistant_answer:
           return {"success": True, "suggestions": []}

       try:
           questions = generate_followup_questions(
               user_message=user_message,
               assistant_answer=assistant_answer,
               model_name=selected_model,
           )
           return {
               "success": True,
               "suggestions": questions if len(questions) == 2 else []
           }
       except Exception as exc:
           logger.warning(f"Parallel follow-up generation failed: {exc}")
           return {"success": False, "suggestions": []}
   ```

### 3.2. Kết quả kiểm thử (Verification):
1. **Kiểm thử đơn vị endpoint `/chat/followups`:**
   - Trường hợp body rỗng: Trả về `{"success": true, "suggestions": []}` ✅
   - Trường hợp hợp lệ: Trả về `{"success": true, "suggestions": ["Câu 1", "Câu 2"]}` ✅
   - Trường hợp ngoại lệ / timeout: Bắt lỗi fail-safe trả về `{"success": false, "suggestions": []}` ✅
2. **Kiểm thử hồi quy toàn bộ hệ thống (`tests/test_refactor_regressions.py`):**
   - **28/28 tests PASSED (100% OK)** trong 0.025s ✅

---

## MỤC 4: Triển Khai Native Async Cho Follow-Up

Hệ thống đã nâng cấp toàn diện từ giải pháp ThreadPool sang **Native Async 100%**:

1. `services/followups.py`: `generate_followup_questions` là hàm `async def`; structured model được gọi trực tiếp bằng `await structured_model.ainvoke(...)`.
2. `app.py`: `chat_followups` là `async def` và gọi `await generate_followup_questions(...)`. Không còn `asyncio.to_thread` trong toàn bộ luồng.
3. `FollowupRequest` (Pydantic Model) tiếp tục tự động parse dữ liệu JSON từ body request.
4. **Kết quả:** Request chờ Groq qua Socket non-blocking ở tầng Kernel, **chiếm dụng 0 luồng phụ**, triệt tiêu 100% rủi ro nghẽn ThreadPool.

### Kết quả kiểm thử:
- `py -3.14 -m unittest discover -s tests -p test_followups_async.py -v`: **5/5 test đạt**.
- `py -3.14 -m unittest discover -s tests -p test_refactor_regressions.py -v`: **28/28 test hồi quy đạt**.
- Tổng cộng: **33/33 test đạt chuẩn (100% OK)**.

---

## MỤC 5: HƯỚNG DẪN KIẾN TRÚC NẾU TRIỂN KHAI FOLLOW-UP BẰNG NODE TRONG LANGGRAPH AGENT

Nếu muốn chuyển tính năng gợi ý câu hỏi (Follow-up) từ một **API độc lập bên ngoài** vào thành **một Node chính thức bên trong Đồ thị LangGraph (LangGraph Graph Node)**, dưới đây là bản thiết kế kỹ thuật chi tiết:

### 5.1. Sơ đồ luồng đồ thị (Graph Topology)

```
[START] ──► [chatbot_node] 
                 │
                 ├──(Có tool_calls)──► [tool_node] ──► [chatbot_node]
                 │                                        │
                 │                         (Tóm tắt xong) ├──► [emit_summary_node]
                 │                                        │            │
                 └──(Không còn tool / Final Answer)───────┴────────────┼──► [followup_node] ──► [END]
```

* **Lưu ý then chốt:** `followup_node` không thể chạy song song từ `START`, mà bắt buộc phải chạy **sau khi `chatbot` (hoặc `emit_summary`) đã hoàn tất câu trả lời cuối cùng**, vì nó cần nội dung câu trả lời hoàn chỉnh để tạo gợi ý.

---

### 5.2. Các bước sửa đổi mã nguồn chi tiết

#### Bước 1: Mở rộng `AgentState` trong `agent.py`
Thêm trường `followup_suggestions` vào state để lưu kết quả gợi ý:
```python
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    conversation_summary: str
    followup_suggestions: list[str]  # <-- Thêm trường này
```

#### Bước 2: Viết `followup_node` trong `agent.py`
Node này nhận state, bóc tách câu hỏi gần nhất của user và câu trả lời hoàn chỉnh của chatbot, rồi gọi hàm Native Async `generate_followup_questions`:
```python
async def followup_node(state: AgentState) -> dict[str, Any]:
    """Node sinh câu hỏi gợi ý tiếp theo sau khi bot đã trả lời xong."""
    if not FOLLOWUPS_ENABLED:
        return {"followup_suggestions": []}

    messages = state.get("messages", [])
    user_msg = ""
    assistant_ans = ""

    # Quét ngược để lấy User message và Assistant answer gần nhất
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not assistant_ans and not getattr(m, "tool_calls", None):
            assistant_ans = m.content if isinstance(m.content, str) else ""
        elif isinstance(m, HumanMessage) and not user_msg:
            user_msg = m.content if isinstance(m.content, str) else ""
        if user_msg and assistant_ans:
            break

    if not user_msg or not assistant_ans:
        return {"followup_suggestions": []}

    try:
        suggestions = await generate_followup_questions(
            user_message=user_msg,
            assistant_answer=assistant_ans,
            model_name=selected_model,
        )
        return {"followup_suggestions": suggestions}
    except Exception as exc:
        logger.warning(f"LangGraph followup_node failed: {exc}")
        return {"followup_suggestions": []}
```

#### Bước 3: Đăng ký Node và cập nhật rẽ nhánh trong `build_agent()`
Thay vì `tools_condition` rẽ thẳng về `END`, ta thay bằng hàm điều kiện chuyển sang `followup_node`:
```python
def route_after_chatbot(state: AgentState) -> str:
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "followup_node"  # Hết tool -> Đi vào sinh gợi ý trước khi kết thúc

# Trong build_agent():
workflow.add_node("chatbot", chatbot_node)
workflow.add_node("tools", tool_node)
workflow.add_node("emit_summary", emit_summary_node)
workflow.add_node("followup_node", followup_node)  # <-- Đăng ký node mới

workflow.add_edge(START, "chatbot")
workflow.add_conditional_edges(
    "chatbot",
    route_after_chatbot,
    {"tools": "tools", "followup_node": "followup_node"}
)
workflow.add_conditional_edges(
    "tools", route_after_tools, {"chatbot": "chatbot", "emit_summary": "emit_summary"}
)
workflow.add_edge("emit_summary", "followup_node")  # Tóm tắt xong cũng đi vào followup
workflow.add_edge("followup_node", END)              # Followup xong thì kết thúc lượt
```

#### Bước 4: Sửa luồng Stream SSE trong `app.py` (`_chat_event_generator`)
Vì không còn API `/chat/followups` riêng, các gợi ý sẽ được truyền thẳng qua luồng SSE của `/chat/stream`:
```python
# Trong vòng lặp async for event in agent.astream_events(...):
# Khi event là node followup_node kết thúc (on_chain_end):
if event.get("name") == "followup_node":
    output = event.get("data", {}).get("output", {})
    suggestions = output.get("followup_suggestions", [])
    if suggestions:
        yield f"data: {json.dumps({'type': 'followup', 'suggestions': suggestions})}\n\n"
```

#### Bước 5: Cập nhật Frontend (`templates/index.html`)
1. **Xóa bỏ:** Hàm `fetchParallelFollowups(...)` và lệnh gọi fetch tới `POST /chat/followups`.
2. **Cập nhật hàm đọc SSE:** Trong khối đọc stream SSE, bắt thêm sự kiện `data.type === 'followup'`:
   ```javascript
   if (parsed.type === "followup" && Array.isArray(parsed.suggestions)) {
       addFollowupSuggestions(parsed.suggestions, botElement);
   }
   ```

---

### 5.3. So Sánh Đánh Đổi Giữa 2 Cách Triển Khai

| Tiêu chí so sánh | Cách 1: API Riêng Biệt (Hiện tại) | Cách 2: Node Trong LangGraph (Theo thiết kế Mục 5) |
| :--- | :--- | :--- |
| **Độ trễ phản hồi (UX Latency)** | **0ms (Nhanh nhất):** Bot stream xong là ô chat mở khóa ngay (`Ready`). Gợi ý nảy lên sau đó ngầm. | **Bị delay thêm 0.5s - 1.5s:** Kết nối SSE phải giữ mở thêm để đợi node followup sinh xong thì ô chat mới `Ready`. |
| **Khả năng hủy (Abort)** | **Linh hoạt:** Người dùng gõ câu mới thì `AbortController` ở frontend hủy ngay lập tức request gợi ý. | **Khó:** Không thể hủy riêng lẻ nhánh gợi ý nếu nó đã nằm trong pipeline của LangGraph. |
| **Dung lượng State (Checkpointer)** | **Sạch sẽ:** Dữ liệu gợi ý không bị ghi vào MongoDB checkpoint của cuộc trò chuyện. | **Tăng dung lượng:** Mỗi turn chat đều lưu mảng `followup_suggestions` vào DB MongoDB checkpointer. |
| **Độ gọn gàng kiến trúc** | Tách rời 2 đầu việc (Chat stream + Followup fetch). | **Gói gọn 100%:** Toàn bộ logic gom vào 1 Graph duy nhất, không cần thêm endpoint API phụ. |

---

# MỤC 6: BẢN CHẤT HẠ TẦNG MẠNG & HỆ ĐIỀU HÀNH: PHÂN BIỆT TRIỆT ĐỂ SOCKET - API - ENDPOINT - THREAD

Khi làm việc với các hệ thống Web và AI hiện đại như FastAPI, LangChain, LangGraph và Uvicorn, các khái niệm tầng mạng thường bị hiểu nhầm hoặc đồng nhất với nhau. Mục này làm sáng tỏ hoàn toàn bản chất kỹ thuật từ tầng ứng dụng đến tầng nhân hệ điều hành (OS Kernel).

---

## 6.1. Bức Tranh Toàn Cảnh: Phân Biệt Server, Endpoint, API, và Socket

Hãy hình dung qua ẩn dụ đời thực: **"Tòa nhà Ngân hàng"** và **"Gọi điện đặt Pizza"**:

```
[Máy Khách / Trình duyệt] 
       │
       │  (1) SOCKET: Đường dây điện thoại / Đường ống mạng kết nối tới Cổng 8000
       ▼
[MÁY CHỦ / SERVER: http://localhost:8000] (Tòa nhà Ngân hàng)
       │
       ├── (2) ENDPOINT: Quầy số 1 (POST /chat/stream)
       ├── (2) ENDPOINT: Quầy số 2 (POST /chat/followups)  ◄── [Điểm đích cụ thể]
       └── (2) ENDPOINT: Quầy số 3 (GET /health)
       │
       ▼
   (3) API: Bản hợp đồng / Quy tắc giao tiếp tại quầy 
       (Ví dụ: "Gửi JSON {user_message, assistant_answer} -> Trả về JSON {suggestions: [...]}")
```

### Bảng phân định 4 tầng khái niệm:

| Khái niệm | Định nghĩa kỹ thuật | Ẩn dụ thực tế | Vai trò trong dự án `Tit-Chatbot` |
| :--- | :--- | :--- | :--- |
| **Server** | Toàn bộ ứng dụng web đang chạy và lắng nghe trên một địa chỉ IP và Port. | **Cả Tòa nhà Ngân hàng** | Uvicorn chạy tại `http://localhost:8000`. |
| **Endpoint** | **Điểm đích chức năng cụ thể** trên Server, cấu thành từ `HTTP Method + URL Path`. | **Quầy giao dịch số 2** (chuyên rút tiền) | `@app.post("/chat/followups")` hoặc `@app.post("/chat/stream")` trong `app.py`. |
| **API** | **Bản quy ước / Giao kèo (Contract)** về dữ liệu và luật lệ tương tác. | **Quy tắc làm việc tại quầy** (Phải điền biểu mẫu gì, nhận biên lai gì) | Quy định body phải có `{user_message: "...", assistant_answer: "..."}` và trả về danh sách câu hỏi. |
| **Socket** | **Đường ống kết nối mạng 2 chiều (Bi-directional Pipe)** do Hệ điều hành tạo ra (IP + Port) để truyền các byte `0101...`. | **Đường dây điện thoại & Bộ đàm** nối người dùng với nhân viên | OS Socket nối từ browser tới Uvicorn, hoặc từ app tới `api.groq.com:443`, hoặc tới MongoDB `localhost:27017`. |

> **Nguyên tắc cốt lõi:**
> - **Socket** là **phương tiện vận chuyển / đường ống**.
> - **API** là **nội dung và luật lệ giao tiếp** chạy xuyên qua đường ống đó.
> - **Không có Socket** thì API không có cách nào gửi đi.
> - **Có Socket mà không có API** thì hai máy tính chỉ kết nối nhìn nhau mà không hiểu đối phương muốn làm gì.

---

## 6.2. Code Socket Riêng Nằm Ở Đâu? Vì Sao Không Thấy Trong Dự Án?

Trong mã nguồn dự án `Tit-Chatbot`, bạn hoàn toàn **không thấy file nào tên là `socket.py` hay lệnh `import socket`**. Lý do là vì toàn bộ tầng giao tiếp socket cấp thấp đã được **bọc (abstracted) qua nhiều tầng thư viện**:

```
[Code của bạn]       -> await structured_model.ainvoke(...)  (trong services/followups.py)
       ↓
[LangChain / Groq]   -> httpx.AsyncClient.post(...)
       ↓
[Python Standard]    -> asyncio.open_connection() / Lib/socket.py (đi kèm bộ cài Python)
       ↓
[OS Kernel]          -> Windows Winsock / Linux Kernel tạo Socket File Descriptor
```

### So sánh: Tự viết Socket thủ công (Raw Socket) vs Code Hiện Tại:

```python
# -------------------------------------------------------------
# CÁCH CỔ ĐIỂN: Lập trình viên phải tự mở và quản lý Socket bằng tay:
# -------------------------------------------------------------
import socket
import ssl

raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
raw_sock.connect(("api.groq.com", 443))
ssl_sock = ssl.wrap_socket(raw_sock)
ssl_sock.sendall(b"POST /openai/v1/chat/completions HTTP/1.1\r\nHost: api.groq.com\r\n...")
data = ssl_sock.recv(4096)
ssl_sock.close()

# -------------------------------------------------------------
# CÁCH HIỆN TẠI TRONG DỰ ÁN (Thư viện tự làm toàn bộ việc trên ngầm):
# -------------------------------------------------------------
response = await structured_model.ainvoke(...)
```

---

## 6.3. Mô Hình: 1 Main Thread Quản Lý Hàng Ngàn Socket (Bí Mật Của Concurrency)

Rất nhiều lập trình viên thắc mắc: *"Dự án chỉ có 1 Main Thread duy nhất, vậy mở nhiều socket thì thread có bị chia nhỏ không?"*

### Câu trả lời: KHÔNG! 1 Main Thread có thể gánh hàng chục ngàn Socket cùng lúc!

- **Thread** là đơn vị thực thi mã nguồn của CPU (chiếm dụng bộ nhớ stack, context-switching).
- **Socket** chỉ là một **con số định danh (File Descriptor / Handle)** trong nhân Hệ điều hành, chiếm vài KB bộ nhớ đệm (buffer) của card mạng.

### Cơ chế bảng tín hiệu (I/O Multiplexing):
> Hãy tưởng tượng **1 Main Thread** là **1 người trực tổng đài điện thoại duy nhất**:
> - Thay vì tuyển 1.000 nhân viên (1.000 Threads) ngồi ôm 1.000 cái điện thoại để chờ chuông reo (cực kỳ tốn kém và nặng nề).
> - Người trực tổng đài có một **bảng đèn LED gồm 10.000 bóng đèn (tương ứng 10.000 sockets)**:
>   - Nhờ hệ điều hành (`IOCP` trên Windows, `epoll` trên Linux), người này chỉ ngồi thảnh thơi.
>   - Đèn số #42 chớp sáng (có gói tin mạng từ Groq gửi về card mạng) $\rightarrow$ Người trực tổng đài nhấc máy đường dây #42 lên xử lý ngay trong vài micro-giây.
>   - Đặt máy xuống $\rightarrow$ Quay sang xử lý ngay đường dây #99 vừa chớp sáng tiếp theo.

### So sánh tài nguyên giữa Thread và Socket:

| Tiêu chí | Dùng nhiều Luồng (Threads) | Dùng nhiều Sockets (Native Async) |
| :--- | :--- | :--- |
| **Tài nguyên tiêu tốn** | Tốn **~1MB - 8MB RAM** cho mỗi Thread | Tốn **vài KB bộ nhớ đệm** cho mỗi Socket Descriptor |
| **Chi phí chuyển ngữ cảnh (Context Switching)** | CPU liên tục phải lưu/khôi phục thanh ghi giữa các thread $\rightarrow$ Nóng máy, giảm hiệu suất | **0 chi phí context-switching**; CPU chỉ chạy 1 luồng duy nhất xử lý dữ liệu |
| **Ngưỡng quá tải** | 40 - 200 threads là chạm trần (Dẫn tới ThreadPool Starvation) | **Hàng chục ngàn socket đồng thời (C10K - C100K)** vẫn nhẹ tênh |

---

## 6.4. Cơ Chế Socket Trong Luồng Native Async Của Dự Án

Khi endpoint `/chat/followups` gọi `await structured_model.ainvoke(...)`:

1. **Có tạo Socket mới không?**
   - **Lần đầu tiên:** Thư viện HTTP ngầm (`httpx.AsyncClient`) mở một kết nối Socket mới tới `api.groq.com:443` (thực hiện bắt tay TCP và SSL Handshake).
2. **Cơ chế Connection Pool (Tái sử dụng socket - HTTP Keep-Alive):**
   - Sau khi nhận xong câu trả lời gợi ý, socket này **KHÔNG BỊ ĐÓNG**, mà được giữ lại trong **Hồ bơi kết nối (Connection Pool)**.
   - Các request follow-up tiếp theo của người dùng sẽ **tái sử dụng ngay socket ấm có sẵn**, loại bỏ hoàn toàn độ trễ bắt tay mạng (bỏ qua được 50ms - 200ms latency khởi tạo).
3. **Khi có 100 người dùng đồng thời (High Concurrency):**
   - Pool tự động mở rộng, tạo đồng thời hàng chục socket kết nối song song tới Groq.
   - Cả 100 socket cùng lúc chờ gói tin mạng từ server Groq ở chế độ non-blocking.
   - Toàn bộ quá trình chờ đợi này **không tốn một luồng phụ nào**, Main Thread hoàn toàn rảnh tay để stream tin nhắn `/chat/stream` mượt mà cho các học sinh / người dùng khác.


