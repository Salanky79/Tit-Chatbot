# Phân tích và tài liệu hóa tính năng Admin (Quản trị Người dùng & Tài liệu)

Tài liệu này tổng hợp toàn bộ phân tích hiện trạng, kiến trúc thiết kế, các thay đổi mã nguồn và giải pháp kỹ thuật đã triển khai cho tính năng **Giao diện Quản trị (Admin Panel)** của dự án **Tit-Chatbot**.

---

## 1. Luồng hệ thống trước khi có tính năng Admin

1. **Quản lý người dùng**:
   - `app.py` cung cấp các API xác thực (`/auth/signup`, `/auth/signin`, `/auth/signout`, `/auth/me`), quản lý phiên thông qua cookie `tit_session` hoặc bearer token.
   - `storage/database.py` tương tác với MongoDB:
     - Collection `users` lưu thông tin tài khoản: `username`, `email`, `hashed_password`, `full_name`, `avatar_url`, `created_at`, `updated_at`.
     - Collection `sessions` lưu các phiên đăng nhập kết nối qua `user_id`.
   - Tài khoản người dùng **chưa có trường phân quyền** (`role`) và **chưa có cờ trạng thái** (`is_active`). Bất kỳ ai đăng ký cũng có quyền hạn như nhau.
2. **Quản lý tài liệu (RAG)**:
   - File upload lưu vật lý tại thư mục `uploads/{file_id}_{safe_filename}`.
   - `services/rag.py` cắt nhỏ tài liệu thành các chunks và lưu vào ChromaDB collection `agentic_chatbot_docs_hf`.
   - Metadata của từng chunk chỉ lưu: `thread_id`, `user_id`, `source` (tên file), `page`, `file_type`.
   - **Chưa có định danh upload (`document_id`)**: Nếu người dùng tải lên 2 file trùng tên ở các thời điểm khác nhau, hệ thống chỉ phân biệt bằng `source`, gây rủi ro xóa nhầm hoặc xóa sót chunks.
   - **Chưa có cơ chế xóa tài liệu**: Khi người dùng hoặc admin muốn xóa file, không có API nào hỗ trợ xóa đồng bộ cả file trên đĩa và các vector chunks trong ChromaDB.

---

## 2. Các thay đổi và giải pháp đã triển khai

### 2.1. Phân quyền và kiểm soát trạng thái người dùng (RBAC & Account Status)

- **Schema mở rộng & Mặc định an toàn**:
  - Mỗi tài khoản có thêm trường `role` (`"admin"` hoặc `"user"`) và `is_active` (`True` hoặc `False`).
  - Luồng đăng ký công khai (`/auth/signup`) luôn gán cứng `role: "user"` và `is_active: True`. Người dùng không thể tự cấp quyền Admin qua body JSON.
- **Tương thích ngược (Backward Compatibility)**:
  - Hàm `_public_user()` sử dụng fallback: `user.get("role", "user")` và `user.get("is_active", True)`. Các tài khoản tạo trước tính năng này vẫn hoạt động bình thường mà không cần chạy migration database.
- **Bảo mật đa tầng khi khóa tài khoản**:
  - Chặn ngay tại khâu đăng nhập (`authenticate_user`).
  - Chặn ngay tại khâu đọc session (`get_current_user`): Nếu tài khoản bị khóa trong lúc đang sử dụng, phiên làm việc lập tức bị vô hiệu hóa ở request tiếp theo.
  - Thu hồi session trong MongoDB: Khi khóa tài khoản, toàn bộ session của user đó trong collection `sessions` bị xóa sạch (`sessions.delete_many`).
- **Bảo vệ Admin cuối cùng (Last Admin Protection)**:
  - Hệ thống kiểm tra số lượng admin đang hoạt động (`active_admins <= 1`). Không cho phép thao tác khóa tài khoản nếu đó là Admin cuối cùng của hệ thống.
  - Admin không thể tự khóa tài khoản của chính mình.
