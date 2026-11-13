from pathlib import Path
from typing import Any
from dotenv import load_dotenv
import os
import certifi

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from pypdf import PdfReader
import docx2txt


Path("uploads").mkdir(exist_ok=True)
Path("chroma_db_hf").mkdir(exist_ok=True)


_embeddings = None
_vectorstore = None


def get_embeddings() -> Any:
    """Lazy loader for HuggingFace embeddings model on CPU."""
    global _embeddings
    if _embeddings is None:
        from langchain_huggingface import HuggingFaceEmbeddings
        try:
            _embeddings = HuggingFaceEmbeddings(
                model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                model_kwargs={"device": "cpu", "local_files_only": True}
            )
        except Exception:
            _embeddings = HuggingFaceEmbeddings(
                model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                model_kwargs={"device": "cpu"}
            )
    return _embeddings


def get_vectorstore() -> Any:
    """Lazy loader for Chroma vector database."""
    global _vectorstore
    if _vectorstore is None:
        from langchain_chroma import Chroma
        _vectorstore = Chroma(
            collection_name="agentic_chatbot_docs_hf",
            embedding_function=get_embeddings(),
            persist_directory="chroma_db_hf"
        )
    return _vectorstore


def __getattr__(name: str) -> Any:
    """Module-level attribute fallback for backward compatibility (PEP 562)."""
    if name == "vectorstore":
        return get_vectorstore()
    if name == "embeddings":
        return get_embeddings()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


# ---------------------------------------------------------------------------
# File readers – trả về List[Document] với metadata đầy đủ (source, page)
# ---------------------------------------------------------------------------

def _read_pdf(file_path: str, filename: str, thread_id: str, user_id: str | None = None) -> list[Document]:
    """Extract each PDF page, falling back to OCR for scanned/sparse pages."""
    reader = PdfReader(file_path)
    docs: list[Document] = []
    ocr_pdf = None

    try:
        try:
            min_native_chars = int(os.getenv("PDF_OCR_TRIGGER_CHARS", "40"))
        except ValueError:
            min_native_chars = 40
        try:
            ocr_dpi = int(os.getenv("PDF_OCR_DPI", "300")) # Độ phân giải DPI cho OCR, mặc định 300 DPI. Tăng DPI có thể cải thiện độ chính xác nhận dạng ký tự nhưng cũng làm chậm quá trình OCR và tăng kích thước dữ liệu.
        except ValueError:
            ocr_dpi = 300
        ocr_languages = os.getenv("PDF_OCR_LANGUAGES", "vie+eng")

        for page_num, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            extraction_method = "native"

            # Scanned pages usually have no text layer. Sparse text can also
            # indicate a scanned page with only a selectable header/footer.
            if len(text) < min_native_chars:
                if ocr_pdf is None:
                    try:
                        import pymupdf
                        ocr_pdf = pymupdf.open(file_path)
                    except Exception as exc:
                        raise RuntimeError(
                            "This PDF page needs OCR, but PyMuPDF could not be opened. "
                            "Install the Python dependency 'pymupdf'."
                        ) from exc

                try:
                    ocr_page = ocr_pdf[page_num - 1]
                    # Empty PDF pages have nothing for OCR to recognize. Avoid
                    # requiring Tesseract for pages with no text or visual data.
                    if (
                        not text
                        and not ocr_page.get_images(full=True) # ảnh
                        and not ocr_page.get_drawings() # hình vẽ vector
                    ):
                        continue
                    text_page = ocr_page.get_textpage_ocr( # yêu cầu PyMuPDF thực hiện nhận dạng ký tự trên trang
                        language=ocr_languages,
                        dpi=ocr_dpi,
                        full=True, # scan toàn bộ trang, không chỉ vùng văn bản
                        tessdata=os.getenv("TESSDATA_PREFIX") or None,
                    )
                    ocr_text = ocr_page.get_text("text", textpage=text_page).strip()
                except Exception as exc:
                    raise RuntimeError(
                        f"OCR failed on page {page_num} of '{filename}'. "
                        f"Check that Tesseract and its language data ({ocr_languages}) "
                        "are installed and available. "
                        f"Root cause: {exc}"
                    ) from exc

                if ocr_text and len(ocr_text) > len(text):
                    text = ocr_text
                    extraction_method = "ocr"

            if not text:
                continue  # blank page or no text recognized by OCR

            docs.append(Document(
                page_content=text,
                metadata={
                    "thread_id": thread_id,
                    "user_id": str(user_id) if user_id else "",
                    "source": filename,
                    "page": page_num,
                    "total_pages": len(reader.pages),
                    "file_type": "pdf",
                    "extraction_method": extraction_method,
                }
            ))
    finally:
        if ocr_pdf is not None:
            ocr_pdf.close()

    return docs


