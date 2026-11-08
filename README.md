# Tit Chatbot — Agentic AI Chatbot with RAG & Context Optimization

Tit Chatbot là một hệ thống chatbot AI toàn diện, đa phương thức (Multimodal Agentic AI) được phát triển bằng **Python 3.12+**, **FastAPI**, **LangGraph**, **Groq LLM**, **ChromaDB**, và **MongoDB**. 

Hệ thống được thiết kế hướng tới khả năng tự chủ của Agent: tự động lập luận và gọi công cụ (Function Calling), hỏi đáp thông minh trên tài liệu cá nhân (RAG), tóm tắt văn bản dài đệ quy (Recursive Map-Reduce Summarization), phân tích hình ảnh (Vision AI), ghi nhớ ngữ cảnh dài hạn (Long-term Memory), và stream phản hồi theo thời gian thực (Server-Sent Events — SSE).

Đặc biệt, dự án tích hợp **Bộ Tối Ưu Hóa Ngữ Cảnh & Ngân Sách Token (`context_manager.py`)** giúp giải quyết triệt để các bài toán thường gặp của Agentic AI: phình to ngữ cảnh, cạn kiệt token, tràn context window và vượt ngưỡng rate-limit (TPM) của nhà cung cấp LLM.

---

## Mục lục