- **Chống tấn công ReDoS**:
  - Chuỗi tìm kiếm người dùng được giới hạn độ dài `clean_search[:100]` và thoát ký tự đặc biệt qua `re.escape()`.

### 2.2. Giao diện và API Quản trị

- **Bảo vệ Route & URL**:
  - Hàm `_admin_api_error(request)` kiểm tra phiên và vai trò `role == "admin"`. Trả về `401 Unauthorized` nếu chưa đăng nhập, `403 Forbidden` nếu là user thường.
  - Route view `GET /admin`: Nếu chưa đăng nhập hoặc không phải admin, hệ thống tự động **chuyển hướng (Redirect 303)** về trang chủ `/`, tránh hiển thị trang trắng chứa JSON thô.
- **Tập hợp Endpoints quản trị**:
  - `GET /admin`: Giao diện Admin Dashboard (`templates/admin.html`).
  - `GET /admin/api/users`: Lấy danh sách người dùng có tìm kiếm (`search`), phân trang (`page`, `page_size`). Không để lộ trường `hashed_password`.
  - `PATCH /admin/api/users/{user_id}/status`: Khóa hoặc mở khóa tài khoản người dùng (`is_active: boolean`).
  - `GET /admin/api/users/{user_id}/conversations`: Lấy các hội thoại thuộc user đã chọn, tìm theo tiêu đề và phân trang.
  - `GET /admin/api/users/{user_id}/conversations/{thread_id}/messages`: Lấy tối đa 50 tin nhắn mỗi trang sau khi xác nhận thread thuộc user đó.
  - `GET /admin/api/documents`: Lấy danh sách tài liệu từ ChromaDB, hỗ trợ tìm kiếm theo tên, lọc theo loại file (`file_type`), và lọc theo `user_id` hoặc `username`.
  - `DELETE /admin/api/documents/{document_key}`: Xóa tài liệu đồng bộ cả vector chunks và file vật lý trên đĩa.
- **Trải nghiệm giao diện (UI/UX)**:
  - Giao diện Dark theme tối giản, đồng bộ thẩm mỹ với trang chat chính.
  - Hỗ trợ phím `Enter` trên toàn bộ các ô tìm kiếm/lọc (`onkeydown`).
  - Phân trang kiểm soát chặt chẽ biên: Tự động vô hiệu hóa (`disabled`) nút "Trước" khi ở trang 1 và nút "Tiếp" khi ở trang cuối, chặn nhảy trang vô tận.
  - Hộp thoại xác nhận (`confirm`) trước khi thực hiện các hành động nhạy cảm (Khóa/Mở khóa, Xóa vĩnh viễn).
  - Liên kết "Admin" trên thanh header của `index.html` chỉ hiển thị với tài khoản có `role === "admin"`.
  - Nút "Lịch sử chat" mở lịch sử với tiêu đề `Lịch sử hội thoại của: @username (full_name)`; Admin chọn hội thoại để đọc tin nhắn.
  - Tin user hiển thị bubble căn phải, tin assistant căn trái; nội dung giữ xuống dòng bằng `white-space: pre-wrap` và ngắt dòng dài bằng `word-break: break-word`.

### 2.3. Quản lý tài liệu trong ChromaDB & Xóa đồng bộ

- **Cơ chế Document ID**:
  - Khi tải file lên (`/upload`), backend sinh UUID (`file_id`) làm `document_id`.
  - Toàn bộ chunks sinh ra từ file đó đều được gắn: `document_id`, `file_path` (đường dẫn tuyệt đối), và `uploaded_at` (thời điểm tải lên ISO UTC).
- **Gom nhóm hiển thị (Aggregation)**:
  - Hàm `list_admin_documents()` đọc metadata từ ChromaDB, gom các chunks có cùng `document_id` thành một dòng dữ liệu duy nhất đại diện cho file (gồm tổng số chunks, tên file, người upload, ngày upload).
- **Lọc tài liệu linh hoạt theo `username` hoặc `user_id`**:
  - Admin có thể nhập chuỗi ObjectId 24 ký tự hoặc nhập trực tiếp tên đăng nhập (`username`) vào ô lọc. Backend tự tra cứu `get_user_by_username()` để lấy `id` tương ứng và lọc chính xác.