def _read_docx(file_path: str, filename: str, thread_id: str, user_id: str | None = None) -> list[Document]:
    """Đọc DOCX – docx2txt không có thông tin trang nên dùng page=1."""
    text = docx2txt.process(file_path) or ""
    if not text.strip():
        return []
    return [Document(
        page_content=text,
        metadata={
            "thread_id": thread_id,
            "user_id": str(user_id) if user_id else "",
            "source": filename,
            "page": 1,
            "file_type": "docx",
        }
    )]


def _read_text(file_path: str, filename: str, thread_id: str, user_id: str | None = None) -> list[Document]:
    """Đọc TXT / MD / PY / CSV – page=1 (file text thuần)."""
    text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        return []
    return [Document(
        page_content=text,
        metadata={
            "thread_id": thread_id,
            "user_id": str(user_id) if user_id else "",
            "source": filename,
            "page": 1,
            "file_type": Path(file_path).suffix.lstrip("."),
        }
    )]


def _load_documents(file_path: str, thread_id: str, user_id: str | None = None) -> list[Document]:
    """Dispatcher – chọn reader đúng theo đuôi file."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    filename = path.name

    if suffix == ".pdf":
        return _read_pdf(file_path, filename, thread_id, user_id=user_id)
    if suffix == ".docx":
        return _read_docx(file_path, filename, thread_id, user_id=user_id)
    if suffix in {".txt", ".md", ".py", ".csv"}:
        return _read_text(file_path, filename, thread_id, user_id=user_id)

    raise ValueError("Unsupported file type. Upload PDF, DOCX, TXT, MD, PY, or CSV.")


# ---------------------------------------------------------------------------
# Chunking – giữ nguyên metadata (page, source, …) trên từng chunk
# ---------------------------------------------------------------------------

def _split_documents(docs: list[Document]) -> list[Document]:
    """Chia mỗi Document thành nhiều chunk nhỏ hơn.

    Metadata (thread_id, source, page, file_type) được sao chép nguyên vẹn
    sang từng chunk. Thêm ``chunk_index`` để phân biệt các chunk cùng trang.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=900,
        chunk_overlap=150,
        add_start_index=True,  # thêm 'start_index' vào metadata
    )

    chunks: list[Document] = []
    for doc in docs:
        page_chunks = splitter.split_documents([doc])
        for idx, chunk in enumerate(page_chunks):
            chunk.metadata["chunk_index"] = idx
        chunks.extend(page_chunks)

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_document_to_rag(file_path: str, thread_id: str, user_id: str | None = None) -> dict:
    """Đọc file, tách thành chunks (kèm metadata trang & user_id), lưu vào ChromaDB.

    Args:
        file_path: Đường dẫn file đã upload.
        thread_id: ID luồng chat để lọc kết quả theo session.
        user_id: ID người dùng sở hữu tài liệu (nếu có) để cách ly dữ liệu.

    Returns:
        dict với ``filename``, ``chunks``, ``pages`` (số trang đã đọc được).
    """
    raw_docs = _load_documents(file_path, thread_id, user_id=user_id)

    if not raw_docs:
        raise ValueError("No text could be extracted from this file.")

    chunks = _split_documents(raw_docs)

    if not chunks:
        raise ValueError("File has content but could not be split into chunks.")

    get_vectorstore().add_documents(chunks)

    pages_found = sorted({doc.metadata.get("page", 1) for doc in raw_docs})

    return {
        "filename": Path(file_path).name,
        "chunks": len(chunks),
        "pages": len(pages_found),
    }


