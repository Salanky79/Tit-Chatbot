# TỔNG HỢP KIẾN THỨC VÀ KIẾN TRÚC HỆ THỐNG TIT-CHATBOT

Tài liệu này tóm tắt ngắn gọn, dễ hiểu và hệ thống hóa toàn bộ các vấn đề kỹ thuật cốt lõi đã thảo luận và triển khai trong dự án.

---

## 1. Chuyển đổi `user_id` sang `ObjectId` trong MongoDB

### Sửa ở những đâu?
Toàn bộ việc chuyển đổi được đóng gói tập trung **duy nhất tại Data Access Layer (`database.py`)**:
* **Thêm helper `_to_object_id(val)`**: Chuyển đổi an toàn chuỗi hex 24 ký tự sang `bson.ObjectId`.
* **Cập nhật `_conv_to_obj(doc)`**: Khi trả dữ liệu ra ngoài, tự động biến mọi `ObjectId` thành `str` (chuỗi) để tránh lỗi crash JSON `TypeError: ObjectId is not JSON serializable`.
* **Cập nhật các hàm ghi & truy vấn MongoDB**:
  * `sessions`: Lưu `user_id` dạng `ObjectId`.
  * `conversations`: Lưu `user_id` dạng `ObjectId`; lọc theo `{"user_id": oid, "is_guest": {"$ne": True}}`.
  * `chat_messages`: Lưu `user_id` dạng `ObjectId`.
  * `long_term_memory`: Lưu và tìm kiếm bằng `{"user_id": oid}`.

### Tại sao các file khác (`app.py`, `tools.py`, `rag.py`) KHÔNG cần sửa?
* **`app.py` & Frontend**: Giao thức HTTP và chuẩn JSON chỉ hỗ trợ kiểu chuỗi (`str`), không có kiểu `ObjectId`. Các tầng này chỉ việc truyền chuỗi, `database.py` sẽ tự động ép kiểu bên trong.
* **`rag.py` (ChromaDB)**: **Bắt buộc phải giữ `str`** vì ChromaDB chỉ chấp nhận metadata kiểu nguyên thủy (`str`, `int`, `float`, `bool`). Truyền `ObjectId` vào ChromaDB sẽ gây lỗi `ValueError: Unsupported metadata type`.
* **`tools.py`**: Chỉ là cầu nối trung gian truyền chuỗi xuống `database.py`.

> **Quy tắc vàng:** *"Đi vào MongoDB thì dùng `ObjectId`, bước chân ra khỏi `database.py` thì dùng `str`."*

---

## 2. Quản lý Conversation & Long-term Memory theo User ID

### Phân chia luồng Guest vs User đã đăng nhập
* **Chưa đăng nhập (Guest)**:
  * Không có danh sách lịch sử (API `/conversations` trả về `[]`).
  * Chỉ có 1 cuộc trò chuyện tạm thời. Khi bấm "New Chat", cuộc trò chuyện cũ bị hủy.
  * Trong MongoDB, cuộc chat của guest được đánh dấu `is_guest: True` và đặt TTL `expires_at = now + 24h` để MongoDB tự động dọn dẹp, không tích rác.
* **Đã đăng nhập**:
  * Lưu trữ và quản lý toàn bộ các cuộc trò chuyện vĩnh viễn theo `user_id` (ObjectId).

### Long-term Memory: Bỏ hoàn toàn fallback `thread_id`
* **Cơ chế cũ**: Nếu thiếu `user_id`, hệ thống cố tra ngược lại qua `get_conversation(thread_id)` rồi lọc theo `thread_id` $\rightarrow$ Thừa thãi và có nguy cơ lẫn lộn dữ liệu giữa các guest.
* **Cơ chế mới**:
  * Bộ nhớ dài hạn gắn chặt $100\%$ vào tài khoản người dùng (`user_id`).
  * `save_memory` và `search_memory` **chỉ lọc theo `user_id`**, không fallback `thread_id`.
  * Chưa đăng nhập thì không dùng được bộ nhớ dài hạn.

---

## 3. Mật mã & Định danh: Password, Token, UUID

