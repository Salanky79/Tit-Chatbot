from pathlib import Path
from typing import List
from dotenv import load_dotenv
import os
import certifi

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from pypdf import PdfReader
import docx2txt


Path("uploads").mkdir(exist_ok=True)
Path("chroma_db").mkdir(exist_ok=True)


# Embeddings model chạy local trên CPU
embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    model_kwargs={"device": "cpu"}
)

# Vector database
vectorstore = Chroma(
    collection_name="agentic_chatbot_docs_hf",
    embedding_function=embeddings,
    persist_directory="chroma_db_hf"
)


# ---------------------------------------------------------------------------
# File readers – trả về List[Document] với metadata đầy đủ (source, page)
# ---------------------------------------------------------------------------

def _read_pdf(file_path: str, filename: str, thread_id: str) -> List[Document]:
    """Đọc PDF, trả về 1 Document mỗi trang với metadata page số."""
    reader = PdfReader(file_path)
    docs = []
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if not text:
            continue  # bỏ qua trang trắng
        docs.append(Document(
            page_content=text,
            metadata={
                "thread_id": thread_id,
                "source": filename,
                "page": page_num,
                "total_pages": len(reader.pages),
                "file_type": "pdf",
            }
        ))
    return docs


def _read_docx(file_path: str, filename: str, thread_id: str) -> List[Document]:
    """Đọc DOCX – docx2txt không có thông tin trang nên dùng page=1."""
    text = docx2txt.process(file_path) or ""
    if not text.strip():
        return []
    return [Document(
        page_content=text,
        metadata={
            "thread_id": thread_id,
            "source": filename,
            "page": 1,
            "file_type": "docx",
        }
    )]


def _read_text(file_path: str, filename: str, thread_id: str) -> List[Document]:
    """Đọc TXT / MD / PY / CSV – page=1 (file text thuần)."""
    text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        return []
    return [Document(
        page_content=text,
        metadata={
            "thread_id": thread_id,
            "source": filename,
            "page": 1,
            "file_type": Path(file_path).suffix.lstrip("."),
        }
    )]


def _load_documents(file_path: str, thread_id: str) -> List[Document]:
    """Dispatcher – chọn reader đúng theo đuôi file."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    filename = path.name

    if suffix == ".pdf":
        return _read_pdf(file_path, filename, thread_id)
    if suffix == ".docx":
        return _read_docx(file_path, filename, thread_id)
    if suffix in {".txt", ".md", ".py", ".csv"}:
        return _read_text(file_path, filename, thread_id)

    raise ValueError("Unsupported file type. Upload PDF, DOCX, TXT, MD, PY, or CSV.")


# ---------------------------------------------------------------------------
# Chunking – giữ nguyên metadata (page, source, …) trên từng chunk
# ---------------------------------------------------------------------------

def _split_documents(docs: List[Document]) -> List[Document]:
    """Chia mỗi Document thành nhiều chunk nhỏ hơn.

    Metadata (thread_id, source, page, file_type) được sao chép nguyên vẹn
    sang từng chunk. Thêm ``chunk_index`` để phân biệt các chunk cùng trang.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=900,
        chunk_overlap=150,
        add_start_index=True,  # thêm 'start_index' vào metadata
    )

    chunks: List[Document] = []
    for doc in docs:
        page_chunks = splitter.split_documents([doc])
        for idx, chunk in enumerate(page_chunks):
            chunk.metadata["chunk_index"] = idx
        chunks.extend(page_chunks)

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_document_to_rag(file_path: str, thread_id: str) -> dict:
    """Đọc file, tách thành chunks (kèm metadata trang), lưu vào ChromaDB.

    Args:
        file_path: Đường dẫn file đã upload.
        thread_id: ID luồng chat để lọc kết quả theo session.

    Returns:
        dict với ``filename``, ``chunks``, ``pages`` (số trang đã đọc được).
    """
    raw_docs = _load_documents(file_path, thread_id)

    if not raw_docs:
        raise ValueError("No text could be extracted from this file.")

    chunks = _split_documents(raw_docs)

    if not chunks:
        raise ValueError("File has content but could not be split into chunks.")

    vectorstore.add_documents(chunks)

    pages_found = sorted({doc.metadata.get("page", 1) for doc in raw_docs})

    return {
        "filename": Path(file_path).name,
        "chunks": len(chunks),
        "pages": len(pages_found),
    }


def retrieve_from_rag(query: str, thread_id: str, k: int = 4) -> str:
    """Tìm kiếm các đoạn văn liên quan trong ChromaDB và trả về kết quả.

    Kết quả bao gồm tên file và số trang để người dùng dễ tham chiếu.

    Args:
        query: Câu hỏi hoặc từ khóa tìm kiếm.
        thread_id: ID luồng chat để lọc đúng tài liệu.
        k: Số đoạn văn tối đa trả về (mặc định 4).

    Returns:
        Chuỗi kết quả, mỗi đoạn có nhãn [Source N: file.pdf – Page X].
    """
    docs = vectorstore.similarity_search(
        query,
        k=k,
        filter={"thread_id": thread_id}
    )

    if not docs:
        return "No relevant uploaded document content found."

    results = []
    for i, doc in enumerate(docs, start=1):
        source = doc.metadata.get("source", "uploaded document")
        page = doc.metadata.get("page")
        total = doc.metadata.get("total_pages")

        # Tạo label có số trang nếu có
        if page is not None:
            page_label = f"Page {page}"
            if total:
                page_label += f"/{total}"
            label = f"[Source {i}: {source} – {page_label}]"
        else:
            label = f"[Source {i}: {source}]"

        results.append(f"{label}\n{doc.page_content}")

    return "\n\n".join(results)
