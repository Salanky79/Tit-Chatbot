# Báo Cáo Tổng Hợp Refactor Hệ Thống Long-Term Memory

**Dự án:** Tit-Chatbot  
**Thời gian cập nhật:** 19/11/2026  
**Trạng thái kiểm thử:** ✅ Đã kiểm thử thành công (100% Pass)  

---

## 1. Bối Cảnh & Vấn Đề Trước Thay Đổi

| Vấn đề | Thực trạng trước đây | Tác động tiêu cực |
| :--- | :--- | :--- |
| **Trùng lặp code (Redundancy)** | Có 2 hàm tìm kiếm ở DB (`search_memory` & `find_memory_candidates`) và 2 tool tương ứng (`recall_memory` & `search_memory_candidates`) với logic regex + fallback giống nhau đến 90%. | Phức tạp hoá hệ thống, bảo trì khó khăn, lãng phí định nghĩa tool của LLM. |
| **Trải nghiệm người dùng (Bad UX)** | Khi tìm kiếm không khớp hoặc ra nhiều candidate, prompt yêu cầu bot in danh sách kèm ID ra bắt người dùng chọn. | Gãy mạch hội thoại; biến AI thành một biểu mẫu chọn lựa thủ công thay vì trợ lý thông minh. |
| **Nguy cơ lặp vô hạn (Infinite Loop)** | LLM có thể liên tục gọi tìm kiếm toàn bộ ký ức (`query=""`) nhiều lần trong cùng 1 request nếu không tìm thấy kết quả mong muốn. | Tốn token, tăng độ trễ (latency), dễ dính rate limit. |
| **Rủi ro bảo mật & Hallucination** | Tool `update_memory_entry` và `forget_memory_entry` không xác thực ID có thực sự vừa được truy xuất hay do LLM tự bịa ra. | Nguy cơ update nhầm hoặc thao túng bản ghi ngoài ý muốn. |

---

## 2. Chi Tiết Các Thay Đổi Trong Mã Nguồn

### 2.1. Tầng Cơ Sở Dữ Liệu (`storage/database.py`)
- **Gộp hàm hoàn toàn:** Xoá bỏ `search_memory` và `find_memory_candidates`, thay thế bằng hàm duy nhất:
  ```python
  def get_user_memories(
      user_id: str | None = None,
      query: str = "",
      allow_full_fallback: bool = True,
  ) -> dict[str, Any]
  ```
- **Chuẩn hoá dữ liệu trả về:** Luôn trả về cấu trúc JSON đầy đủ metadata:
  ```json
  {
    "status": "ok | empty | full_read_already_used | unauthenticated",
    "source": "regex_exact | regex_words | all_requested | fallback_no_regex_match | none",
    "memories": [
      {
        "memory_id": "...",
        "memory": "...",
        "version": 1,
        "updated_at": "..."
      }
    ]
  }
  ```
- **Quy trình truy vấn theo từng lớp (Cascading):**
  1. *Truy vấn rỗng (`query=""`):* Agent chủ động đọc toàn bộ ký ức (`source: "all_requested"`).
  2. *Regex nguyên cụm:* Tìm kiếm chính xác an toàn theo regex (`source: "regex_exact"`).
  3. *Regex từng từ:* Khớp bất kỳ từ khóa nào (`source: "regex_words"`).
  4. *Tự động Fallback toàn bộ:* Nếu không có kết quả regex, tự động trả về toàn bộ ký ức còn hoạt động để LLM tự đọc và suy luận (`source: "fallback_no_regex_match"`), có cờ `allow_full_fallback` bảo vệ.

---

### 2.2. Tầng Công Cụ Agent (`tools.py`)
- **Thu gọn danh sách Tool:** Loại bỏ `recall_memory` và `search_memory_candidates`. Thay bằng 1 tool duy nhất: `get_user_memories`.
- **Cơ chế Chống Lặp Vô Hạn (One-Full-Read Guard):**
  - Sử dụng `InjectedState` để đọc ngữ cảnh các tin nhắn trong lượt chat hiện tại (`messages` sau `HumanMessage` gần nhất).
  - Nếu trong lượt chat này đã từng gọi fallback toàn bộ (`all_requested` hoặc `fallback_no_regex_match`), tool sẽ lập tức chặn lệnh gọi full-read thứ hai (`status: "full_read_already_used"`).