| Khái niệm | Dùng ở đâu? | Cách thức hoạt động | Tại sao dùng? |
|---|---|---|---|
| **`bcrypt`** | Băm mật khẩu người dùng (`database.py`) | **Hàm băm 1 chiều (One-way Hash)**. Tự động rắc Muối ngẫu nhiên (**Salt**) và chạy qua $2^{12} = 4.096$ vòng tính toán (**Rounds**). Không thể dịch ngược về mật khẩu gốc. | **Cố tình chạy chậm (~0.1s)** để triệt tiêu khả năng hacker dùng siêu máy tính dò mật khẩu (brute-force). |
| **`secrets.token_urlsafe(32)`** | Tạo Session Token (`database.py`) | Sinh **32 bytes (256 bits) ngẫu nhiên tuyệt đối** từ entropy phần cứng hệ điều hành (CSPRNG), mã hóa Base64 URL-safe (dài 43 ký tự: `a-z`, `A-Z`, `0-9`, `-`, `_`). | **Siêu nhanh ($<0.001$ms)** để tra cứu tức thì trên mỗi request. Bảo mật $1/2^{256}$ không thể đoán mò. |
| **`uuid.uuid4()`** | Đặt tên file upload (`app.py`) | Sinh chuỗi ngẫu nhiên 128-bit dạng chuẩn `8-4-4-4-12` (ví dụ: `c9a646d3-9c61-4cd9-...`). Xác suất trùng lặp gần như bằng $0$. | Chống việc file của User B **ghi đè và xóa mất** file của User A khi trùng tên file gốc (như `image.png`, `baocao.pdf`). |

---

## 4. Pipeline xử lý và đọc ảnh (Vision Pipeline)

### Lưu ở đâu và dạng gì?
1. **File ảnh vật lý**: Lưu nguyên bản nhị phân (`.png`, `.jpg`, `.webp`) trong thư mục ổ cứng `uploads/` với tiền tố UUID (ví dụ `uploads/a1b2_anh.jpg`).
2. **Trong Vector DB (ChromaDB)**: **Không lưu ảnh** mà chỉ lưu một tấm thẻ ghi chú (metadata) chứa `image_path` trỏ tới file ảnh trên ổ cứng.

### Trình tự đọc ảnh
```text
User hỏi về ảnh 
  ──► LLM gọi tool `analyze_uploaded_image` (tools.py)
  ──► ChromaDB tìm ra đường dẫn `image_path` trên ổ cứng
  ──► `analyze_image` (image_analysis.py) đọc file byte nhị phân
  ──► Mã hóa thành chuỗi Base64 Data URL
  ──► Gửi lên Groq Vision Model (qwen/qwen3.8-27b)
  ──► Model phân tích và trả về câu trả lời
```

### Dòng mã Base64:
```python
data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
```
* **Bản chất**: Biến dữ liệu ảnh nhị phân thành chuỗi văn bản ASCII an toàn để "nhét" vừa vào gói tin JSON gửi qua mạng.
* **Mô hình AI có đọc Base64 như văn bản không?** 
  * **Không**. Đến máy chủ AI, chuỗi Base64 được giải mã ngược lại thành **ma trận điểm ảnh (Pixels)**. 
  * Bộ mã hóa thị giác (**Vision Transformer - ViT**) cắt ảnh thành các ô vuông nhỏ (patches) và biến thành các **Visual Tokens** giúp AI thực sự **"nhìn"** thấy ảnh như mắt người.

---

## 5. Similarity Search trong RAG: Cơ chế Cosine Similarity

### Tại sao dùng Cosine Similarity thay vì đo khoảng cách Euclid?
* **Khoảng cách Euclid (Độ dài đường thẳng)**: Bị ảnh hưởng bởi độ dài đoạn văn bản. Một câu ngắn và một đoạn văn dài có cùng ý nghĩa nhưng vector của đoạn dài sẽ có độ lớn lớn hơn, làm khoảng cách Euclid bị lệch xa.
* **Cosine Similarity (Đo góc giữa 2 vector)**:
  $$\text{Cosine}(\theta) = \frac{\vec{A} \cdot \vec{B}}{\|\vec{A}\| \|\vec{B}\|}$$
  * Chỉ quan tâm đến **hướng và góc $\theta$** của 2 vector trong không gian.
  * Nếu 2 văn bản có cùng ý nghĩa, góc giữa chúng sẽ cực nhỏ $\rightarrow \cos(\theta) \approx 1$.
  * Hoàn toàn không bị phụ thuộc vào việc đoạn văn dài hay ngắn.

---

## 6. Phân tích chuyên sâu: Session, Cookie, Token, Authentication & Authorization