def retrieve_from_rag(query: str, thread_id: str, user_id: str | None = None, k: int = 4) -> str:
    """Tìm kiếm các đoạn văn liên quan trong ChromaDB và trả về kết quả.

    Kết quả bao gồm tên file và số trang để người dùng dễ tham chiếu.

    Args:
        query: Câu hỏi hoặc từ khóa tìm kiếm.
        thread_id: ID luồng chat để lọc đúng tài liệu.
        user_id: ID người dùng để cách ly tài liệu giữa các người dùng.
        k: Số đoạn văn tối đa trả về (mặc định 4).

    Returns:
        Chuỗi kết quả, mỗi đoạn có nhãn [Source N: file.pdf – Page X].
    """
    filter_dict = {"thread_id": thread_id}
    if user_id:
        filter_dict = {"$and": [{"thread_id": thread_id}, {"user_id": str(user_id)}]}

    docs = get_vectorstore().similarity_search(
        query,
        k=k,
        filter=filter_dict
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


def get_document_chunks(source: str, thread_id: str, user_id: str | None = None) -> list[Document]:
    """Return every stored chunk for one uploaded file in one conversation.

    This deliberately reads the Chroma collection directly instead of using
    similarity search, which would omit chunks unrelated to a query.
    """
    source = (source or "").strip()
    thread_id = (thread_id or "").strip()
    if not source:
        raise ValueError("A file name is required to summarize a document.")
    if not thread_id:
        raise ValueError("A conversation ID is required to find the document.")

    conditions = [{"thread_id": thread_id}, {"source": source}]
    if user_id:
        conditions.append({"user_id": str(user_id)})

    where_filter = {"$and": conditions} if len(conditions) > 1 else conditions[0]

    result = get_vectorstore()._collection.get(
        where=where_filter,
        include=["documents", "metadatas"],
    )
    contents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    docs = [
        Document(page_content=content, metadata=metadata or {})
        for content, metadata in zip(contents, metadatas)
        if isinstance(content, str) and content.strip()
    ]

    if not docs:
        raise ValueError(
            f'No content found for "{source}" in this conversation. '
            "Check the file name or upload the file first."
        )

    # page orders PDF pages; start_index restores chunk order within a page.
    # chunk_index is retained as a fallback for records without start_index.
    docs.sort(key=lambda doc: (
        _sort_number(doc.metadata.get("page")),
        _sort_number(doc.metadata.get("start_index")),
        _sort_number(doc.metadata.get("chunk_index")),
    ))
    return docs


def _sort_number(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def list_document_sources(thread_id: str, user_id: str | None = None) -> list[str]:
    """List distinct filenames uploaded in a conversation."""
    where_filter = {"thread_id": thread_id}
    if user_id:
        where_filter = {"$and": [{"thread_id": thread_id}, {"user_id": str(user_id)}]}

    result = get_vectorstore()._collection.get(
        where=where_filter, include=["metadatas"]
    )
    return sorted({
        metadata.get("source")
        for metadata in (result.get("metadatas") or [])
        if metadata and metadata.get("source")
    }, key=str.casefold)


def add_image_to_rag(file_path: str, thread_id: str, filename: str, user_id: str | None = None) -> dict:
    """Register an uploaded image so image tools can find it in this thread."""
    path = Path(file_path).resolve()
    if not path.is_file():
        raise ValueError("Uploaded image could not be found on the server.")
    image_doc = Document(
        page_content=f"Uploaded image: {filename}",
        metadata={
            "thread_id": thread_id,
            "user_id": str(user_id) if user_id else "",
            "source": filename,
            "file_type": "image",
            "image_path": str(path),
        },
    )
    get_vectorstore().add_documents([image_doc])
    return {"filename": filename, "images": 1}


def list_uploaded_images(thread_id: str, user_id: str | None = None) -> list[dict]:
    """Return image metadata for one conversation without exposing other files."""
    conditions = [{"thread_id": thread_id}, {"file_type": "image"}]
    if user_id:
        conditions.append({"user_id": str(user_id)})

    where_filter = {"$and": conditions}

    result = get_vectorstore()._collection.get(
        where=where_filter,
        include=["metadatas"],
    )
    images = {}
    for metadata in result.get("metadatas") or []:
        if metadata and metadata.get("source") and metadata.get("image_path"):
            images[(metadata["source"], metadata["image_path"])] = {
                "source": metadata["source"],
                "image_path": metadata["image_path"],
            }
    return sorted(images.values(), key=lambda item: item["source"].casefold())