- **Xóa trực tiếp theo document ID**:
  - Hàm `delete_admin_document(document_key)` truy vấn trực tiếp theo `where={"document_id": document_key}` với `limit=1` để lấy `file_path`.
  - Không gọi `list_admin_documents()` và không nạp toàn bộ collection vào RAM của Python. Chroma thực hiện lọc/xóa theo metadata; thời gian vẫn phụ thuộc vào Chroma và số chunks khớp.
- **Xử lý triệt để lỗi Windows File Lock (WinError 32)**:
  - Trên Windows, nếu file vừa upload hoặc đang bị tiến trình khác giữ file handle, lệnh `unlink()` có thể ném `PermissionError`.
  - Code bọc `try...except OSError`: Nếu xóa lần đầu thất bại, gọi `gc.collect()` để Python giải phóng các file descriptors đang treo và retry xóa lại.
  - Nếu file vẫn bị khóa bởi tiến trình ngoài OS, hệ thống ghi `logger.warning` và **vẫn tiếp tục xóa sạch toàn bộ vector chunks trong ChromaDB** (`collection.delete(where=...)`). Thao tác không bao giờ bị crash 500 giữa chừng.
- **Chống Path Traversal**:
  - Dùng `candidate.is_relative_to(upload_root)` để đảm bảo chỉ cho phép xóa các file nằm trong thư mục `uploads/`, ngăn chặn tấn công xóa file hệ thống ra bên ngoài.

### 2.4. Xem lịch sử chat của user

- `list_admin_conversations()` xác thực `user_id` bằng `_to_object_id()` trước khi truy vấn MongoDB. ID sai định dạng trả về danh sách rỗng; guest conversations không được đưa vào danh sách.
- `get_admin_conversation_messages()` cũng xác thực ObjectId, kiểm tra cặp `user_id` + `thread_id` trong collection `conversations`, rồi truy vấn tin nhắn với cả hai điều kiện. Thread không thuộc user được chọn trả `None`.
- Các truy vấn có phân trang ở MongoDB: 20 hội thoại/trang, 50 tin nhắn/trang. Tin nhắn sắp xếp thời gian tăng dần.
- Hai API mới đều gọi `_admin_api_error()`; API lịch sử người dùng `/history/{thread_id}` không bị nới quyền.

---

## 3. Cấu trúc cây thư mục sau khi hoàn thiện

```text
app.py                         # Admin page routing, Admin APIs, Auth guard
storage/
  database.py                  # User role, is_active, list_users, set_user_active
services/
  rag.py                       # Document metadata, list_admin_documents, delete_admin_document
templates/
  index.html                   # Nút truy cập Admin cho tài khoản Admin
  admin.html                   # Giao diện quản trị Người dùng và Tài liệu
experiments/
  ADMIN_FEATURE_ANALYSIS.md    # Tài liệu phân tích và kiến trúc tính năng Admin
```

---

## 4. Hướng dẫn cấp quyền Admin lần đầu

Hệ thống khóa chặt quyền admin ở backend nên không thể đăng ký tài khoản tự phong Admin. Để cấp quyền cho tài khoản Admin đầu tiên:

1. Đăng ký một tài khoản bình thường trên giao diện web (ví dụ: username `admin_tit`).
2. Mở MongoDB shell (hoặc MongoDB Compass / mongosh) kết nối tới database của dự án và chạy lệnh:
   ```javascript
   db.users.updateOne(
     { username: "admin_tit" },
     { $set: { role: "admin", is_active: true } }
   )
   ```
3. Đăng xuất và đăng nhập lại (hoặc tải lại trang chat), nút **Admin** sẽ xuất hiện trên thanh điều hướng góc trên bên phải. Nhấp vào để mở trang Quản trị tại `/admin`.

---

## 5. Trích đoạn mã nguồn tiêu biểu (Key Code Snippets)