Kiến trúc an ninh và quản lý phiên của Tit-Chatbot được thiết kế theo mô hình **Stateful Server-Side Session** kết hợp với **Secure HTTP-Only Cookies**. Dưới đây là phân tích chi tiết từng tầng công nghệ kèm trích dẫn mã nguồn thực tế.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 LUỒNG XÁC THỰC & ỦY QUYỀN                              │
└────────────────────────────────────────────────────────────────────────────────────────┘

 [TRÌNH DUYỆT (BROWSER)]                                         [SERVER FASTAPI]                          [MONGODB ATLAS]
          │                                                             │                                         │
          │─── 1. POST /auth/signin {identifier, password} ────────────►│                                         │
          │                                                             │─── Tra user & verify_password(bcrypt) ─►│
          │                                                             │◄── User Document hợp lệ ────────────────│
          │                                                             │                                         │
          │                                                             │─── secrets.token_urlsafe(32) ───────────┐
          │                                                             │    (Sinh 256-bit entropy token)         │
          │                                                             │─── create_session(user_id) ────────────►│
          │                                                             │    Lưu session_id, user_id, expires_at │
          │                                                             │◄── Session Inserted OK ─────────────────│
          │                                                             │                                         │
          │◄── 2. Response: Set-Cookie: tit_session=<token>; ───────────┤                                         │
          │       HttpOnly; SameSite=Lax; Max-Age=604800; Path=/        │                                         │
          │                                                             │                                         │
  ┌───────┴────────────────────────┐                                    │                                         │
  │ Ghi Cookie an toàn xuống ổ cứng│                                    │                                         │
  │ (Persistent Cookie - 7 ngày)   │                                    │                                         │
  └───────┬────────────────────────┘                                    │                                         │
          │                                                             │                                         │
          │─── 3. GET /history/{thread_id} ────────────────────────────►│                                         │
          │       (Trình duyệt TỰ ĐỘNG đính kèm Cookie: tit_session)    │─── get_current_user(request) ───────────┐
          │                                                             │    Đọc cookie 'tit_session'             │
          │                                                             │─── get_session(session_id) ────────────►│
          │                                                             │◄── Trả về {user_id: ObjectId("...")} ───│
          │                                                             │                                         │
          │                                                             │─── [AUTHORIZATION CHECK] ───────────────┐
          │                                                             │    Tra cứu conversation.user_id         │
          │                                                             │    So sánh: conv.user_id == user.id?    │
          │                                                             │    - KHÁC: Trả về HTTP 403 Forbidden ───┘
          │                                                             │    - TRÙNG: Trả về HTTP 200 OK          │
          │◄── 4. Response: Dữ liệu tin nhắn (hoặc 403 Forbidden) ──────┤                                         │
```

---

### A. Bản chất của Session: Stateful vs Stateless (Tại sao chọn Session thay vì JWT?)

Giao thức HTTP bản chất là **Stateless (Phi trạng thái)**: Mỗi request gửi lên máy chủ là độc lập hoàn toàn, máy chủ không thể tự biết request trước đó do ai gửi. Có 2 trường phái giải quyết:

1. **Stateless (JWT - JSON Web Token)**: 
   * Server mã hóa toàn bộ thông tin user vào token và ký số (signature). Client giữ token và gửi lên ở mỗi request. Server chỉ cần verify chữ ký mà không cần tra Database.
   * **Nhược điểm chí mạng**: **Không thể thu hồi phiên tức thì (Cannot Revoke Immediately)**. Khi người dùng bấm Đăng xuất, hoặc tài khoản bị xâm nhập cần khóa khẩn cấp, token JWT vẫn có hiệu lực cho đến khi hết hạn (trừ khi xây dựng blacklist trên Redis — điều này biến JWT thành stateful).
2. **Stateful Session (Tit-Chatbot sử dụng)**:
   * Token chỉ là một chuỗi ngẫu nhiên (Opaque String) không chứa bất kỳ dữ liệu nhạy cảm nào.
   * Toàn bộ trạng thái phiên (ai đang đăng nhập, hết hạn lúc nào) được lưu tập trung trong MongoDB `sessions`.
   * **Ưu điểm vượt trội**:
     * **Thu hồi phiên tức thì (Instant Kill-Switch)**: Khi gọi `/auth/signout`, server xóa record trong MongoDB. Token đó lập tức biến thành "chìa khóa vô dụng", dù hacker có nhặt được token cũng không thể sử dụng.
     * **Kiểm soát đa thiết bị**: Dễ dàng đếm và giới hạn số lượng thiết bị đăng nhập đồng thời của 1 tài khoản.
     * **Dọn dẹp tự động**: Tận dụng cơ chế TTL Index của MongoDB để tự xóa session hết hạn mà không tốn tài nguyên server.

---

### B. Kiến trúc Token: Hàm mật mã `secrets.token_urlsafe(32)`

Trong [`database.py`](file:///d:/Tit-Chatbot/database.py#L309-L323):
```python
def create_session(user_id: str | ObjectId, duration_days: int = 7) -> str:
    """Tạo phiên đăng nhập an toàn mới lưu vào MongoDB."""
    session_id = secrets.token_urlsafe(32)
    now = _now()
    expires_at = now + timedelta(days=duration_days) # thời hạn mặc định 7 ngày

    oid = _to_object_id(user_id)
    sessions = get_collection("sessions")
    sessions.insert_one({
        "session_id": session_id,
        "user_id": oid or user_id,
        "created_at": now,
        "expires_at": expires_at,
    })
    return session_id