- [1. Kiến trúc tổng thể hệ thống](#1-kiến-trúc-tổng-thể-hệ-thống)
- [2. Luồng xử lý End-to-End (Workflow)](#2-luồng-xử-lý-end-to-end-workflow)
  - [2.1. Luồng hội thoại thời gian thực (SSE Stream)](#21-luồng-hội-thoại-thời-gian-thực-sse-stream)
  - [2.2. Luồng tải lên và xử lý tài liệu đa định dạng (RAG & OCR)](#22-luồng-tải-lên-và-xử-lý-tài-liệu-đa-định-dạng-rag--ocr)
  - [2.3. Luồng tóm tắt văn bản dài đệ quy (Map-Reduce Pipeline)](#23-luồng-tóm-tắt-văn-bản-dài-đệ-quy-map-reduce-pipeline)
  - [2.4. Luồng phân tích hình ảnh (Vision AI)](#24-luồng-phân-tích-hình-ảnh-vision-ai)
  - [2.5. Luồng sinh câu hỏi gợi ý thông minh (Follow-up Suggestions)](#25-luồng-sinh-câu-hỏi-gợi-ý-thông-minh-follow-up-suggestions)
- [3. Bộ Tối Ưu Hóa Ngữ Cảnh & Ngân Sách Token (Core Feature)](#3-bộ-tối-ưu-hóa-ngữ-cảnh--ngân-sách-token-core-feature)
  - [Bước 1: Trimming conversation history (Cắt tỉa & Tóm tắt lượt cũ)](#bước-1-trimming-conversation-history-cắt-tỉa--tóm-tắt-lượt-cũ)
  - [Bước 2: Loại bỏ tool results cũ (Pruning Old Tool Results)](#bước-2-loại-bỏ-tool-results-cũ-pruning-old-tool-results)
  - [Bước 3: Tách context theo tác vụ (Task Context Isolation)](#bước-3-tách-context-theo-tác-vụ-task-context-isolation)
  - [Bước 4: Kiểm soát token trước khi gọi LLM (Token Budget Enforcement)](#bước-4-kiểm-soát-token-trước-khi-gọi-llm-token-budget-enforcement)
- [4. Chi tiết các Module & Thành phần trong Codebase](#4-chi-tiết-các-module--thành-phần-trong-codebase)
  - [4.1. Web Server & API Gateway (`app.py`)](#41-web-server--api-gateway-apppy)
  - [4.2. LangGraph Agent Orchestrator (`agent.py`)](#42-langgraph-agent-orchestrator-agentpy)
  - [4.3. Quản lý Ngữ cảnh & Token (`context_manager.py`)](#43-quản-lý-ngữ-cảnh--token-context_managerpy)
  - [4.4. Hệ thống 7 Công cụ Agent (`tools.py`)](#44-hệ-thống-7-công-cụ-agent-toolspy)
  - [4.5. Xử lý Tài liệu, OCR & Vector Store (`rag.py`)](#45-xử-lý-tài-liệu-ocr--vector-store-ragpy)
  - [4.6. Tóm tắt đệ quy Map-Reduce (`summarization.py`)](#46-tóm-tắt-đệ-quy-map-reduce-summarizationpy)
  - [4.7. Thị giác máy tính (`image_analysis.py`)](#47-thị-giác-máy-tính-image_analysispy)
  - [4.8. Sinh gợi ý tiếp nối (`followups.py`)](#48-sinh-gợi-ý-tiếp-nối-followupspy)
  - [4.9. Cơ sở dữ liệu MongoDB DAL (`database.py` & `mongo_client.py`)](#49-cơ-sở-dữ-liệu-mongodb-dal-databasepy--mongo_clientpy)
  - [4.10. Giao diện người dùng Web (`templates/index.html`)](#410-giao-diện-người-dùng-web-templatesindexhtml)
- [5. Bảng công nghệ & Thư viện sử dụng](#5-bảng-công-nghệ--thư-viện-sử-dụng)
- [6. Cấu trúc thư mục dự án](#6-cấu-trúc-thư-mục-dự-án)
- [7. Các API Endpoints](#7-các-api-endpoints)
- [8. Hướng dẫn cài đặt và khởi chạy](#8-hướng-dẫn-cài-đặt-và-khởi-chạy)
- [9. Cấu hình biến môi trường (`.env`)](#9-cấu-hình-biến-môi-trường-env)
- [10. Kiểm thử tự động (Test Suite)](#10-kiểm-thử-tự-động-test-suite)
- [11. Từ điển thuật ngữ](#11-từ-điển-thuật-ngữ)

---

## 1. Kiến trúc tổng thể hệ thống

Hệ thống được tổ chức theo kiến trúc phân tầng dạng module, tách biệt rõ ràng giữa tầng giao diện, tầng gateway, tầng điều phối Agent, tầng tối ưu context và tầng lưu trữ:

```mermaid
flowchart TD
    subgraph ClientLayer ["Client Layer (Trình duyệt)"]
        UI["Web UI: HTML5, CSS3, JS<br/>(Markdown, Syntax Highlight, Voice Input, SSE Client)"]
    end

    subgraph GatewayLayer ["Gateway Layer (FastAPI app.py)"]
        Router["FastAPI Routes & Middlewares"]
        SSEGen["SSE Event Generator"]
        Filter["Chunk Filter (should_stream_chunk)"]
    end

    subgraph AgentLayer ["Agentic Orchestration (LangGraph agent.py)"]
        GraphState["AgentState: messages + conversation_summary"]
        ChatbotNode["Chatbot Node (ChatGroq)"]
        Condition{"tools_condition"}
        ToolNode["ToolNode (LangGraph Prebuilt)"]
    end

    subgraph OptimizationLayer ["Context Optimization (context_manager.py)"]
        S1["(1) Trimming turns & Tóm tắt lượt cũ"]
        S2["(2) Loại bỏ tool results cũ khỏi completed turns"]
        S3["(3) Tách context theo tác vụ chuyên biệt"]
        S4["(4) Kiểm soát token budget trước khi invoke"]
    end

    subgraph ToolsLayer ["Execution Engines & Tools"]
        T1["calculator"]
        T2["search_uploaded_documents (RAG)"]
        T3["summarize_uploaded_document (Map-Reduce)"]
        T4["analyze_uploaded_image (Vision)"]
        T5["remember_this & recall_memory"]
        T6["web_search (Tavily Search)"]
    end

    subgraph StorageLayer ["Persistence & Vector Database"]
        MongoDB[("MongoDB Atlas<br/>• conversations<br/>• chat_messages<br/>• long_term_memory<br/>• Checkpoints")]
        ChromaDB[("ChromaDB Vector Store<br/>Embeddings local CPU")]
        LocalFiles[("Local Storage<br/>uploads/ & temp files")]
    end

    UI <-->|HTTP / SSE Stream| Router
    Router --> SSEGen
    SSEGen --> Filter
    Filter <--> AgentLayer

    AgentLayer --> OptimizationLayer
    OptimizationLayer --> ChatbotNode
    ChatbotNode --> Condition
    Condition -->|Cần gọi Tool| ToolNode
    Condition -->|Hoàn tất| SSEGen
    ToolNode --> ToolsLayer
    ToolsLayer --> ChatbotNode

    AgentLayer <-->|State Checkpoint| MongoDB
    Router <-->|Lưu chat & history| MongoDB
    ToolsLayer <-->|Query & Read| ChromaDB
    ToolsLayer <-->|Read File / Image| LocalFiles
```

---

## 2. Luồng xử lý End-to-End (Workflow)

### 2.1. Luồng hội thoại thời gian thực (SSE Stream)
1. **Người dùng nhập tin nhắn:** Client gửi `POST /chat/stream` chứa `message`, `thread_id`, và `model` đã chọn.
2. **Khởi tạo và lưu vết:**
   - FastAPI (`app.py`) ghi nhận hoặc cập nhật cuộc hội thoại (`create_or_update_conversation`).
   - Tin nhắn của người dùng được lưu vào collection `chat_messages` của MongoDB.
   - Luồng kích hoạt `set_current_thread_id(thread_id)` để mọi công cụ bên trong đều nắm được thread hiện hành.
3. **Gọi Agent qua LangGraph:**
   - Graph tải trạng thái `AgentState` từ MongoDB Checkpointer ứng với `thread_id`.
   - Node `chatbot` tiếp nhận danh sách tin nhắn.
4. **Tiền xử lý tối ưu hóa Context (`context_manager.py`):**
   - **Trimming:** Giữ $N$ lượt gần nhất (`MAX_HISTORY_TURNS = 4`), tóm tắt các lượt cũ đưa vào `SystemMessage`.
   - **Pruning:** Xóa kết quả công cụ cũ (web search, RAG) từ các lượt trước; chỉ giữ lượt hỏi-đáp sạch.
   - **Enforce Budget:** Tính toán tổng token (System + Tools + History + Output) và cắt ngắn nếu vượt ngưỡng `MAX_CONTEXT_TOKENS = 6000`.
5. **LLM xử lý và rẽ nhánh:**
   - `llm_with_tools.invoke(final_messages)`: ChatGroq suy luận.
   - Nếu LLM sinh `tool_calls` $\rightarrow$ `tools_condition` chuyển sang `tool_node` $\rightarrow$ công cụ thực thi $\rightarrow$ kết quả `ToolMessage` quay lại `chatbot` để LLM tổng hợp lời giải.
   - Nếu LLM sinh văn bản cuối cùng $\rightarrow$ kết thúc graph.
6. **Streaming & Gợi ý tiếp nối:**
   - `should_stream_chunk` lọc bỏ các gói tin kỹ thuật, chỉ stream văn bản thuần qua SSE: `data: {"token": "..."}`.
   - Lưu câu trả lời của Assistant vào MongoDB `chat_messages`.
   - Kích hoạt `generate_followup_questions` sinh 2 gợi ý câu hỏi tiếp theo và stream về client: `data: {"suggestions": [...]}`.

---

### 2.2. Luồng tải lên và xử lý tài liệu đa định dạng (RAG & OCR)

```mermaid
flowchart TD
    Upload([Người dùng Upload File / Ảnh]) --> Endpoint["POST /upload (app.py)"]
    Endpoint --> CheckType{Kiểm tra đuôi file}

    CheckType -->|Ảnh: JPG, PNG, WEBP| ImageFlow["_looks_like_image & Kiểm tra <= 14MB"]
    ImageFlow --> SaveImg["Lưu vào uploads/ và gọi add_image_to_rag"]
    SaveImg --> ChromaImg["Lưu Document vào ChromaDB (file_type: image)"]

    CheckType -->|Tài liệu: PDF, DOCX, TXT, MD, PY, CSV| DocFlow["Lưu vào uploads/"]
    DocFlow --> Dispatcher["_load_documents (rag.py)"]
    
    Dispatcher -->|PDF| PDFReader["_read_pdf (pypdf + PyMuPDF OCR fallback)"]
    Dispatcher -->|DOCX| DOCXReader["_read_docx (docx2txt)"]
    Dispatcher -->|TXT/MD/PY/CSV| TextReader["_read_text (UTF-8)"]

    PDFReader --> CheckScan{Số ký tự < 40?}
    CheckScan -->|Đúng| PyMuPDF["Kích hoạt PyMuPDF OCR (Tesseract DPI 300, vie+eng)"]
    CheckScan -->|Sai| NativeText["Lấy text trực tiếp từ trang PDF"]

    PyMuPDF --> Splitter["RecursiveCharacterTextSplitter (chunk_size=900, overlap=150)"]
    NativeText --> Splitter
    DOCXReader --> Splitter
    TextReader --> Splitter

    Splitter --> Embeddings["HuggingFaceEmbeddings (paraphrase-multilingual local CPU)"]
    Embeddings --> ChromaDocs["Lưu vào ChromaDB kèm metadata (thread_id, page, source, index)"]
    ChromaDocs --> Response["Trả JSON: success, chunks count, pages count"]
```

---

### 2.3. Luồng tóm tắt văn bản dài đệ quy (Map-Reduce Pipeline)

Khi người dùng yêu cầu tóm tắt toàn bộ file (ví dụ sách, tiểu luận, giáo trình):
1. Tool `summarize_uploaded_document` kiểm tra tên file cần tóm tắt trong thread hiện tại.
2. Hàm `get_document_chunks` đọc **toàn bộ các chunk** của file trong ChromaDB (sắp xếp tuần tự theo `page`, `start_index`, `chunk_index`), không dùng similarity search để tránh bỏ sót thông tin.
3. Chuyển giao các chunk cho pipeline [`summarize_documents`](file:///d:/Tit-Chatbot/summarization.py#L107):
   - **Tách biệt ngữ cảnh tuyệt đối (Step 3):** Hàm `validate_and_isolate_summarization_context` lọc sạch các chunk, chỉ truyền nội dung tài liệu và metadata trang vào pipeline, **tuyệt đối không đưa lịch sử chat vào**.
   - **Pha Map:** Chia chunks thành các nhóm nhỏ theo ngân sách `effective_chunk_budget`. Mỗi nhóm được LLM tóm tắt trung thực, bảo toàn các định nghĩa, số liệu và kết luận cốt lõi.
   - **Pha Reduce:** Gom các bản tóm tắt trung gian thành nhóm mới và tiếp tục gọi LLM gộp lại. Quá trình này lặp đệ quy qua từng cấp độ (`level 1`, `level 2`,...) cho đến khi chỉ còn đúng 1 bản tóm tắt hoàn chỉnh duy nhất.
4. Trả kết quả tóm tắt về cho Agent. Khi đưa vào `ToolMessage`, cơ chế Step 4 sẽ kiểm soát ngân sách token để không làm tràn context của Agent.

---

### 2.4. Luồng phân tích hình ảnh (Vision AI)
1. Người dùng đặt câu hỏi về hình ảnh (ví dụ: *"Bức ảnh này vẽ biểu đồ gì?", "Đọc văn bản trong ảnh"*).
2. Agent kích hoạt tool `analyze_uploaded_image(question, filename)`.
3. Hàm tìm đường dẫn ảnh đã upload thuộc `thread_id` hiện hành.
4. [`analyze_image`](file:///d:/Tit-Chatbot/image_analysis.py#L25) thực hiện:
   - Đọc bytes, kiểm tra kích thước ($\le 14\text{ MB}$), chuyển đổi thành chuỗi Base64 Data URL.
   - **Kiểm soát token:** Giới hạn câu hỏi $\le 400$ token để không làm tràn cửa sổ ngữ cảnh của model thị giác.
   - **Tách context tác vụ:** Prompt thị giác hoàn toàn độc lập, chỉ gồm ảnh và câu hỏi trực quan.
   - Gọi mô hình Vision (`qwen/qwen3.8-27b` hoặc cấu hình qua `GROQ_VISION_MODEL`) để sinh lời giải thích chi tiết.

---

### 2.5. Luồng sinh câu hỏi gợi ý thông minh (Follow-up Suggestions)
1. Ngay sau khi câu trả lời chính hoàn thành stream, `app.py` gọi hàm [`generate_followup_questions`](file:///d:/Tit-Chatbot/followups.py#L149).
2. **Tối ưu hóa token:**
   - Loại bỏ các khối code dài ````...```` bằng `[Code snippet omitted]` và gỡ bỏ các attachment thô.
   - Cắt tỉa `user_message` xuống tối đa 250 token và `assistant_answer` xuống tối đa 500 token.
   - Giúp tiết kiệm hơn **70% token** so với việc gửi nguyên văn văn bản dài.
3. LLM sinh đúng 2 câu hỏi tiếp nối mang tính gợi mở, phân tích theo tiếng Việt hoặc tiếng Anh tương ứng với ngôn ngữ người dùng.
4. Nếu gặp sự cố mạng hoặc lỗi API, hệ thống tự động kích hoạt `fallback_followup_questions` tạo 2 gợi ý cục bộ bằng biểu thức chính quy (Regex) mà không cần gọi API.

---

## 3. Bộ Tối Ưu Hóa Ngữ Cảnh & Ngân Sách Token (Core Feature)

Module [`context_manager.py`](file:///d:/Tit-Chatbot/context_manager.py) triển khai 4 bước chuẩn mực theo đúng thứ tự logic:

```mermaid
flowchart LR
    M[Messages từ MongoDB State] --> Step1["(1) Trimming turns<br/>Tóm tắt các lượt cũ"]
    Step1 --> Step2["(2) Loại bỏ tool results cũ<br/>Xóa dumps khỏi completed turns"]
    Step2 --> Step3["(3) Tách context tác vụ<br/>Cách ly Map-Reduce & Vision"]
    Step3 --> Step4["(4) Kiểm soát Token Budget<br/>Tính toán tổng & Trim an toàn"]
    Step4 --> LLM[ChatGroq Invoke]
```

### Bước 1: Trimming conversation history (Cắt tỉa & Tóm tắt lượt cũ)
- **Hàm:** `group_messages_into_turns`, `summarize_old_messages`, `trim_conversation_history`.
- **Cơ chế:**
  - Nhóm tin nhắn thành các lượt hội thoại (`turns`).
  - Giữ lại tối đa `DEFAULT_MAX_HISTORY_TURNS = 4` lượt gần nhất trong bộ nhớ tích cực.
  - Các lượt cũ hơn được chuyển cho hàm `summarize_old_messages` để tóm tắt thành các gạch đầu dòng ngắn gọn (bảo toàn sở thích, thông tin cá nhân, tên riêng, nội dung cốt lõi).
  - Bản tóm tắt được lưu vào `AgentState.conversation_summary`, tự động đồng bộ vào MongoDB Checkpoint và đưa vào đầu prompt qua `SystemMessage`.
  - Có cơ chế **Local Extractive Fallback** đảm bảo hệ thống không gián đoạn khi LLM tóm tắt gặp sự cố mạng.

### Bước 2: Loại bỏ tool results cũ (Pruning Old Tool Results)
- **Hàm:** `prune_old_tool_results`.
- **Cơ chế:**
  - **Active Turn (Lượt đang diễn ra):** Từ `HumanMessage` cuối cùng trở đi, nếu có `AIMessage(tool_calls)` và `ToolMessage` vừa được trả về, chúng **bắt buộc được giữ nguyên 100%** để LLM đọc và tổng hợp câu trả lời.
  - **Completed Turns (Các lượt trong quá khứ đã kết thúc):** Kết quả web search, RAG chunks hay memory dumps của các lượt cũ đã được Assistant tổng hợp xong ở lượt trước. Hàm sẽ loại bỏ toàn bộ `ToolMessage` và `AIMessage(tool_calls)` trung gian cũ, chỉ giữ lại cặp `HumanMessage` và `AIMessage(final answer)`.
  - Giảm thiểu ngay lập tức từ **2.000 đến 6.000+ token thừa** trong mỗi request tiếp theo!

### Bước 3: Tách context theo tác vụ (Task Context Isolation)
- **Hàm:** `validate_and_isolate_summarization_context`.
- **Cơ chế:**
  - Đảm bảo các tác vụ chuyên biệt không bị "nhiễm" ngữ cảnh trò chuyện:
    - **Tóm tắt PDF Map-Reduce:** Chỉ nhận tài liệu và SystemMessage hướng dẫn tóm tắt trung thực, tuyệt đối không nhận chat history.
    - **Thị giác máy tính (Vision AI):** Chỉ nhận ảnh và câu hỏi trực quan, loại bỏ các lịch sử chat trước đó.
    - **Sinh câu hỏi gợi ý:** Chỉ nhận tóm tắt ngắn của lượt trao đổi gần nhất, đã lọc sạch code blocks và file dumps thô.

### Bước 4: Kiểm soát token trước khi gọi LLM (Token Budget Enforcement)
- **Hàm:** `estimate_tokens`, `estimate_message_tokens`, `estimate_tool_schemas_tokens`, `calculate_token_breakdown`, `enforce_token_budget`.
- **Công thức tính toán tổng token:**
  $$\text{Total Tokens} = T_{\text{system}} + T_{\text{tool\_schemas}} + T_{\text{history}} + T_{\text{tool\_results}} + \text{Reserved Output}$$
- **Quy tắc ước tính token an toàn:**
  Sử dụng chặn trên `ceil(len(text) / 3.0)` cho cả tiếng Việt và tiếng Anh mà không phụ thuộc vào tokenizer ngoài (tránh lỗi tải mạng khi chạy sandbox).
- **Cơ chế can thiệp khi $\text{Total Tokens} > \text{MAX\_CONTEXT\_TOKENS}$ (6.000 tokens):**
  1. **Rút gọn Tool Results:** Nếu kết quả công cụ đang chạy vượt quá `MAX_TOOL_RESULT_TOKENS = 1800`, tự động cắt ngắn và bổ sung thông báo rút gọn.
  2. **Cắt tỉa tin nhắn cũ:** Lần lượt loại bỏ các tin nhắn cũ từ đầu danh sách cho đến khi vừa vặn ngân sách, đồng thời đảm bảo không làm đứt gãy cặp tin nhắn gọi tool của lượt hiện tại.

---

## 4. Chi tiết các Module & Thành phần trong Codebase

### 4.1. Web Server & API Gateway (`app.py`)
- Khởi tạo FastAPI application, cấu hình Jinja2 templates (`templates/index.html`).
- Tạo các thư mục cần thiết: `uploads/`, `data/`.
- Quản lý các endpoint RESTful: tải file (`/upload`), xem danh sách hội thoại (`/conversations`), xem lịch sử tin nhắn (`/history/{thread_id}`).
- Stream Server-Sent Events qua endpoint `/chat/stream`:
  - `should_stream_chunk`: Ngăn chặn rò rỉ metadata, tên node, ToolMessage hay tool_calls lên màn hình.
  - `extract_text_from_chunk`: Trích xuất chuỗi văn bản từ cấu trúc chuỗi hoặc danh sách text chunk của LangChain.
  - Tự động kích hoạt luồng sinh câu hỏi gợi ý sau khi stream câu trả lời xong.

### 4.2. LangGraph Agent Orchestrator (`agent.py`)
- Quản lý danh sách mô hình cho phép: `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`, `openai/gpt-oss-120b`.
- Hàm `normalize_model_name`: Chuẩn hóa và fallback về model mặc định nếu người dùng truyền model không hợp lệ.
- Cấu trúc trạng thái mở rộng:
  ```python
  class AgentState(TypedDict, total=False):
      messages: Annotated[list[BaseMessage], add_messages]
      conversation_summary: str
  ```
- Node `chatbot_node`: Gọi `prepare_context_for_llm` trước khi gọi mô hình ChatGroq đã được gắn 7 tools bằng `bind_tools`.
- Cấu hình `MongoDBSaver`: Lưu checkpoint của graph vào database MongoDB Atlas (`tit_chatbot_checkpoints`), duy trì bộ nhớ đa phiên.
- Hệ thống Caching `_AGENT_CACHE`: Đảm bảo mỗi model chỉ biên dịch StateGraph một lần duy nhất.

### 4.3. Quản lý Ngữ cảnh & Token (`context_manager.py`)
- Cung cấp toàn bộ các hàm cốt lõi cho 4 bước tối ưu hóa ngữ cảnh và ngân sách token.
- Đóng vai trò là "người gác cổng" kiểm tra và định dạng prompt trước khi bất kỳ lệnh gọi LLM nào được thực thi.

### 4.4. Hệ thống 7 Công cụ Agent (`tools.py`)
- Quản lý biến toàn cục `CURRENT_THREAD_ID` và hàm `set_current_thread_id`.
- Danh sách 7 công cụ được trang bị docstring chi tiết hỗ trợ Function Calling:
  1. `calculator(expression: str)`: Đánh giá biểu thức toán học qua hàm `eval` an toàn (chỉ cho phép thư viện `math` và các hàm số học cơ bản).
  2. `search_uploaded_documents(query: str)`: Truy vấn RAG tài liệu theo thread.
  3. `summarize_uploaded_document(filename: str)`: Tóm tắt trọn vẹn tài liệu đã tải lên bằng Map-Reduce.
  4. `analyze_uploaded_image(question: str, filename: str)`: Phân tích ảnh tải lên bằng Vision model.
  5. `remember_this(memory: str)`: Lưu thông tin cần ghi nhớ vào MongoDB.
  6. `recall_memory(query: str)`: Tra cứu các mục ghi nhớ đã lưu.
  7. `web_search`: Công cụ tìm kiếm web thời gian thực qua Tavily Search API.

### 4.5. Xử lý Tài liệu, OCR & Vector Store (`rag.py`)
- Khởi tạo `HuggingFaceEmbeddings` chạy cục bộ trên CPU với `local_files_only=True`, loại bỏ hoàn toàn hiện tượng thử lại kết nối mạng gây treo hệ thống khi offline.
- Quản lý ChromaDB vectorstore tại thư mục `chroma_db_hf`.
- Đọc tài liệu:
  - `_read_pdf`: Trích xuất text từng trang; tự động kích hoạt PyMuPDF + Tesseract OCR khi số ký tự $< 40$ (hỗ trợ song ngữ `vie+eng` ở độ phân giải 300 DPI).
  - `_read_docx`: Dùng `docx2txt`.
  - `_read_text`: Đọc file UTF-8 (`.txt`, `.md`, `.py`, `.csv`).
- Chunking: `RecursiveCharacterTextSplitter` với `chunk_size=900`, `chunk_overlap=150`, bảo tồn metadata: `thread_id`, `source`, `page`, `chunk_index`, `start_index`.
- Các hàm tra cứu: `retrieve_from_rag`, `get_document_chunks`, `list_document_sources`, `add_image_to_rag`, `list_uploaded_images`.

### 4.6. Tóm tắt đệ quy Map-Reduce (`summarization.py`)
- Định nghĩa ngân sách tóm tắt: `DEFAULT_INPUT_TOKEN_BUDGET = 6000`, `SUMMARY_OUTPUT_TOKENS = 1200`.
- Hàm `_groups_by_budget`: Đóng gói các đoạn văn bản vào từng nhóm dựa theo ngân sách token thực tế đã trừ hao overhead.
- Hàm `summarize_documents`:
  - Thực hiện xác thực và cô lập ngữ cảnh qua `validate_and_isolate_summarization_context`.
  - Thực hiện pha Map song song trên từng nhóm.
  - Thực hiện đệ quy pha Reduce nhiều tầng cho đến khi đạt được bản tóm tắt cuối cùng.
  - Đảm bảo tính toàn vẹn: nếu bất kỳ pass nào lỗi thì raise error, không bao giờ trả về bản tóm tắt cụt lủn.

### 4.7. Thị giác máy tính (`image_analysis.py`)
- Giới hạn kích thước ảnh: $\le 14\text{ MB}$.
- Hỗ trợ các định dạng: `.jpg`, `.jpeg`, `.png`, `.webp`.
- Hàm `analyze_image`:
  - Kiểm soát token câu hỏi trực quan: $\le 400$ token.
  - Đóng gói ảnh thành chuỗi Base64 Data URL.
  - Gọi ChatGroq Vision model với `max_tokens=1200`.

### 4.8. Sinh gợi ý prompt tiếp nối (`followups.py`)
- Hàm `generate_followup_questions`:
  - Lọc code blocks và dữ liệu thừa bằng `_clean_content_for_followup`.
  - Giới hạn token đầu vào (user $\le 250$, assistant $\le 500$).
  - System prompt định hướng tạo **User Prompt** (câu lệnh / yêu cầu từ góc nhìn người dùng gửi tới AI), tuyệt đối loại bỏ các đại từ và câu hỏi từ phía trợ lý như "Bạn có muốn...", "Bạn có quan tâm...", "Would you like...".
  - Hàm `_clean_prompt_suggestion`: Bộ lọc hậu xử lý Regex tự động chuyển đổi mọi câu hỏi kiểu trợ lý còn sót thành câu lệnh hành động trực tiếp của người dùng.
  - Parse kết quả linh hoạt từ JSON hoặc danh sách phân dòng.
- Hàm `fallback_followup_questions`: Thuật toán sinh gợi ý prompt người dùng cục bộ hỗ trợ tiếng Việt và tiếng Anh khi model offline.

### 4.9. Cơ sở dữ liệu MongoDB DAL (`database.py` & `mongo_client.py`)
- Singleton Client trong `mongo_client.py` sử dụng chứng chỉ an toàn `certifi` và Google DNS (`8.8.8.8`) cho truy vấn SRV.
- Tạo index tự động:
  - `conversations`: index duy nhất trên `thread_id`, index giảm dần trên `updated_at`.
  - `chat_messages`: index kép trên `thread_id` và `created_at`.
  - `long_term_memory`: index trên `thread_id` và Text Index trên trường `memory`.
- Các hàm nghiệp vụ: `create_or_update_conversation`, `list_conversations`, `save_chat_message`, `get_chat_history`, `save_memory`, `search_memory`.

### 4.10. Giao diện người dùng Web (`templates/index.html`)
- Giao diện hiện đại tông màu tối (Dark Theme), tương thích mọi kích thước màn hình.
- Tích hợp Marked.js phân tích cú pháp Markdown và Highlight.js tô màu cú pháp code.
- Tính năng:
  - Nhập liệu bằng giọng nói (Speech-to-Text / Voice Dictation).
  - Kéo-thả hoặc đính kèm tài liệu và ảnh.
  - Hiển thị thanh tiến trình trực quan khi AI gọi công cụ (`addToolProgress`).
  - Hiển thị danh sách 2 câu hỏi gợi ý dưới dạng clickable pills.
  - Quản lý danh sách lịch sử các cuộc hội thoại ở sidebar bên trái.

---

## 5. Bảng công nghệ & Thư viện sử dụng

| Tên thư viện | Phiên bản | Mục đích sử dụng |
|---|---|---|
| `fastapi` | 0.136.3 | Framework xây dựng RESTful API tốc độ cao |
| `uvicorn` | 0.48.0 | ASGI web server chạy ứng dụng FastAPI |
| `jinja2` | 3.1.6 | Template engine render trang HTML |
| `python-multipart` | 0.0.29 | Xử lý dữ liệu form và file upload |
| `python-dotenv` | 1.2.2 | Quản lý biến môi trường từ file `.env` |
| `langchain` | 1.3.2 | Framework xây dựng ứng dụng LLM |
| `langchain-core` | >= 1.7.0 | Định nghĩa Messages, Tools, Runnable |
| `langgraph` | 1.2.2 | Điều phối workflow dạng đồ thị trạng thái Agentic |
| `langchain-groq` | Mới nhất | Tích hợp các mô hình suy luận tốc độ cao của Groq |
| `langgraph-checkpoint-mongodb` | >= 0.5.1 | Lưu trữ trạng thái phiên chat vào MongoDB |
| `pymongo` | >= 4.18.2 | Trình điều khiển kết nối MongoDB chính thức |
| `langchain-chroma` | 1.1.0 | Vectorstore lưu trữ và tìm kiếm vector tương đồng |
| `chromadb` | 1.5.9 | Cơ sở dữ liệu vector mã nguồn mở |
| `langchain-huggingface` | Mới nhất | Mô hình Sentence Transformers embeddings local |
| `pypdf` | 6.12.2 | Trích xuất văn bản từ tài liệu PDF |
| `pymupdf` | >= 1.24 | Mở PDF và xử lý OCR nâng cao |
| `docx2txt` | 0.9 | Trích xuất văn bản từ tài liệu Microsoft Word (.docx) |
| `langchain-tavily` | 0.2.18 | Công cụ tìm kiếm web thời gian thực qua Tavily API |
| `certifi` | Mới nhất | Cung cấp chứng chỉ SSL/TLS CA chuẩn |

---

## 6. Cấu trúc thư mục dự án

```text
Tit-Chatbot/
├── app.py                         # FastAPI routes, file upload, SSE streaming
├── agent.py                       # LangGraph agent, AgentState, MongoDBSaver
├── context_manager.py             # Bộ tối ưu hóa ngữ cảnh và kiểm soát token
├── summarization.py               # Thuật toán tóm tắt văn bản dài đệ quy Map-Reduce
├── followups.py                   # Bộ sinh 2 câu hỏi gợi ý thông minh
├── image_analysis.py              # Xử lý hình ảnh và thị giác máy tính với Groq Vision
├── tools.py                       # Registry và hiện thực 7 công cụ của Agent
├── rag.py                         # Đọc file, OCR scan, text chunking, ChromaDB
├── database.py                    # Tầng DAL tương tác với MongoDB
├── mongo_client.py                # MongoDB Client Singleton và cấu hình Index
├── templates/
│   └── index.html                 # Giao diện web người dùng đầy đủ tính năng
├── uploads/                       # Thư mục lưu trữ file và ảnh upload
├── chroma_db_hf/                  # Thư mục lưu trữ vectorstore ChromaDB
├── tests/                         # Bộ kiểm thử tự động toàn diện (27 unit tests)
│   ├── test_context_manager.py
│   ├── test_agent_workflow.py
│   ├── test_summarization_integration.py
│   └── test_followups_and_vision.py
├── .env                           # Biến môi trường và khóa bí mật API
├── requirements.txt               # Danh sách thư viện phụ thuộc
└── README.md                      # Tài liệu kỹ thuật chi tiết của dự án
```

---

## 7. Các API Endpoints

### 7.1. Giao diện & Lịch sử
- **`GET /`**: Trả về giao diện web chính (`index.html`).
- **`GET /conversations`**: Lấy danh sách các cuộc trò chuyện gần nhất (gồm `thread_id`, `title`, `created_at`, `updated_at`).
- **`GET /history/{thread_id}`**: Lấy toàn bộ lịch sử tin nhắn đã lưu của một cuộc trò chuyện cụ thể.

### 7.2. Tải lên tài liệu
- **`POST /upload`**:
  - **Form Data:** `file` (File), `thread_id` (str).
  - **Xử lý:** Kiểm tra định dạng (PDF, DOCX, TXT, MD, PY, CSV, JPG, PNG, WEBP). Lưu file vào `uploads/`. Nếu là ảnh thì đăng ký vào RAG ảnh; nếu là tài liệu thì đọc text, OCR (nếu cần), chia chunks và lưu vào ChromaDB.
  - **Phản hồi JSON:** `{"success": true, "message": "Uploaded ... and created X chunks."}`.

### 7.3. Trò chuyện thời gian thực
- **`POST /chat/stream`**:
  - **Body JSON:**
    ```json
    {
      "message": "Nội dung câu hỏi của người dùng",
      "thread_id": "session-id-123",
      "model": "openai/gpt-oss-20b"
    }
    ```
  - **Phản hồi:** Server-Sent Events (`text/event-stream`):
    - `data: {"token": "Xin chào"}`
    - `data: {"suggestions_pending": true}`
    - `data: {"suggestions": ["Gợi ý 1?", "Gợi ý 2?"]}`
    - `data: {"done": true}`

---

## 8. Hướng dẫn cài đặt và khởi chạy

### 8.1. Cài đặt môi trường Python
Khuyến nghị tạo môi trường ảo Python 3.12:
```bash
# Tạo virtual environment
python -m venv venv

# Kích hoạt trên Windows:
venv\Scripts\activate

# Hoặc kích hoạt trên Linux/macOS:
source venv/bin/activate
```

### 8.2. Cài đặt các thư viện phụ thuộc
```bash
pip install -r requirements.txt
```

### 8.3. Cài đặt Tesseract OCR (Tùy chọn, dùng cho PDF scan)
- **Windows:** Tải bộ cài từ [UB Mannheim Tesseract](https://github.com/UB-Mannheim/tesseract/wiki). Đảm bảo tích chọn gói ngôn ngữ **Vietnamese** và **English**. Thêm thư mục cài đặt vào `PATH`.
- **Linux:**
  ```bash
  sudo apt-get update
  sudo apt-get install tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng
  ```

### 8.4. Khởi chạy Server
```bash
python app.py
```
Ứng dụng sẽ hoạt động tại địa chỉ: `http://localhost:8080`.

---

## 9. Cấu hình biến môi trường (`.env`)

Tạo file `.env` tại thư mục gốc với các thông số cấu hình:

```dotenv
# ===========================================================================
# LLM Providers & Models (Groq API)
# ===========================================================================
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-20b
GROQ_VISION_MODEL=qwen/qwen3.8-27b

# ===========================================================================
# Web Search Tool (Tavily Search API)
# ===========================================================================
TAVILY_API_KEY=tvly-your_tavily_api_key_here

# ===========================================================================
# MongoDB Database (Atlas hoặc Local)
# ===========================================================================
MONGODB_URI=mongodb+srv://<username>:<password>@cluster0.mongodb.net/?retryWrites=true&w=majority
MONGODB_DB_NAME=tit_chatbot
MONGODB_CHECKPOINT_DB=tit_chatbot_checkpoints

# ===========================================================================
# Context Optimization & Token Budgeting (context_manager.py)
# ===========================================================================
# Số lượt chat gần nhất giữ trong active context trước khi tóm tắt (mặc định: 4 turns)
MAX_HISTORY_TURNS=4

# Ngân sách token tối đa cho mỗi lượt gọi LLM (mặc định: 6000 tokens)
MAX_CONTEXT_TOKENS=6000

# Token dự kiến dành riêng cho LLM sinh câu trả lời (mặc định: 1200 tokens)
EXPECTED_OUTPUT_TOKENS=1200

# Ngưỡng token tối đa cho kết quả công cụ active (mặc định: 1800 tokens)
MAX_TOOL_RESULT_TOKENS=1800

# ===========================================================================
# Tóm tắt tài liệu Map-Reduce (summarization.py)
# ===========================================================================
SUMMARIZATION_MAX_INPUT_TOKENS=6000

# ===========================================================================
# Cấu hình OCR PDF Scan (rag.py)
# ===========================================================================
PDF_OCR_LANGUAGES=vie+eng
PDF_OCR_DPI=300
PDF_OCR_TRIGGER_CHARS=40
# TESSDATA_PREFIX=C:\Program Files\Tesseract-OCR\tessdata
```

---

## 10. Kiểm thử tự động (Test Suite)

Dự án sở hữu bộ kiểm thử tự động toàn diện với **27 unit tests** bao quát 100% các thành phần và 4 bước tối ưu hóa ngữ cảnh:

```bash
python -m unittest discover -s tests
```

### Kết quả chạy kiểm thử:
```text
Ran 27 tests in 3.544s

OK
```

### Các tệp kiểm thử trong thư mục `tests/`:
1. **`test_context_manager.py` (12 tests):**
   - Kiểm thử hàm ước tính token đa ngôn ngữ (`estimate_tokens`).
   - Kiểm thử ước tính token của tin nhắn (`estimate_message_tokens`) và schema công cụ (`estimate_tool_schemas_tokens`).
   - Kiểm thử gom nhóm lượt hội thoại (`group_messages_into_turns`).
   - Kiểm thử cắt tỉa và tóm tắt hội thoại cũ (`trim_conversation_history`).
   - Kiểm thử loại bỏ kết quả công cụ cũ nhưng bảo vệ công cụ đang chạy (`prune_old_tool_results`).
   - Kiểm thử cô lập ngữ cảnh tóm tắt (`validate_and_isolate_summarization_context`).
   - Kiểm thử giới hạn token và cắt gọn dữ liệu khi vượt ngưỡng (`enforce_token_budget`).
   - Kiểm thử luồng tích hợp hoàn chỉnh (`prepare_context_for_llm`).
2. **`test_agent_workflow.py` (5 tests):**
   - Kiểm thử chuẩn hóa tên model (`normalize_model_name`).
   - Kiểm thử tích hợp Chatbot Node trong LangGraph.
   - Kiểm thử loại bỏ tool cũ qua các turn liên tiếp.
   - Kiểm thử bảo toàn kết quả công cụ active của lượt hiện tại.
   - Kiểm thử phân rã các thành phần token (`calculate_token_breakdown`).
3. **`test_summarization_integration.py` (4 tests):**
   - Kiểm thử quy trình Map-Reduce tài liệu lớn qua nhiều tầng.
   - Kiểm thử tóm tắt tài liệu nhỏ chỉ qua 1 pass.
   - Kiểm thử lan truyền lỗi khi LLM thất bại thay vì trả về tóm tắt chắp vá.
   - Kiểm thử cô lập ngữ cảnh: đảm bảo không có lịch sử chat nào lọt vào Map-Reduce pass.
4. **`test_followups_and_vision.py` (6 tests):**
   - Kiểm thử lọc khối code dài khỏi dữ liệu sinh gợi ý (`_clean_content_for_followup`).
   - Kiểm thử loại bỏ tag đính kèm file/ảnh thô.
   - Kiểm thử áp dụng giới hạn token cho ngữ cảnh gợi ý.
   - Kiểm thử thuật toán gợi ý cục bộ cho tiếng Việt và tiếng Anh.
   - Kiểm thử cắt gọn câu hỏi thị giác máy tính quá dài trong `analyze_image`.

---

## 11. Từ điển thuật ngữ

| Thuật ngữ | Định nghĩa trong dự án |
|---|---|
| **Turn (Lượt hội thoại)** | Một lượt trao đổi hoàn chỉnh gồm 1 câu hỏi từ người dùng (`HumanMessage`) và chuỗi phản hồi tương ứng của trợ lý (có thể gồm các bước gọi công cụ và câu trả lời kết luận). |
| **Active Turn** | Lượt hội thoại đang diễn ra: công cụ vừa thực thi xong và Agent đang chuẩn bị tổng hợp câu trả lời cho người dùng. Kết quả công cụ của lượt này luôn được giữ nguyên. |
| **Completed Turn** | Lượt hội thoại trong quá khứ đã có câu trả lời hoàn tất. Các kết quả công cụ thô (như JSON search hay RAG chunk) sẽ bị loại bỏ khỏi context của các lượt tiếp theo. |
| **Token Budget** | Ngân sách token tối đa cho phép cho một request, giúp đảm bảo không bao giờ vượt quá context window hay rate limits (TPM) của nhà cung cấp LLM. |
| **Map-Reduce** | Chiến lược chia để trị xử lý tài liệu dài: chia nhỏ thành các nhóm để tóm tắt song song (Map), sau đó gộp dần các bản tóm tắt trung gian lại (Reduce) cho đến khi ra kết quả tổng thể. |
| **RAG** | Retrieval-Augmented Generation: kỹ thuật truy xuất các đoạn văn bản có độ tương đồng ngữ nghĩa cao nhất từ kho vector để hỗ trợ mô hình trả lời chính xác, tránh hiện tượng ảo giác (hallucination). |
| **SSE** | Server-Sent Events: giao thức truyền thông một chiều từ server về client qua HTTP, giúp đẩy từng token câu trả lời về giao diện người dùng theo thời gian thực. |
| **Checkpointer** | Cơ chế lưu trữ trạng thái của LangGraph vào cơ sở dữ liệu (MongoDB), cho phép tạm dừng, khôi phục và tiếp tục phiên làm việc của Agent một cách bền vững. |