### 5.1. Bảo vệ API và Chuyển hướng Route Admin (`app.py`)

```python
def _admin_api_error(request: Request) -> JSONResponse | None:
    """Chặn API Admin ở backend; ẩn nút trên UI không thay thế được bước kiểm tra này."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Sign-in required."}, status_code=401)
    if user.get("role", "user") != "admin":
        return JSONResponse({"success": False, "error": "Admin access required."}, status_code=403)
    return None

@app.get("/admin")
async def admin_page(request: Request):
    """Render trang quản trị sau khi xác nhận session và role Admin."""
    user = get_current_user(request)
    if not user or user.get("role", "user") != "admin":
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="admin.html", context={})
```

### 5.2. Quản lý trạng thái User & Bảo vệ Admin cuối cùng (`storage/database.py`)

```python
def set_user_active(user_id: str | ObjectId, is_active: bool) -> dict:
    """Khóa/mở khóa tài khoản và giữ lại ít nhất một Admin đang hoạt động."""
    query_id = _to_object_id(user_id)
    if query_id is None:
        raise ValueError("ID người dùng không hợp lệ.")

    users = get_collection("users")
    user = users.find_one({"_id": query_id})
    if not user:
        raise ValueError("Không tìm thấy người dùng.")

    if not is_active and user.get("role", "user") == "admin" and user.get("is_active", True):
        active_admins = users.count_documents({"role": "admin", "is_active": {"$ne": False}})
        if active_admins <= 1:
            raise ValueError("Không thể khóa Admin cuối cùng đang hoạt động.")

    users.update_one(
        {"_id": query_id},
        {"$set": {"is_active": bool(is_active), "updated_at": _now()}},
    )
    if not is_active:
        get_collection("sessions").delete_many({
            "user_id": {"$in": [query_id, str(query_id)]}
        })
    updated_user = users.find_one({"_id": query_id})
    return _public_user(updated_user)
```

### 5.3. Xóa tài liệu an toàn & Chống Windows File Lock (`services/rag.py`)

```python
def delete_admin_document(document_key: str) -> bool:
    """Xóa tài liệu theo document_id: xóa file vật lý và xóa toàn bộ vector chunks."""
    collection = get_vectorstore()._collection
    where_filter = {"document_id": document_key}

    result = collection.get(where=where_filter, limit=1, include=["metadatas"])
    if not result.get("ids"):
        return False

    metadata_list = result.get("metadatas") or []
    metadata = metadata_list[0] if metadata_list else None
    file_path = (metadata or {}).get("file_path") or (metadata or {}).get("image_path")
    if file_path:
        upload_root = Path("uploads").resolve()
        candidate = Path(file_path).resolve()
        if candidate.is_relative_to(upload_root) and candidate.is_file():
            try:
                candidate.unlink()
            except OSError:
                import gc
                gc.collect()
                try:
                    candidate.unlink()
                except OSError as retry_exc:
                    logger.warning(
                        "Không thể xóa file vật lý trên đĩa (%s) do Windows file lock: %s. "
                        "Vẫn tiếp tục xóa vector chunks trong ChromaDB.",
                        candidate,
                        retry_exc,
                    )

    collection.delete(where=where_filter)
    return True
```

### 5.4. Lọc tài liệu theo User ID hoặc Username (`app.py`)

```python
@app.get("/admin/api/documents")
async def admin_list_documents(
    request: Request,
    search: str = "",
    user_id: str = "",
    file_type: str = "",
    page: int = 1,
    page_size: int = 25,
):
    error = _admin_api_error(request)
    if error:
        return error

    documents = list_admin_documents()
    clean_search = search.strip().casefold()[:100]
    if clean_search:
        documents = [doc for doc in documents if clean_search in doc["source"].casefold()]
    clean_user = user_id.strip()
    if clean_user:
        target_user_ids = {clean_user}
        user_info = get_user_by_username(clean_user)
        if user_info:
            target_user_ids.add(str(user_info["id"]))
        documents = [doc for doc in documents if str(doc.get("user_id") or "") in target_user_ids]
    clean_type = file_type.strip().lower()
    if clean_type:
        documents = [doc for doc in documents if (doc.get("file_type") or "").lower() == clean_type]

    ...
```

