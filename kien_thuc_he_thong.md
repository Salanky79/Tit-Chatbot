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

## 6. Phân tích chuyên sâu: Session, Cookie, Token & Authorization

```text
[Trình duyệt (Browser)]                                 [Server Tit-Chatbot]
         │                                                      │
         ├──── 1. POST /auth/signin (user, pass) ──────────────►│ Kiểm tra pass bằng bcrypt
         │                                                      │ Sinh token: secrets.token_urlsafe(32)
         │                                                      │ Lưu session vào MongoDB (user_id, expires_at)
         │◄─── 2. Phản hồi: Set-Cookie: tit_session=<token> ────┤ (Đặt max_age = 7 ngày)
(Lưu cookie vào ổ cứng)                                         │
         │                                                      │
[Hôm sau mở lại tab]                                            │
         ├──── 3. GET /auth/me (Trình duyệt TỰ ĐÍNH KÈM Cookie)─►│ Đọc cookie 'tit_session'
         │                                                      │ Tra MongoDB 'sessions' -> Biết là User A
         │◄─── 4. Trả về: { authenticated: true, user: "A" } ───┤
(Tự động vào tài khoản)                                         │
```

### Các khái niệm cốt lõi:
1. **Tại sao cần Session?** Giao thức web HTTP là **Stateless (Mất trí nhớ)**. Session là cơ chế giúp Server nhớ rằng bạn đã đăng nhập để không bắt bạn nhập lại mật khẩu sau mỗi click chuột.
2. **Token là gì?** Là chuỗi ký tự ngẫu nhiên duy nhất đại diện cho phiên làm việc đó (như tấm vé xem phim hoặc chìa khóa thẻ từ khách sạn).
3. **Cookie là gì?** Là nơi cất giữ token trên trình duyệt. Trình duyệt có tính năng tự động gửi kèm cookie mỗi khi gọi API lên server.
4. **Cơ chế tự động đăng nhập khi mở lại tab (`max_age`)**:
   * Khi server đặt `max_age = 7 * 24 * 3600`, trình duyệt ghi cookie xuống **ổ đĩa cứng** (Persistent Cookie) thay vì RAM.
   * Khi bạn mở lại tab, frontend gọi `/auth/me`, trình duyệt tự móc cookie trong ổ cứng gửi lên $\rightarrow$ Server nhận diện và đăng nhập tự động.
5. **Authentication vs Authorization**:
   * **Authentication (Xác thực)**: *Bạn là ai?* $\rightarrow$ Server kiểm tra token và biết bạn là User A.
   * **Authorization (Ủy quyền / Phân quyền)**: *Bạn có quyền làm gì?* $\rightarrow$ Bạn là User A nhưng cố tình xem/xóa chat của User B, server sẽ chặn lại bằng lỗi **`403 Forbidden`**.

---

## 7. Model Embedding: `paraphrase-multilingual-MiniLM-L12-v2`

* **Số chiều (Dimensions)**: **384 chiều**. Mọi văn bản đưa vào đều được nén thành một vector có đúng 384 con số thực. Đây là "điểm vàng" giúp tiết kiệm RAM và tính toán Cosine Similarity cực nhanh trên CPU.
* **Đa ngôn ngữ (Multilingual)**: Hỗ trợ hơn 50+ ngôn ngữ. Dùng kỹ thuật Teacher-Student để ánh xạ câu tiếng Việt và câu tiếng Anh cùng nghĩa về gần như cùng một tọa độ trong không gian vector.
* **Mean Pooling**: Lấy trung bình cộng vector của toàn bộ các từ trong câu (thay vì chỉ lấy token đầu tiên như BERT gốc), giúp vector nắm bắt trọn vẹn ngữ nghĩa của cả câu.