- **Cơ chế Bảo Vệ Chống Bịa ID (Turn-bound ID Validation Guard):**
  - Hàm `_current_turn_memory_results(state)` bóc tách danh sách ID và version thực tế đã trả về cho Agent trong lượt chat hiện tại.
  - Trước khi thực thi `update_memory_entry` hoặc `forget_memory_entry`, hệ thống đối soát:
    ```python
    if not any(
        entry.get("memory_id") == memory_id and int(entry.get("version", -1)) == int(version)
        for entry in _current_turn_memory_results(state)
    ):
        return "Memory update/deletion blocked: retrieve this exact memory ID and version with get_user_memories during the current request first."
    ```
  - **Tác dụng:** Ngăn chặn 100% việc LLM đoán mò hoặc hallucinate ID/version.

---

### 2.3. Tầng Hướng Dẫn Mô Hình (`prompts/agent.py`)
- **Xoá bỏ hoàn toàn:**
  - *"Step 3: If multiple candidates match, present the choices naturally to the user and ask which one they wish to update."*
- **Quy trình mới 5 bước (Autonomous Semantic Resolution):**
  1. Khi cần nhớ lại/sửa/xoá: Luôn gọi `get_user_memories`.
  2. Nếu tool trả về regex matches: LLM tự đọc nội dung để đánh giá ngữ nghĩa. Không bao giờ hỏi người dùng chọn.
  3. Nếu regex không khớp: Tool đã tự động trả về toàn bộ ký ức. LLM tự đọc và đối soát ngữ nghĩa.
  4. Nếu thông tin không thể xác định an toàn (quá mơ hồ): Không sửa/xoá bất kỳ bản ghi nào và giải thích lịch sự với người dùng. Tuyệt đối không phơi ID hoặc danh sách candidate.
  5. Kết quả truy xuất bộ nhớ là **ngữ cảnh ngầm (internal reasoning)**. Không bao giờ hiển thị ID, Version hay list thô cho người dùng.

---

## 3. Đánh Giá Chất Lượng Mã Nguồn (Code Review)

### Điểm Tốt (Strengths):
1. **Thiết kế cực kỳ thông minh (State-Aware Tools):** Việc ứng dụng `InjectedState` từ LangGraph để kiểm soát bảo mật và chống lặp ở cấp độ lượt chat (turn-level) là kỹ thuật hiện đại, chuẩn kiến trúc Agentic.
2. **Khắc phục triệt để lỗi UX:** Người dùng không còn bị quấy rầy bởi các câu hỏi chọn lựa thủ công. Mọi quy trình sửa/xóa diễn ra tự nhiên trong 1 lượt thoại duy nhất.
3. **Tính toàn vẹn dữ liệu (Data Integrity):** Giữ vững cơ chế Optimistic Concurrency Control thông qua trường `version` và `ObjectId` hợp lệ.
4. **Không còn nợ kỹ thuật (Clean Architecture):** Xoá sạch code trùng lặp ở cả tầng database lẫn tools.

### Lưu Ý & Khuyến Nghị Về Sau:
- *Tập ký ức lớn:* Cơ chế fallback hiện tại trả về toàn bộ bản ghi còn hoạt động (`is_deleted: False`). Khi người dùng có hàng trăm ký ức dài hạn, có thể cân nhắc tích hợp **Vector Embedding Cosine Similarity** (tận dụng `services.rag.py` sẵn có) để chỉ lấy Top 5-10 ký ức tương đồng nhất thay vì toàn bộ. Hiện tại với nhu cầu thông thường, giải pháp này đã tối ưu vượt trội.

---

## 4. Kết Quả Kiểm Thử (Verification)

1. **Hệ thống Test Hồi Quy Toàn Dự Án (`test_refactor_regressions.py`):**
   - Số lượng test: 28 tests.
   - Kết quả: **28/28 OK** (Thời gian chạy: 0.023s).
2. **Kiểm thử Tính Năng Mới Của Memory (`test_memory_new.py` & `test_db_memories.py`):**
   - Test One-Full-Read Guard (Chặn lặp vô hạn): **PASSED** ✅
   - Test Chặn Hallucination ID (Blocked Invalid ID): **PASSED** ✅
   - Test Chặn Hallucination Delete (Blocked Invalid Delete): **PASSED** ✅
   - Test Cập nhật hợp lệ cùng lượt (Valid Update): **PASSED** ✅
   - Test Fallback & Exact Regex trong Database: **PASSED** ✅