---

### 5.5. Kiểm tra user/thread và phân trang lịch sử chat

Hàm đọc tin nhắn xác thực user ID, kiểm tra quyền sở hữu conversation trước, rồi chỉ truy vấn messages thuộc cùng user và thread:

```python
oid = _to_object_id(user_id)
if oid is None or not thread_id:
    return None

conversation_query = {
    "user_id": oid,
    "thread_id": thread_id,
    "is_guest": {"$ne": True},
}
conversation = get_collection("conversations").find_one(conversation_query, ...)
if not conversation:
    return None

message_query = {"user_id": oid, "thread_id": thread_id}
messages = get_collection("chat_messages")
total = messages.count_documents(message_query)
cursor = messages.find(message_query, ...).sort("created_at", 1).skip(...).limit(...)
```

API Admin kiểm tra role trước khi gọi DAL và trả `404` nếu DAL không xác nhận được hội thoại:

```python
error = _admin_api_error(request)
if error:
    return error

result = get_admin_conversation_messages(user_id, thread_id, page=page, page_size=page_size)
if result is None:
    return JSONResponse({"success": False, "error": "Conversation not found."}, status_code=404)
```

Giao diện căn bubble theo vai trò và giữ nguyên xuống dòng trong nội dung:

```css
.chat-bubble { white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; }
.chat-bubble.user { align-self: flex-end; }
.chat-bubble.assistant { align-self: flex-start; }
```

## 6. Đánh giá chất lượng và kiểm thử (Verification & Testing)

1. **Kiểm thử phân quyền**:
   - Khách vãng lai và User thường truy cập `/admin` $\rightarrow$ Tự động chuyển hướng về `/`.
   - Gọi trực tiếp API `/admin/api/...` bằng token thường $\rightarrow$ Nhận `403 Forbidden`.
   - Đăng nhập tài khoản Admin $\rightarrow$ Mở `/admin` thành công, hiển thị đầy đủ bảng dữ liệu.
2. **Kiểm thử khóa tài khoản**:
   - Khóa user bình thường $\rightarrow$ Trạng thái chuyển thành "Đã khóa", token bị xóa khỏi database, user lập tức bị từ chối ở request chat kế tiếp.
   - Cố gắng khóa Admin cuối cùng $\rightarrow$ Bị chặn với lỗi `"Không thể khóa Admin cuối cùng đang hoạt động"`.
3. **Kiểm thử tìm kiếm & phím Enter**:
   - Gõ từ khóa tìm kiếm và nhấn phím `Enter` $\rightarrow$ Bảng tự động tải lại dữ liệu khớp từ khóa mà không cần bấm nút "Tìm".
   - Nhập `username` vào ô lọc tài liệu $\rightarrow$ Lọc ra chính xác các tài liệu do user đó tải lên.
4. **Kiểm thử phân trang**:
   - Ở trang đầu tiên, nút "Trước" bị mờ và không thể nhấn (`disabled`).
   - Ở trang cuối cùng, nút "Tiếp" bị mờ và không thể nhấn (`disabled`).
5. **Kiểm thử xóa tài liệu & Windows File Lock**:
   - Xóa tài liệu thành công: File trong thư mục `uploads/` bị xóa và toàn bộ vector chunks biến mất khỏi ChromaDB.
   - Thử nghiệm với file đang bị lock $\rightarrow$ Hệ thống ghi log cảnh báo nhưng vẫn xóa sạch vector chunks trong ChromaDB, trả về trạng thái thành công cho Admin UI.
6. **Kiểm thử xem lịch sử chat**:
   - User ID không hợp lệ không gọi MongoDB; hội thoại phân trang và tin nhắn được trả theo thời gian tăng dần.
   - Thread không thuộc user được chọn trả `404`; request không phải Admin bị chặn ở API guard.