```

#### Phân tích mật mã học:
* **Nguồn Entropy (CSPRNG)**: Thư viện `secrets` của Python gọi trực tiếp bộ tạo số ngẫu nhiên an toàn mật mã của hệ điều hành (`/dev/urandom` trên Linux hoặc `CryptGenRandom` trên Windows). Nó lấy độ nhiễu vật lý (nhiệt độ CPU, độ trễ ngắt phần cứng, chuyển động chuột) để đảm bảo tính ngẫu nhiên tuyệt đối, khác biệt hoàn toàn với `random` thông thường.
* **Độ phức tạp 256 bits**: `secrets.token_urlsafe(32)` sinh ra đúng **32 bytes = 256 bits** ngẫu nhiên.
  $$\text{Số khả năng có thể xảy ra} = 2^{256} \approx 1.1579 \times 10^{77}$$
  Con số này lớn hơn số nguyên tử trong toàn bộ dải Ngân Hà. Kẻ tấn công dùng toàn bộ siêu máy tính mạnh nhất Trái Đất cũng mất hàng tỷ năm để đoán mò được 1 token (Triệt tiêu tấn công Brute-Force).
* **Mã hóa URL-safe Base64**: Biến 32 bytes nhị phân thành chuỗi 43 ký tự an toàn (`a-z`, `A-Z`, `0-9`, `-`, `_`). Không chứa các ký tự đặc biệt như `+`, `/`, `=` vốn có thể bị lỗi khi truyền qua HTTP Cookie hoặc URL parameter.

---

### C. Cơ Chế Truyền Nhận: Response Là Gì? Tại Sao Phải Gửi Kèm Response? Cookie Có Phải Là Tham Số?

Để hiểu tại sao code lại viết `response.set_cookie(...)`, ta cần nhìn vào bản chất của **giao thức mạng HTTP (HyperText Transfer Protocol)**:

#### 1. "Response" là gì?
Trong thế giới web, mọi cuộc trò chuyện giữa Trình duyệt (Client) và Máy chủ (Server) đều tuân theo mô hình **Hỏi - Đáp (Request - Response)**:
* **Request (Yêu cầu)**: Client gửi lên: *"Tôi muốn đăng nhập với username 'salanky' và password '123'!"*
* **Response (Phản hồi)**: Server xử lý xong và gửi câu trả lời về cho Client: *"Đăng nhập thành công nhé, đây là dữ liệu của bạn!"*

Trong FastAPI, đối tượng `response` (`JSONResponse`) chính là chiếc **"phong bì thư"** mà Server đóng gói để gửi ngược lại cho Trình duyệt qua đường truyền mạng. Chiếc phong bì này gồm 2 phần:
1. **Header (Phần tiêu đề)**: Chứa các chỉ thị điều khiển của giao thức (`Content-Type`, `Set-Cookie`, `Cache-Control`...).
2. **Body (Phần thân)**: Chứa dữ liệu thực tế (chuỗi JSON `{"success": true, ...}`).

#### 2. "Sao lại truyền qua response thế? Session truyền kèm response à?"
* **Đúng vậy, bắt buộc phải truyền kèm Response!**
* **Ví dụ thực tế**:
  * Bạn đến quầy lễ tân khách sạn để nhận phòng (gửi **Request**).
  * Lễ tân kiểm tra thông tin hợp lệ trong sổ sách khách sạn (Database MongoDB).
  * Lễ tân đưa cho bạn **chìa khóa phòng / thẻ từ** kèm câu nói *"Chúc quý khách nghỉ vui vẻ"* (đây chính là **Response**).
  * **Nếu lễ tân không đưa thẻ từ kèm theo phản hồi đó**, thì bạn lấy chìa khóa ở đâu để quẹt thang máy và mở cửa phòng ở những lần đi lại tiếp theo?!
* Do đó: Khi bạn đăng nhập thành công, Server tạo ra `session_id` trong MongoDB, và **bắt buộc phải nhét chiếc chìa khóa này vào chiếc phong bì `response`** gửi về cho trình duyệt. Trình duyệt cầm chiếc chìa khóa đó cất đi để dùng cho mọi thao tác sau này.

#### 3. "Cookie có phải là tham số không?"
* **Cookie KHÔNG PHẢI là tham số (parameter) của hàm hay tham số URL!**
* **Cookie là một cơ chế lưu trữ đặc biệt được điều khiển bởi HTTP Header**:
  * Khi Server muốn giao "chìa khóa" cho Trình duyệt, Server ghi thêm một dòng chỉ thị đặc biệt vào phần Header của gói tin Response:
    ```http
    HTTP/1.1 200 OK
    Content-Type: application/json
    Set-Cookie: tit_session=c9a646d3...; HttpOnly; SameSite=Lax; Max-Age=604800; Path=/

    {"success": true, "message": "Đăng nhập thành công!"}
    ```
  * Trong code Python FastAPI, lệnh `response.set_cookie(...)` thực chất là một **hàm tiện ích** giúp Server dán dòng chữ `Set-Cookie: tit_session=...` này lên phong bì Response trước khi gửi đi.
  * Khi Trình duyệt nhận được gói tin Response có dòng `Set-Cookie`, trình duyệt tự động hiểu:  
    > *"À, Server gửi cho mình một thẻ khóa có tên là `tit_session`. Mình phải cất ngay vào két sắt Cookie trên ổ cứng. Từ giờ trở đi, hễ gửi bất kỳ Request nào lên domain này, mình sẽ TỰ ĐỘNG dán kèm thẻ khóa này vào Header để Server nhận diện mình!"*

#### 4. Luồng đối ứng 2 chiều của Cookie trong toàn bộ hệ thống:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          LUỒNG 2 CHIỀU CỦA COOKIE QUA HTTP                             │
└────────────────────────────────────────────────────────────────────────────────────────┘

[CHIỀU 1: SERVER GỬI CHO CLIENT (Lúc Đăng Nhập)]
Server tạo token ──► Gắn vào Response: response.set_cookie(...)
                  ──► Gói tin mạng có Header: Set-Cookie: tit_session=<token>
                  ──► Trình duyệt tự bắt lấy và cất vào két sắt Cookie của máy tính.

[CHIỀU 2: CLIENT GỬI LÊN SERVER (Mọi Thao Tác Sau Đó: Chat, Tải file, Xem lịch sử)]
Người dùng click  ──► Trình duyệt TỰ ĐỘNG móc Cookie từ két sắt ra
                  ──► Kẹp vào Header Request: Cookie: tit_session=<token>
                  ──► Server nhận và đọc qua: session_id = request.cookies.get("tit_session")
```

