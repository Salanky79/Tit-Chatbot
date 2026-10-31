# Tit Chatbot — Agentic AI Chatbot with RAG

Tit Chatbot là chatbot AI được xây dựng bằng Python. Ứng dụng hướng tới trải nghiệm trò chuyện có thể sử dụng công cụ (tools), tìm kiếm thông tin trong tài liệu người dùng tải lên (Retrieval-Augmented Generation — RAG), lưu lịch sử hội thoại và stream câu trả lời ra giao diện theo từng phần.


## Mục lục

- [1. Tổng quan](#1-tổng-quan)
- [2. Chức năng chính](#2-chức-năng-chính)
- [3. Công nghệ sử dụng](#3-công-nghệ-sử-dụng)
- [4. Kiến trúc dự án](#4-kiến-trúc-dự-án)
- [5. Luồng xử lý tin nhắn](#5-luồng-xử-lý-tin-nhắn)
- [6. Agent, LangGraph và tools](#6-agent-langgraph-và-tools)
- [7. RAG: hỏi đáp trên tài liệu tải lên](#7-rag-hỏi-đáp-trên-tài-liệu-tải-lên)
- [8. Bộ nhớ và cơ sở dữ liệu](#8-bộ-nhớ-và-cơ-sở-dữ-liệu)
- [9. Streaming: async, await, yield và chunk](#9-streaming-async-await-yield-và-chunk)
- [10. Các API endpoint](#10-các-api-endpoint)
- [11. Cài đặt và chạy ứng dụng](#11-cài-đặt-và-chạy-ứng-dụng)
- [12. Cấu hình biến môi trường](#12-cấu-hình-biến-môi-trường)
- [13. Thư mục và dữ liệu được tạo](#13-thư-mục-và-dữ-liệu-được-tạo)
- [14. Giới hạn hiện tại và việc cần kiểm tra](#14-giới-hạn-hiện-tại-và-việc-cần-kiểm-tra)
- [15. Từ điển thuật ngữ](#15-từ-điển-thuật-ngữ)

---

## 1. Tổng quan

Tit Chatbot là một ứng dụng chatbot có kiến trúc backend/frontend:

- **Frontend:** trang web HTML/Jinja2 và JavaScript để người dùng gửi tin nhắn, xem câu trả lời, xem hội thoại và tải tài liệu lên.
- **Backend:** FastAPI nhận request từ frontend, gọi AI agent, lưu dữ liệu và trả kết quả.
- **AI agent:** LangGraph điều phối LLM và các tools. Tùy yêu cầu, agent có thể trả lời trực tiếp hoặc gọi công cụ.
- **RAG:** tài liệu được đọc, chia thành các đoạn nhỏ (*chunks*), biến thành embedding và lưu trong ChromaDB để tìm các đoạn liên quan khi đặt câu hỏi.
- **Database:** SQLite lưu hội thoại, tin nhắn và các ghi nhớ dài hạn.
- **Streaming:** câu trả lời được gửi ra frontend từng phần thông qua Server-Sent Events (SSE), thay vì đợi toàn bộ câu trả lời mới hiển thị.

### Mục tiêu thiết kế

1. Trò chuyện với LLM qua giao diện web.
2. Hỗ trợ agent gọi tools để thực hiện các tác vụ ngoài việc sinh văn bản trực tiếp.
3. Hỏi đáp dựa trên nội dung tài liệu đã tải lên.
4. Lưu lịch sử hội thoại để người dùng xem lại.
5. Lưu các ghi nhớ tách biệt theo hội thoại/thread.
6. Hiển thị câu trả lời theo thời gian thực nhờ streaming.

## 2. Chức năng chính

### 2.1. Chat với AI

Người dùng nhập tin nhắn trên giao diện. Frontend gửi nội dung và `thread_id` tới backend. Backend gọi agent, nhận các phần nội dung AI tạo ra và stream chúng về frontend.

### 2.2. Agent sử dụng tools

Agent có thể được cung cấp các công cụ như:

- **Calculator:** tính các biểu thức toán học được hỗ trợ.
- **Search uploaded documents:** truy xuất nội dung liên quan từ tài liệu đã tải lên bằng RAG.
- **Remember:** lưu một thông tin vào bộ nhớ dài hạn.
- **Recall memory:** lấy các ghi nhớ đã lưu của hội thoại.
- **Web search:** tìm kiếm thông tin trên web thông qua Tavily, nếu API key và cấu hình hợp lệ.

LLM quyết định khi nào cần gọi tool dựa trên ngữ cảnh và định nghĩa tool. LangGraph điều phối quá trình gọi tool và đưa kết quả trở lại cho LLM để tiếp tục tạo câu trả lời.

### 2.3. Tải tài liệu lên

Các định dạng được endpoint upload cho phép theo đoạn code hiện tại:

- PDF: `.pdf`
- Word: `.docx`
- Text: `.txt`
- Markdown: `.md`
- Python: `.py`
- CSV: `.csv`

Sau khi nhận file, backend lưu file vào thư mục `uploads/`, đọc nội dung, chia thành chunks, tạo embedding và thêm dữ liệu vào ChromaDB cùng metadata như `thread_id` và tên file. Các định dạng được chấp nhận còn phụ thuộc vào thư viện đọc file đã được cài đặt.

### 2.4. Truy xuất tài liệu bằng RAG

Khi agent cần hỏi đáp trên tài liệu, tool RAG thực hiện tìm kiếm tương đồng trong ChromaDB và ưu tiên lấy các chunk có metadata `thread_id` trùng với hội thoại hiện tại. Nhờ vậy, trong cấu hình hiện tại, việc truy xuất được giới hạn theo thread thay vì mặc định lấy mọi tài liệu trong collection.

### 2.5. Lưu lịch sử và bộ nhớ

SQLite được sử dụng để lưu:

- Danh sách hội thoại và tiêu đề.
- Tin nhắn của người dùng và assistant.
- Các ghi nhớ dài hạn gắn với `thread_id`.

Ngoài database của ứng dụng, LangGraph có thể dùng SQLite checkpoint để lưu trạng thái thực thi của graph theo `thread_id`. Đây là một cơ chế khác với việc lưu lịch sử chat và cũng khác với kho vector RAG.

### 2.6. Streaming câu trả lời

Backend gửi các sự kiện SSE có dạng gần như:

```text
data: {"token": "Xin "}

data: {"token": "chào bạn"}

data: {"done": true}

```

Frontend có thể nhận và hiển thị từng phần câu trả lời khi chúng đến. Backend đồng thời nối các phần văn bản lại để lưu câu trả lời hoàn chỉnh vào database sau khi stream kết thúc.

## 3. Công nghệ sử dụng

| Thành phần | Công nghệ / thư viện | Vai trò |
|---|---|---|
| Ngôn ngữ | Python | Logic backend và agent |
| Web backend | FastAPI | Xây dựng API và xử lý request |
| ASGI server | Uvicorn | Chạy ứng dụng FastAPI |
| Template | Jinja2 | Render trang HTML |
| Agent orchestration | LangGraph | Điều phối các node và vòng lặp gọi tools |
| LLM integration | LangChain chat model integration | Kết nối tới mô hình ngôn ngữ |
| Tools | LangChain tools / ToolNode | Đóng gói và thực thi công cụ |
| RAG orchestration | LangChain | Chia văn bản, tạo documents và truy xuất |
| Vector store | ChromaDB | Lưu vector embedding và tìm kiếm tương đồng |
| Embeddings | Hugging Face Sentence Transformers, nếu dùng cấu hình local hiện tại | Biến văn bản thành vector |
| Database | SQLite + SQLAlchemy | Lưu hội thoại, messages và memory |
| Web search | Tavily | Tìm kiếm web qua tool |
| Streaming | Server-Sent Events (SSE) | Gửi dữ liệu từ backend tới frontend theo từng phần |
| Cấu hình | python-dotenv | Đọc biến môi trường từ `.env` |

> Provider LLM sinh câu trả lời và provider embedding là hai thành phần độc lập. Ví dụ, chatbot có thể dùng Groq cho chat LLM nhưng dùng một model Sentence Transformers chạy local để tạo embedding.

## 4. Kiến trúc dự án

Cấu trúc logic của dự án:

```text
Tit/
├── app.py                 # FastAPI: routes, upload, chat streaming
├── agent.py               # LLM + LangGraph workflow + checkpointing
├── tools.py               # Calculator, RAG, memory và web-search tools
├── rag.py                 # Đọc tài liệu, chia chunks, embeddings, ChromaDB
├── database.py            # SQLite/SQLAlchemy: conversations, messages, memory
├── templates/
│   └── index.html         # Giao diện HTML/Jinja2 và JavaScript phía client
├── uploads/               # Các file người dùng tải lên
├── data/                  # SQLite database và LangGraph checkpoint database
├── chroma_db_hf/          # Ví dụ thư mục lưu vectorstore với embedding local
├── .env                   # API keys và model config; không commit lên Git
└── README.md
```

Tên thư mục database/vectorstore có thể khác tùy phiên bản code đang chạy. Chỉ giữ trong README những đường dẫn đang thực sự được dùng trong code hiện tại.

### Vai trò từng file

#### `app.py` — Web backend

- Tạo ứng dụng FastAPI.
- Render trang chủ từ `templates/index.html`.
- Cung cấp endpoint lấy danh sách hội thoại và lịch sử.
- Nhận file tải lên và gọi hàm thêm tài liệu vào RAG.
- Nhận tin nhắn, gọi agent và stream nội dung bằng SSE.
- Lưu message người dùng và câu trả lời assistant vào database.

#### `agent.py` — Bộ điều phối AI

- Khởi tạo chat model.
- Gắn các tools vào LLM bằng `bind_tools(...)`.
- Tạo graph với `StateGraph` và trạng thái messages.
- Dùng `ToolNode` để chạy tool calls.
- Dùng điều kiện như `tools_condition` để quyết định AI trả lời tiếp hay cần gọi tool.
- Compile graph và có thể cấu hình SQLite checkpointer để lưu trạng thái theo `thread_id`.

#### `tools.py` — Các công cụ agent có thể gọi

- Định nghĩa calculator.
- Gọi hàm truy xuất tài liệu trong `rag.py`.
- Gọi hàm lưu và tìm memory trong `database.py`.
- Khởi tạo web-search tool thông qua Tavily.
- Đăng ký các tools trong một danh sách để agent sử dụng.

#### `rag.py` — Xử lý tài liệu và truy xuất

- Đọc nội dung từ file PDF, DOCX, TXT, Markdown, Python và CSV.
- Chia văn bản thành chunks bằng `RecursiveCharacterTextSplitter`.
- Tạo `Document` objects có nội dung và metadata.
- Thêm documents vào ChromaDB.
- Tìm các chunk liên quan đến truy vấn và lọc theo `thread_id`.

#### `database.py` — Dữ liệu ứng dụng

Các bảng đã được mô tả trong code gồm:

- `conversations`: thông tin hội thoại, `thread_id`, tiêu đề và thời gian.
- `chat_messages`: nội dung từng tin nhắn, vai trò và thời gian.
- `long_term_memory`: các ghi nhớ được lưu cho một thread.

Các hàm hỗ trợ gồm khởi tạo bảng, tạo/cập nhật hội thoại, liệt kê hội thoại, lưu message, đọc lịch sử, lưu memory và tìm memory.

#### `templates/index.html` — Frontend

- Hiển thị giao diện chat.
- Thu thập nội dung người dùng nhập.
- Gửi request tới backend.
- Nhận dữ liệu streaming và nối các đoạn text hiển thị lên màn hình.
- Gửi file tải lên và hiển thị kết quả trả về từ API.

## 5. Luồng xử lý tin nhắn

Luồng cơ bản khi người dùng gửi một tin nhắn:

```text
Người dùng
   |
   v
Frontend (index.html + JavaScript)
   |  POST /chat/stream
   |  message + thread_id + model
   v
FastAPI (app.py)
   |  kiểm tra dữ liệu request
   |  tạo/cập nhật conversation và lưu user message
   |  lấy agent theo model
   v
LangGraph agent (agent.py)
   |
   +---- LLM trả lời trực tiếp ---------------------+
   |                                                |
   +---- LLM yêu cầu gọi tool                       |
            |                                       |
            v                                       |
        ToolNode / tools.py                         |
            |                                       |
            +---- calculator                        |
            +---- RAG / ChromaDB                    |
            +---- memory / SQLite                   |
            +---- web search / Tavily               |
            |                                       |
            +---- kết quả quay lại LLM -------------+
                                                    |
                                                    v
                                        AI tạo câu trả lời
                                                    |
                                                    v
                              app.py stream từng chunk qua SSE
                                                    |
                                                    v
                                    Frontend hiển thị dần
                                                    |
                                                    v
                                  Lưu câu trả lời assistant
```

### `thread_id` dùng để làm gì?

`thread_id` là định danh của một hội thoại. Nó giúp ứng dụng biết message thuộc cuộc trò chuyện nào, và được dùng trong một số cơ chế lưu trạng thái/truy xuất dữ liệu.

- Database dùng `thread_id` để gắn conversation, messages và memory với một hội thoại.
- LangGraph checkpointer dùng `thread_id` để xác định trạng thái graph tương ứng.
- RAG dùng `thread_id` trong metadata filter để giới hạn các tài liệu được truy xuất.

Trong code upload, `uuid.uuid4()` được dùng để tạo `file_id` duy nhất cho tên file. Đây **không đồng nghĩa** với việc tạo `thread_id` của cuộc hội thoại. `thread_id` thường được frontend tạo hoặc lấy từ hội thoại hiện tại rồi gửi lên backend; hãy xác nhận logic cụ thể trong `index.html`.

## 6. Agent, LangGraph và tools

### 6.1. LLM không tự động chạy mọi tool

LLM có thể phát ra một yêu cầu gọi tool. Phần điều phối của LangGraph nhận yêu cầu đó, chuyển tới node chạy tool, rồi đưa kết quả tool về lại LLM. LLM có thể tiếp tục xử lý kết quả và tạo câu trả lời cuối cùng.

Ví dụ hỏi “2 + 2 bằng mấy?”:

1. Người dùng gửi câu hỏi.
2. LLM xác định calculator phù hợp và tạo tool call.
3. `ToolNode` thực thi calculator.
4. Calculator trả về `4`.
5. Kết quả được đưa trở lại LLM.
6. LLM tạo câu trả lời như “Kết quả là 4.”
7. Backend stream câu trả lời phù hợp ra frontend.

### 6.2. Các thành phần LangGraph thường gặp

- **State:** dữ liệu hiện tại của workflow. Với `MessagesState`, trọng tâm là danh sách messages.
- **Node:** một bước xử lý trong graph, ví dụ node chatbot hoặc node tools.
- **Edge:** xác định bước nào được thực hiện tiếp theo.
- **Conditional edge:** chọn đường đi dựa trên kết quả hiện tại, ví dụ có tool call hay không.
- **ToolNode:** thực thi các tools được LLM yêu cầu.
- **Checkpointer:** lưu checkpoint trạng thái graph để có thể tiếp tục theo cùng thread.
- **`bind_tools(tools)`:** cung cấp mô tả và schema của tools cho model; thao tác này không có nghĩa model đã thực thi tool.

Ví dụ mô hình workflow:

```text
START -> chatbot
            |
            +-- cần gọi tool --> tools --> chatbot
            |
            +-- không cần tool --> END
```

Tên node, edge và điều kiện thực tế phụ thuộc vào code `agent.py`.

### 6.3. Phân biệt checkpoint, chat history, memory và RAG

Đây là bốn khái niệm khác nhau:

| Thành phần | Lưu gì? | Dùng để làm gì? |
|---|---|---|
| Chat history (`chat_messages`) | Các tin nhắn đã lưu | Hiển thị lại lịch sử hội thoại |
| Long-term memory (`long_term_memory`) | Những thông tin được tool lưu có chủ đích | Ghi nhớ thông tin qua các lượt chat trong phạm vi được thiết kế |
| LangGraph checkpoint | Trạng thái thực thi graph | Khôi phục/tiếp tục trạng thái theo thread |
| ChromaDB / RAG | Embeddings và nội dung chunks từ tài liệu | Tìm các đoạn tài liệu liên quan tới câu hỏi |

Checkpointer không tự động đồng nghĩa với việc hệ thống đã có chức năng tóm tắt lịch sử hội thoại. Muốn có summarization cần xây dựng thêm logic tương ứng.

## 7. RAG: hỏi đáp trên tài liệu tải lên

### 7.1. RAG hoạt động như thế nào?

RAG (Retrieval-Augmented Generation) kết hợp tìm kiếm thông tin với sinh văn bản:

1. **Upload:** người dùng tải tài liệu lên.
2. **Read:** backend đọc nội dung từ file.
3. **Chunking:** chia nội dung thành đoạn nhỏ hơn.
4. **Embedding:** chuyển mỗi đoạn thành vector số biểu diễn ngữ nghĩa.
5. **Indexing:** lưu vector, nội dung và metadata trong ChromaDB.
6. **Query:** người dùng đặt câu hỏi.
7. **Retrieval:** tạo embedding cho câu hỏi và tìm các chunk gần về ngữ nghĩa.
8. **Context:** tool đưa nội dung tìm được cho LLM.
9. **Generation:** LLM sử dụng nội dung đó để trả lời.

### 7.2. Chunking

Code đã trao đổi dùng `RecursiveCharacterTextSplitter` với các tham số ví dụ:

```python
splitter = RecursiveCharacterTextSplitter(
    chunk_size=900,
    chunk_overlap=150
)
```

- `chunk_size=900`: mục tiêu kích thước mỗi chunk theo đơn vị mà splitter sử dụng (mặc định thường là ký tự).
- `chunk_overlap=150`: hai chunk liên tiếp có thể lặp lại khoảng 150 ký tự để giảm nguy cơ mất ngữ cảnh ở ranh giới đoạn.

Kích thước và overlap là cấu hình có thể điều chỉnh, không phải giá trị tối ưu cho mọi tài liệu.

### 7.3. Embeddings và ChromaDB

Cấu hình embedding local đã được đề cập:

```python
from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    model_kwargs={"device": "cpu"}
)
```

Model này chạy cục bộ trên CPU theo cấu hình trên. Lần đầu sử dụng, model có thể cần tải về cache của Hugging Face; những lần sau thư viện thường sử dụng bản cache thay vì tải lại, miễn là cache còn và môi trường cho phép truy cập.

Một cấu hình Chroma tương ứng đã được trao đổi:

```python
vectorstore = Chroma(
    collection_name="agentic_chatbot_docs_hf",
    embedding_function=embeddings,
    persist_directory="chroma_db_hf"
)
```

Tên collection và đường dẫn trên chỉ đúng nếu code hiện tại đang sử dụng chính xác cấu hình đó.

**Quan trọng:** chuyển sang embedding model khác thường cần tạo lại embeddings cho tài liệu trong collection mới. Vì vậy, nếu thay từ Gemini embeddings sang Hugging Face embeddings, cần index lại hoặc upload tài liệu lại vào collection mới. Không nên giả định vector được tạo bởi hai model embedding khác nhau có thể dùng thay thế trực tiếp cho nhau.

### 7.4. Phân vùng tài liệu theo thread

Mỗi chunk được lưu metadata, ví dụ:

```python
metadata = {
    "thread_id": thread_id,
    "source": Path(file_path).name
}
```

Khi truy xuất:

```python
docs = vectorstore.similarity_search(
    query,
    k=4,
    filter={"thread_id": thread_id}
)
```

- `k=4`: yêu cầu tối đa bốn chunk phù hợp (nếu có).
- `filter`: giới hạn kết quả theo `thread_id`.
- `source`: giúp biết chunk lấy từ file nào.

Collection Chroma có thể chứa documents của nhiều thread; metadata filter tạo ranh giới truy xuất logic. Tuy nhiên, metadata filter không thay thế việc kiểm soát quyền truy cập ở backend. Ứng dụng nhiều người dùng cần xác thực danh tính và kiểm tra quyền sở hữu thread/tài liệu ở phía server.

## 8. Bộ nhớ và cơ sở dữ liệu

### 8.1. SQLite và SQLAlchemy

SQLite lưu dữ liệu trong file database; SQLAlchemy cung cấp lớp làm việc với các bảng và records bằng Python.

Cấu trúc thường gồm:

- `engine`: cấu hình kết nối đến database.
- `SessionLocal`: bộ tạo database session.
- `db`: một session cụ thể dùng cho thao tác đọc/ghi.
- `commit()`: xác nhận thay đổi để lưu.
- `close()`: đóng session và giải phóng tài nguyên liên quan.

### 8.2. Ba bảng chính

#### `conversations`

Lưu thông tin hội thoại như `thread_id`, tiêu đề, thời điểm tạo và cập nhật.

#### `chat_messages`

Lưu tin nhắn theo `thread_id`, với các trường như `role`, `content`, `created_at`. Vai trò thường là `user` hoặc `assistant`.

Ví dụ:

| role | content |
|---|---|
| `user` | `2 + 2 bằng mấy?` |
| `assistant` | `Kết quả là 4.` |

`assistant` chỉ là nhãn xác định bên tạo nội dung, không phải một biến chứa mô hình AI.

#### `long_term_memory`

Lưu các ghi nhớ do tool `remember_this` ghi vào. Theo code đã trao đổi, memory được gắn với `thread_id`, nên phạm vi ghi nhớ là từng thread chứ không tự động chia sẻ giữa tất cả các cuộc trò chuyện.

Một điểm cần lưu ý: hàm `search_memory(thread_id, query)` trong phiên bản code đã thảo luận lọc theo thread nhưng chưa sử dụng `query` để tìm kiếm ngữ nghĩa/nội dung thực sự. Nếu điều này chưa thay đổi, chức năng recall hiện tại không phải semantic memory search.

## 9. Streaming: async, await, yield và chunk

### 9.1. `async` và `await`

Ví dụ trong FastAPI:

```python
async def chat_stream(request: Request):
    data = await request.json()
```

- `async def`: khai báo một hàm bất đồng bộ (*coroutine function*).
- `await`: chờ một thao tác bất đồng bộ có thể chờ được, ở đây là đọc và phân tích JSON request.
- `async` không tự chờ tất cả thao tác; `await` xác định cụ thể chỗ cần chờ.
- Trong lúc chờ I/O bất đồng bộ, event loop có thể cho tác vụ khác chạy.

### 9.2. `yield`

Trong generator:

```python
def event_generator():
    yield "part 1"
    yield "part 2"
```

`yield` đưa ra một giá trị và tạm dừng generator tại đó. Khi bên gọi yêu cầu phần tiếp theo, generator tiếp tục chạy. Điều này phù hợp với việc stream nhiều phần dữ liệu.

`return` thì khác: trả kết quả và kết thúc hàm (trong generator, `return` kết thúc quá trình sinh các phần tử).

### 9.3. `chunk` là gì?

`chunk` là một phần dữ liệu được tạo ra trong quá trình streaming. Mỗi chunk có thể chứa một phần text, metadata, hoặc thông tin liên quan đến tool call; tùy đối tượng và provider.

### 9.4. `should_stream_chunk()`

Hàm này kiểm tra một chunk có vượt qua các điều kiện cho phép stream hay không. Trong đoạn code đã trao đổi, hàm:

1. Đọc tên node từ `metadata`.
2. Bỏ qua chunk nếu tên node có chứa `tool`.
3. Bỏ qua `ToolMessage`.
4. Chỉ chấp nhận `AIMessage` hoặc `AIMessageChunk`.
5. Bỏ qua message chứa `tool_calls` hoặc `invalid_tool_calls`.
6. Trả về `True` nếu chunk vượt qua các điều kiện trên.

`True` chỉ có nghĩa là chunk vượt qua bộ lọc này; nó không đảm bảo mọi trường hợp đều là văn bản thuần. Cần kiểm tra hành vi thực tế với phiên bản LangChain/provider đang cài.

### 9.5. `extract_text_from_chunk()`

Hàm này lấy phần text từ `chunk.content`:

- Nếu `content` rỗng: trả về chuỗi rỗng.
- Nếu `content` là chuỗi (`str`): trả về trực tiếp.
- Nếu `content` là danh sách (`list`): duyệt từng phần tử và gom các phần text nhận diện được.
- Nếu không xử lý được kiểu nội dung: trả về chuỗi rỗng.

### 9.6. `getattr()` và `.get()`

`getattr()` là hàm built-in của Python, không cần import:

```python
content = getattr(chunk, "content", "")
```

Nó lấy thuộc tính `content` từ object `chunk`; nếu không có thuộc tính đó thì dùng giá trị mặc định `""`.

Còn `.get()` thường dùng với dictionary:

```python
message = data.get("message", "")
```

Nó lấy value theo key `message`; nếu key không tồn tại thì trả về `""`.

### 9.7. Luồng streaming trong `app.py`

Logic khái quát:

```python
for chunk, metadata in agent.stream(
    inputs,
    config=config,
    stream_mode="messages"
):
    if not should_stream_chunk(chunk, metadata):
        continue

    token = extract_text_from_chunk(chunk)

    if token:
        final_answer += token
        yield sse_data({"token": token})
```

- `agent.stream(...)`: lấy các phần output từ graph.
- `should_stream_chunk(...)`: quyết định bỏ qua hay xử lý tiếp chunk.
- `extract_text_from_chunk(...)`: lấy chữ.
- `final_answer += token`: nối các đoạn text thành câu trả lời đầy đủ.
- `yield sse_data(...)`: gửi từng đoạn qua stream response.

Trong code hiện tại đã thảo luận, `chat_stream()` là hàm async nhưng `event_generator()` là generator đồng bộ và gọi `agent.stream()` đồng bộ. Với ứng dụng có tải đồng thời lớn, cần đánh giá liệu việc stream đồng bộ có chặn event loop hay không; có thể cần thiết kế streaming bất đồng bộ hoặc đưa tác vụ đồng bộ ra thread phù hợp.

## 10. Các API endpoint

Các route dưới đây được mô tả theo `app.py` đã trao đổi. Kiểm tra code mới nhất nếu route hoặc request schema đã thay đổi.

| Method | Endpoint | Chức năng |
|---|---|---|
| `GET` | `/` | Render trang giao diện chat `index.html` |
| `GET` | `/conversations` | Trả danh sách hội thoại |
| `GET` | `/history/{thread_id}` | Trả lịch sử message của một thread |
| `POST` | `/upload` | Nhận file và thêm nội dung vào RAG |
| `POST` | `/chat/stream` | Nhận tin nhắn và stream câu trả lời bằng SSE |

### `POST /chat/stream`

Request JSON dự kiến có cấu trúc:

```json
{
  "message": "Nội dung câu hỏi",
  "thread_id": "id-cua-hoi-thoai",
  "model": "openai/gpt-oss-20b"
}
```

Tên model trong ví dụ là giá trị từng được dùng trong cấu hình, **không bảo đảm model vẫn khả dụng hoặc được provider hiện tại hỗ trợ**.

Backend kiểm tra message rỗng, lưu message người dùng, thiết lập thread, gọi agent và trả về `StreamingResponse` với media type `text/event-stream`.

### `POST /upload`

Endpoint nhận file và `thread_id` qua form data. Backend kiểm tra phần mở rộng, lưu file vào `uploads/`, gọi `add_document_to_rag(...)` rồi trả thông báo upload và số chunks đã tạo nếu thành công.

## 11. Cài đặt và chạy ứng dụng

Các lệnh dưới đây phù hợp với môi trường Python/Conda trên Windows đã được đề cập. Tên môi trường Conda và danh sách thư viện thực tế có thể khác.

### 11.1. Chuẩn bị môi trường

```bash
conda create -n tit python=3.12 -y
conda activate tit
```

Nếu đã có môi trường `tit`, chỉ cần kích hoạt:

```bash
conda activate tit
```

### 11.2. Cài thư viện

Nếu repository có `requirements.txt`:

```bash
python -m pip install -r requirements.txt
```

Nếu chưa có file này, hãy tạo một file dependency dựa trên các import thực sự trong project. Các thư viện đã xuất hiện trong code được trao đổi gồm FastAPI, Uvicorn, Jinja2, LangGraph, LangChain Core, gói tích hợp provider LLM, Chroma, Hugging Face embeddings, Sentence Transformers, SQLAlchemy, python-dotenv, certifi, pypdf, docx2txt và Tavily integration.

Không nên cài tất cả package theo tên đoán nếu project đã có dependency file; hãy ưu tiên file dependency hiện tại để tránh xung đột phiên bản.

### 11.3. Khai báo biến môi trường

Tạo file `.env` ở thư mục gốc, điền các API key theo provider bạn thực sự sử dụng. Xem [Cấu hình biến môi trường](#12-cấu-hình-biến-môi-trường).

### 11.4. Chạy server

```bash
python app.py
```

Theo code đã trao đổi, Uvicorn chạy ứng dụng ở cổng `8080`. Mở trình duyệt tại:

```text
http://127.0.0.1:8080
```

Địa chỉ `127.0.0.1` là loopback — truy cập server từ chính máy đang chạy ứng dụng. `0.0.0.0` trong cấu hình Uvicorn có nghĩa server lắng nghe trên các network interface của máy; không nên công khai ứng dụng ra Internet nếu chưa có xác thực, kiểm soát quyền truy cập và cấu hình bảo mật phù hợp.

## 12. Cấu hình biến môi trường

Ví dụ khung `.env` (điền giá trị thật trong máy của bạn; không commit secret lên Git):

```dotenv
# Chat LLM — chỉ dùng nếu agent.py đã được cấu hình để sử dụng Groq
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-20b

# Web search — chỉ cần nếu bật Tavily tool
TAVILY_API_KEY=your_tavily_api_key
```

Các lưu ý:

- Không ghi API key thật trực tiếp vào source code hoặc README.
- Thêm `.env`, database có dữ liệu nhạy cảm và file upload riêng tư vào `.gitignore` khi phù hợp.
- Model ID phải khớp với provider và phải còn được provider hỗ trợ.
- Nếu dùng Groq, `agent.py` phải dùng integration của Groq, chẳng hạn `ChatGroq`, chứ không được khởi tạo Groq model ID bằng class dành cho Google Gemini.
- Embeddings local bằng Hugging Face thường không cần API key của Google, nhưng có thể cần internet ở lần tải model đầu tiên.
- Nếu code vẫn dùng `GoogleGenerativeAIEmbeddings`, cấu hình phải có credential phù hợp với Google và quyền sử dụng model embedding đó.

## 13. Thư mục và dữ liệu được tạo

| Đường dẫn | Vai trò |
|---|---|
| `uploads/` | File đã upload |
| `data/chatbot_memory.db` | SQLite database của ứng dụng, theo cấu hình từng được trao đổi |
| `data/langgraph_checkpoints.sqlite` | SQLite database cho LangGraph checkpoint, theo cấu hình từng được trao đổi |
| `chroma_db/` | Vectorstore cũ hoặc collection dùng embedding ban đầu nếu vẫn còn được cấu hình |
| `chroma_db_hf/` | Vectorstore ví dụ dùng embedding Hugging Face local |
| Hugging Face cache | Cache model embedding đã tải về, thường ở thư mục cache của user hệ điều hành |

Các thư mục cụ thể được tạo tùy theo đường dẫn khai báo trong `rag.py`, `database.py` và `agent.py`. Không xóa thư mục database hoặc vectorstore nếu cần giữ dữ liệu.

## 14. Giới hạn hiện tại và việc cần kiểm tra



## 15. Từ điển thuật ngữ

| Thuật ngữ | Giải thích ngắn |
|---|---|
| API | Giao diện để frontend và backend trao đổi dữ liệu |
| FastAPI | Framework Python xây dựng API/web backend |
| Endpoint / route | Địa chỉ và phương thức API mà ứng dụng cung cấp |
| LLM | Mô hình ngôn ngữ lớn tạo hoặc xử lý văn bản |
| Agent | Thành phần kết hợp LLM, trạng thái và khả năng gọi tools để xử lý nhiệm vụ |
| Tool | Hàm/công cụ mà agent có thể yêu cầu thực thi |
| LangGraph | Framework điều phối workflow dạng graph có trạng thái |
| State | Dữ liệu hiện hành của workflow |
| Node | Một bước xử lý trong graph |
| Tool call | Yêu cầu của model muốn gọi một công cụ cụ thể |
| RAG | Truy xuất nội dung liên quan rồi đưa vào context để LLM trả lời |
| Chunk | Một đoạn nhỏ được cắt ra từ văn bản hoặc output streaming |
| Embedding | Vector số đại diện cho nội dung, dùng cho tìm kiếm tương đồng |
| Vector store | Hệ thống lưu vector và metadata để truy xuất tương đồng |
| ChromaDB | Vector database được sử dụng trong project |
| Metadata | Thông tin phụ gắn với document/chunk, ví dụ nguồn file và thread ID |
| SQLite | Database dạng file, phù hợp cho ứng dụng local và quy mô nhỏ |
| SQLAlchemy | Thư viện Python làm việc với database qua ORM/Core |
| Checkpoint | Trạng thái workflow được lưu để tiếp tục hoặc khôi phục |
| `async` | Khai báo hàm coroutine bất đồng bộ trong Python |
| `await` | Chờ kết quả của một thao tác awaitable trong hàm async |
| `yield` | Sinh/trả ra từng giá trị từ generator mà chưa kết thúc ngay hàm |
| SSE | Server-Sent Events; cơ chế server gửi sự kiện streaming tới client qua HTTP |
| `getattr()` | Hàm built-in lấy thuộc tính từ object, có thể chỉ định giá trị mặc định |
| `dict.get()` | Lấy giá trị của dictionary theo key, có thể chỉ định mặc định |
| `thread_id` | Định danh của một hội thoại/luồng trạng thái trong ứng dụng |

---

## Tổng kết

Tit Chatbot kết hợp giao diện web, FastAPI backend, LangGraph agent, tools, RAG trên tài liệu, SQLite persistence và SSE streaming. Các thành phần đảm nhiệm vai trò riêng:

- **FastAPI** tiếp nhận request và kết nối frontend với agent.
- **LangGraph** điều phối quá trình AI và tool calls.
- **Tools** cung cấp khả năng tính toán, truy xuất tài liệu, lưu/đọc memory và tìm kiếm web.
- **RAG + ChromaDB + embeddings** hỗ trợ hỏi đáp dựa trên các file đã tải lên.
- **SQLite** lưu lịch sử, conversation và memory.
- **SSE streaming** giúp câu trả lời hiển thị dần trên giao diện.

Trước khi đưa vào vận hành thực tế, cần xác nhận cấu hình provider/model, quyền truy cập theo thread, xử lý lỗi và các vấn đề bảo mật/concurrency được liệt kê ở trên.