---

### D. Cookie An Toàn: Giải mã 4 Cờ An Ninh (Security Flags)

Trong [`app.py`](file:///d:/Tit-Chatbot/app.py#L132-L140) và [`app.py`](file:///d:/Tit-Chatbot/app.py#L178-L186):
```python
response.set_cookie(
    key="tit_session",
    value=session_id,
    httponly=True,          # Chống XSS (Cross-Site Scripting)
    samesite="lax",         # Chống CSRF (Cross-Site Request Forgery)
    max_age=7 * 24 * 3600,  # Persistent Cookie 7 ngày
    path="/",               # Hiệu lực toàn domain
)
```

#### Ý nghĩa thực chiến của từng Flag:

| Cờ (Flag) | Giá trị | Rủi ro nếu KHÔNG có | Cơ chế bảo vệ thực tế |
| :--- | :--- | :--- | :--- |
| **`HttpOnly`** | `True` | **Mất tài khoản qua XSS**: Nếu trang web dính lỗi XSS, mã độc JavaScript của hacker (`document.cookie`) sẽ đọc được token và gửi về máy chủ của hacker. | Trình duyệt cấm tuyệt đối mọi script JavaScript đọc/ghi cookie này. Chỉ duy nhất bộ phân tích HTTP của trình duyệt được phép gửi cookie lên máy chủ. |
| **`SameSite`** | `"lax"` | **Tấn công CSRF**: Hacker lừa người dùng click vào link `evil.com`, trang web độc hại bí mật gửi request ngầm đến `tit-chatbot.com` với cookie của người dùng. | `"lax"` quy định: Trình duyệt chỉ gửi kèm cookie khi người dùng trực tiếp điều hướng trang (Top-level Navigation như click thẻ `<a>`), chặn mọi request ngầm từ iframe, AJAX hoặc form post của bên thứ ba. |
| **`Max-Age`** | `604.800s` (7 ngày) | **Mất phiên khi tắt tab**: Cookie chỉ lưu trong RAM (Session Cookie), người dùng vừa tắt trình duyệt mở lại là bị văng đăng nhập. | Trình duyệt ghi cookie xuống **ổ đĩa cứng (Persistent Storage)**. Khi mở lại tab sau vài ngày, cookie vẫn còn nguyên, giúp tự động đăng nhập. |
| **`Path`** | `"/"` | Cookie chỉ gửi ở một vài endpoint nhất định, gây lỗi thiếu quyền ở các API khác. | Đảm bảo cookie được tự động đính kèm ở mọi route: `/conversations`, `/chat/stream`, `/history`, `/upload`. |

#### Cơ chế Đăng Xuất An Toàn (`/auth/signout`):
Trong [`app.py`](file:///d:/Tit-Chatbot/app.py#L189-L206):
```python
@app.post("/auth/signout")
async def auth_signout(request: Request):
    """Đăng xuất và hủy bỏ phiên làm việc."""
    session_id = request.cookies.get("tit_session")
    ...
    if session_id:
        delete_session(session_id)  # 1. Hủy phiên trên Server (MongoDB)

    response = JSONResponse({"success": True, "message": "Đã đăng xuất thành công."})
    response.delete_cookie(key="tit_session", path="/")  # 2. Xóa sạch Cookie trên Client
    return response
```
Quy trình xóa sạch ở **cả 2 đầu (Client & Server)**: Server xóa bản ghi session trong MongoDB, đồng thời gửi chỉ thị `delete_cookie` để trình duyệt xóa tệp cookie trên ổ cứng.

---

### E. Cơ chế Tự Dọn Dẹp Phiên Hết Hạn: MongoDB TTL Index

Trong [`mongo_client.py`](file:///d:/Tit-Chatbot/mongo_client.py#L108-L112):
```python
# sessions (với TTL index tự động dọn dẹp phiên hết hạn)
sessions = get_collection("sessions")
sessions.create_index([("session_id", ASCENDING)], unique=True, background=True)
sessions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, background=True)
```

* **Cơ chế TTL (Time-To-Live)**: MongoDB có một tiến trình ngầm (Background Thread) chạy định kỳ mỗi 60 giây. Nó kiểm tra trường `expires_at`: Khi `expires_at <= current_time`, MongoDB **tự động xóa bỏ document session đó**.
* **Lợi ích**: Bảng `sessions` không bao giờ bị phình to (leak memory), không cần viết script cron-job chạy ngầm để dọn rác.
* **Lớp phòng thủ kép (Defense in Depth)** tại [`database.py`](file:///d:/Tit-Chatbot/database.py#L335-L343):
  ```python
  # Kiểm tra hết hạn chủ động trong trường hợp TTL worker chưa kịp quét
  now = _now()
  expires_at = session.get("expires_at")
  if expires_at and expires_at < now:
      sessions.delete_one({"session_id": session_id})
      return None
  ```
  Nếu người dùng gửi request vào đúng mili-giây mà session vừa hết hạn nhưng TTL worker chưa kịp chạy, hàm `get_session` sẽ chủ động xóa ngay và từ chối truy cập.

---

### F. Authentication (Xác thực): Hàm `get_current_user` với 3 Lớp Dự Phòng

Trong [`app.py`](file:///d:/Tit-Chatbot/app.py#L87-L104):
```python
def get_current_user(request: Request) -> dict | None:
    """Trích xuất thông tin người dùng từ Cookie, Header Authorization hoặc Query Param."""
    # 1. Ưu tiên số 1: Đọc từ Cookie HTTP-Only của Web Browser
    session_id = request.cookies.get("tit_session")
    
    # 2. Dự phòng 2: Đọc từ Header Authorization (dành cho Mobile App, Postman, API Client)
    if not session_id:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_id = auth_header[7:].strip()
            
    # 3. Dự phòng 3: Đọc từ Query Param (dành cho EventSource SSE / WebSocket nếu client không thể set header)
    if not session_id:
        session_id = request.query_params.get("session_id")
        
    if not session_id:
        return None

    session = get_session(session_id)
    if not session:
        return None

    return get_user_by_id(session.get("user_id", ""))
```

#### Thiết kế linh hoạt:
1. **Web Browser thông thường**: Lấy tự động qua `request.cookies.get("tit_session")` (An toàn nhất, chống XSS).
2. **REST API / Third-party Client**: Cho phép truyền `Authorization: Bearer <session_token>`.
3. **SSE Stream / WebSockets**: Trong một số môi trường frontend cũ nơi `EventSource` không hỗ trợ đính kèm header, hệ thống cho phép fallback qua `?session_id=...`.

#### Chi tiết Quy trình 2 Bước Đối Soát Session với Database:
Để biết một `session_id` có đúng người dùng không, Server thực hiện 2 bước truy vấn MongoDB:

1. **Bước 1: Tra cứu phiên trong bảng `sessions`** qua hàm [`get_session`](file:///d:/Tit-Chatbot/database.py#L326-L350):
   ```python
   session = sessions.find_one({"session_id": session_id})
   ```
   - Server tìm document khớp với `session_id`.
   - Kiểm tra hạn dùng: nếu `expires_at < now`, xóa bản ghi và trả về `None` (phiên hết hạn).
   - Nếu hợp lệ, lấy ra trường `user_id` (trỏ đến `_id` của bảng `users`).

2. **Bước 2: Tra cứu thông tin người dùng trong bảng `users`** qua hàm [`get_user_by_id`](file:///d:/Tit-Chatbot/database.py#L195-L218):
   ```python
   user = users.find_one({"_id": ObjectId(user_id)})
   ```
   - Chuyển `user_id` thành `ObjectId` rồi truy vấn bảng `users`.
   - Trả về hồ sơ người dùng (`username`, `email`, `full_name`, `avatar_url`).
   - Nếu không tìm thấy hoặc người dùng đã bị xóa, trả về `None`.

```text
[HTTP Request] ──(Cookie: tit_session)──► get_current_user()
                                                 │
                     ┌───────────────────────────┴───────────────────────────┐
                     ▼                                                       ▼
      [database.py: get_session]                            [database.py: get_user_by_id]
      Collection: "sessions"                                 Collection: "users"
      ┌──────────────────────────────────┐                   ┌──────────────────────────────────┐
      │ session_id: "a1b2c3..."          │                   │ _id: ObjectId("663fa1...")       │
      │ user_id: ObjectId("663fa1...")──┼──────────────────►│ username: "hoangnam"             │
      │ expires_at: 2026-05-17...        │ (Khóa ngoại)      │ email: "nam@example.com"         │
      └──────────────────────────────────┘                   └──────────────────────────────────┘
```

---

### G. Authorization (Phân quyền & Chống Lỗ Hổng BOLA / IDOR)

**Authentication** trả lời câu hỏi: *"Bạn là ai?"* $\rightarrow$ Server biết bạn là `User_A`.  
**Authorization** trả lời câu hỏi: *"Bạn có được phép thao tác trên tài nguyên này không?"* $\rightarrow$ Server kiểm tra quyền sở hữu.

#### Nguy cơ nếu THIẾU Authorization:
Lỗ hổng **BOLA (Broken Object Level Authorization)** hay **IDOR (Insecure Direct Object Reference)** — Lỗ hổng số 1 trong OWASP API Security:
Kẻ xấu là `User_B`, nhưng biết được mã `thread_id = "abc-123"` của `User_A`. Nếu server chỉ kiểm tra `User_B` đã đăng nhập hay chưa mà không kiểm tra quyền sở hữu, `User_B` có thể đọc trộm hoặc xóa sạch lịch sử chat của `User_A`!

#### Cách Tit-Chatbot phòng chống triệt để:

**1. Phân quyền xem lịch sử hội thoại** trong [`app.py`](file:///d:/Tit-Chatbot/app.py#L378-L389):
```python
@app.get("/history/{thread_id}")
async def history(thread_id: str, request: Request):
    user = get_current_user(request)
    conv = get_conversation(thread_id)
    conv_user_id = conv.get("user_id") if isinstance(conv, dict) else getattr(conv, "user_id", None)
    
    # BẮT BUỘC KIỂM TRA QUYỀN SỞ HỮU:
    if conv and conv_user_id:
        if not user or str(user["id"]) != str(conv_user_id):
            return JSONResponse(
                {"error": "Bạn không có quyền truy cập cuộc trò chuyện này."},
                status_code=403 # HTTP 403 Forbidden
            )
    ...
```

**2. Phân quyền xóa cuộc trò chuyện** trong [`app.py`](file:///d:/Tit-Chatbot/app.py#L403-L414):
```python
@app.delete("/conversations/{thread_id}")
async def remove_conversation(thread_id: str, request: Request):
    user = get_current_user(request)
    conv = get_conversation(thread_id)
    conv_user_id = conv.get("user_id") if isinstance(conv, dict) else getattr(conv, "user_id", None)
    
    # Chặn hành vi xóa trộm cuộc trò chuyện của người khác:
    if conv and conv_user_id:
        if not user or str(user["id"]) != str(conv_user_id):
            return JSONResponse(
                {"error": "Bạn không có quyền xóa cuộc trò chuyện này."},
                status_code=403
            )
    ...
```

**3. Phân quyền gửi tin nhắn / Stream chat** trong [`app.py`](file:///d:/Tit-Chatbot/app.py#L602-L614):
```python
    user = get_current_user(request)
    user_id = user["id"] if user else None

    # Kiểm tra quyền sở hữu cuộc trò chuyện trước khi cho phép gọi LLM:
    conv = get_conversation(thread_id)
    conv_user_id = conv.get("user_id") if isinstance(conv, dict) else getattr(conv, "user_id", None)
    if conv and conv_user_id:
        if not user or str(user["id"]) != str(conv_user_id):
            return JSONResponse(
                {"error": "Bạn không có quyền gửi tin nhắn trong cuộc trò chuyện này."},
                status_code=403
            )
```

**4. Cô lập Bộ nhớ dài hạn (Long-term Memory)** trong [`database.py`](file:///d:/Tit-Chatbot/database.py#L630-L640):
```python
def search_memory(query: str, user_id: str | ObjectId, limit: int = 5) -> list[str]:
    oid = _to_object_id(user_id)
    if not oid:
        return []
    # Truy vấn bắt buộc phải kèm user_id dạng ObjectId, triệt tiêu nguy cơ rò rỉ trí nhớ giữa các tài khoản:
    filter_criteria = {"user_id": oid, "is_deleted": {"$ne": True}}
    ...
```

---

### H. Bảng Ma Trận So Sánh Tổng Hợp

| Tiêu chí | Session-based (Tit-Chatbot) | JWT (JSON Web Token) | Basic Auth / API Key |
| :--- | :--- | :--- | :--- |
| **Nơi lưu Token** | Server (MongoDB) + Client (Cookie) | Client (LocalStorage hoặc Cookie) | Client (Header) |
| **Nội dung Token** | Chuỗi ngẫu nhiên 256-bit vô nghĩa | Dữ liệu mã hóa Base64 JSON (Claims) | `username:password` mã hóa Base64 |
| **Khả năng Đăng xuất tức thì** | **Ngay lập tức 100%** (Xóa DB) | Rất khó (Phải chờ hết hạn hoặc build Redis Blacklist) | Không thể (Trừ khi đổi mật khẩu) |
| **Chống XSS** | **Tuyệt đối** (nhờ `HttpOnly=True`) | Thường lưu LocalStorage $\rightarrow$ Dễ bị đánh cắp | Dễ bị lộ trong code client |
| **Chống CSRF** | **Tuyệt đối** (nhờ `SameSite=Lax`) | Miễn nhiễm nếu lưu LocalStorage, nhưng gặp rủi ro XSS | Phải gửi kèm Header |
| **Khả năng Scale ngang** | Cần database tập trung (MongoDB/Redis) | Cực tốt (Server không cần lưu state) | Cực tốt |
| **Phù hợp nhất cho** | Web App người dùng cuối, Chatbot cá nhân hóa, Bảo mật cao | Microservices phân tán, Mobile App | Giao tiếp Server-to-Server |

---

## 7. Model Embedding: `paraphrase-multilingual-MiniLM-L12-v2`

* **Số chiều (Dimensions)**: **384 chiều**. Mọi văn bản đưa vào đều được nén thành một vector có đúng 384 con số thực. Đây là "điểm vàng" giúp tiết kiệm RAM và tính toán Cosine Similarity cực nhanh trên CPU.
* **Đa ngôn ngữ (Multilingual)**: Hỗ trợ hơn 50+ ngôn ngữ. Dùng kỹ thuật Teacher-Student để ánh xạ câu tiếng Việt và câu tiếng Anh cùng nghĩa về gần như cùng một tọa độ trong không gian vector.
* **Mean Pooling**: Lấy trung bình cộng vector của toàn bộ các từ trong câu (thay vì chỉ lấy token đầu tiên như BERT gốc), giúp vector nắm bắt trọn vẹn ngữ nghĩa của cả câu.
