from pathlib import Path
from datetime import datetime, timezone
import logging
from typing import Any
from dotenv import load_dotenv
import os
import certifi

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

logger = logging.getLogger(__name__)

from bson import ObjectId

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

def add_document_to_rag(
    file_path: str,
    thread_id: str,
    user_id: str | None = None,
    *,
    document_id: str | None = None,
) -> dict:
    """Đọc file, tách thành chunks (kèm metadata trang & user_id), lưu vào ChromaDB.

    Args:
        file_path: Đường dẫn file đã upload.
        thread_id: ID luồng chat để lọc kết quả theo session.
        user_id: ID người dùng sở hữu tài liệu (nếu có) để cách ly dữ liệu.

    Returns:
        dict với ``filename``, ``chunks``, ``pages`` (số trang đã đọc được).
    """
    raw_docs = _load_documents(file_path, thread_id, user_id=user_id)

    # Gắn ID riêng cho lần upload (không chỉ tên file) để phân biệt các file trùng tên.
    uploaded_at = datetime.now(timezone.utc).isoformat()
    for doc in raw_docs:
        doc.metadata.update({
            "document_id": document_id or Path(file_path).stem,
            "file_path": str(Path(file_path).resolve()),
            "uploaded_at": uploaded_at,
        })

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

    Cơ chế Hybrid:
    - Bước 1: Ưu tiên tìm kiếm tài liệu trong thread hiện tại.
    - Bước 2: Nếu không tìm thấy và người dùng đã đăng nhập (có user_id),
      mở rộng tìm kiếm sang toàn bộ tài liệu đã tải lên của user đó.

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

    # Fallback: Nếu thread hiện tại không có tài liệu và user đã đăng nhập, tìm rộng ra toàn bộ tài liệu của user
    is_cross_thread = False
    if not docs and user_id:
        user_filter = {"user_id": str(user_id)}
        docs = get_vectorstore().similarity_search(
            query,
            k=k,
            filter=user_filter
        )
        if docs:
            is_cross_thread = True

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
            label_text = f"{source} – {page_label}"
        else:
            label_text = source

        if is_cross_thread:
            label = f"[Source {i}: {label_text} (từ tài liệu bạn đã tải lên trước đó)]"
        else:
            label = f"[Source {i}: {label_text}]"

        results.append(f"{label}\n{doc.page_content}")

    return "\n\n".join(results)


def get_document_chunks(source: str, thread_id: str, user_id: str | None = None) -> list[Document]:
    """Return every stored chunk for one uploaded file.

    Cơ chế Hybrid:
    - Tìm file trong thread hiện tại trước.
    - Nếu không thấy và có user_id, fallback tìm trong toàn bộ file của user đó.
    """
    source = (source or "").strip()
    thread_id = (thread_id or "").strip()
    if not source:
        raise ValueError("A file name is required to summarize a document.")
    if not thread_id:
        raise ValueError("A conversation ID is required to find the document.")

    # 1. Tìm trong thread hiện tại
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

    # 2. Fallback: Nếu không tìm thấy trong thread hiện tại và user đã đăng nhập, tìm trong file của user
    if not contents and user_id:
        where_filter = {"$and": [{"user_id": str(user_id)}, {"source": source}]}
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
            f'No content found for "{source}". '
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
    """List distinct filenames uploaded in a conversation, falling back to all user documents."""
    where_filter = {"thread_id": thread_id}
    if user_id:
        where_filter = {"$and": [{"thread_id": thread_id}, {"user_id": str(user_id)}]}

    result = get_vectorstore()._collection.get(
        where=where_filter, include=["metadatas"]
    )
    sources = sorted({
        metadata.get("source")
        for metadata in (result.get("metadatas") or [])
        if metadata and metadata.get("source")
    }, key=str.casefold)

    # Fallback: Nếu thread hiện tại chưa có file nào nhưng có user_id, lấy danh sách file user từng upload
    if not sources and user_id:
        result = get_vectorstore()._collection.get(
            where={"user_id": str(user_id)}, include=["metadatas"]
        )
        sources = sorted({
            metadata.get("source")
            for metadata in (result.get("metadatas") or [])
            if metadata and metadata.get("source")
        }, key=str.casefold)

    return sources


def add_image_to_rag(
    file_path: str,
    thread_id: str,
    filename: str,
    user_id: str | None = None,
    *,
    document_id: str | None = None,
) -> dict:
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
            "file_path": str(path),
            "document_id": document_id or path.stem,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    get_vectorstore().add_documents([image_doc])
    return {"filename": filename, "images": 1}


def list_admin_documents() -> list[dict[str, Any]]:
    """Lập danh sách tài liệu cho Admin bằng cách gom các chunks của cùng một upload.

    Chroma lưu mỗi chunk thành một bản ghi riêng, còn bảng Admin cần một dòng
    cho mỗi file. Các chunks được nhóm theo ``document_id`` mà upload mới gắn
    vào metadata.

    Trả về metadata phục vụ hiển thị và số chunks của từng tài liệu. Bản ghi
    không có ``document_id`` bị bỏ qua; hệ thống hiện giả định dữ liệu Chroma
    được tạo theo schema upload mới. Hàm đọc metadata của toàn collection; khi
    số tài liệu lớn, nên thay bằng registry tài liệu có phân trang ở storage.
    """
    collection = get_vectorstore()._collection
    # API get trả IDs kèm kết quả; phần listing chỉ cần metadata nên không giữ IDs.
    result = collection.get(include=["metadatas"])
    grouped: dict[str, dict[str, Any]] = {}

    for metadata in result.get("metadatas") or []:
        # Bản ghi không có document_id không thuộc schema Admin hiện tại.
        if not metadata or not metadata.get("source") or not metadata.get("document_id"):
            continue
        document_id = metadata.get("document_id")
        item = grouped.setdefault(document_id, {
            "id": str(document_id),
            "document_id": document_id,
            "source": metadata.get("source", ""),
            "user_id": metadata.get("user_id", "") or None,
            "thread_id": metadata.get("thread_id", "") or None,
            "file_type": metadata.get("file_type", "unknown"),
            "uploaded_at": metadata.get("uploaded_at"),
            "chunks": 0,
            "file_path": metadata.get("file_path") or metadata.get("image_path"),
        })
        # Mỗi metadata record tương ứng một chunk của tài liệu.
        item["chunks"] += 1

    # uploaded_at ISO UTC có thể sắp xếp dạng chuỗi; tên file là tiêu chí phụ.
    return sorted(
        grouped.values(),
        key=lambda item: (item.get("uploaded_at") or "", item["source"].casefold()),
        reverse=True,
    )


def delete_user_documents(user_id: str) -> dict[str, int]:
    """Xóa chunks Chroma và file tải lên thuộc về một user.

    Xóa file trước metadata Chroma để nếu file system lỗi, Admin còn metadata
    làm căn cứ thử dọn lại.
    """
    if not ObjectId.is_valid(user_id):
        raise ValueError("ID người dùng không hợp lệ.")

    user_filter = {"user_id": str(ObjectId(user_id))}
    collection = get_vectorstore()._collection
    result = collection.get(where=user_filter, include=["metadatas"])
    metadatas = result.get("metadatas") or []
    file_paths = set()
    for metadata in metadatas:
        metadata = metadata or {}
        file_path = metadata.get("file_path") or metadata.get("image_path")
        if file_path:
            file_paths.add(str(file_path))

    upload_root = Path("uploads").resolve()
    deleted_files = 0
    failed_paths: list[str] = []
    for file_path in file_paths:
        candidate = Path(file_path).resolve()
        if not candidate.is_relative_to(upload_root) or not candidate.is_file():
            continue
        try:
            candidate.unlink()
            deleted_files += 1
        except OSError as exc:
            import gc
            gc.collect()
            try:
                candidate.unlink()
                deleted_files += 1
            except OSError as retry_exc:
                logger.warning("Could not remove uploaded file %s: %s", candidate, retry_exc)
                failed_paths.append(str(candidate))

    if failed_paths:
        raise RuntimeError(
            "Không thể xóa một số file tải lên; dữ liệu Chroma được giữ lại để thử lại."
        )

    chunk_count = len(result.get("ids") or [])
    if chunk_count:
        collection.delete(where=user_filter)
    return {"chunks_deleted": chunk_count, "files_deleted": deleted_files}


def delete_admin_document(document_key: str) -> bool:
    """Xóa tài liệu bằng metadata filter mà không tải toàn collection vào Python.

    ``document_key`` là ``document_id`` được gắn lên mọi chunk của lần upload.
    Hàm chỉ đọc metadata của tối đa một chunk để lấy đường dẫn file, sau đó yêu
    cầu Chroma xóa tất cả chunks khớp ID. Vì vậy lượng dữ liệu trả về Python
    không tăng theo kích thước của toàn collection.

    File vật lý chỉ bị xóa nếu metadata có đường dẫn và đường dẫn sau khi
    resolve vẫn nằm dưới thư mục ``uploads``.

    Returns:
        ``True`` nếu tìm thấy tài liệu và gửi yêu cầu xóa chunks; ``False`` nếu
        không có chunk nào mang ``document_key``.

    Lưu ý: file system và Chroma không hỗ trợ một transaction chung. Vì file
    được xóa trước, lỗi xảy ra trong bước xóa Chroma có thể để lại chunks mồ côi.
    """
    collection = get_vectorstore()._collection
    where_filter = {"document_id": document_key}

    # Lấy duy nhất một metadata record: mọi chunk cùng upload chia sẻ file_path.
    # get() luôn trả IDs; nếu không có ID thì document_key không tồn tại trong Chroma.
    result = collection.get(
        where=where_filter,
        limit=1,
        include=["metadatas"],
    )
    if not result.get("ids"):
        return False

    metadata_list = result.get("metadatas") or []
    metadata = metadata_list[0] if metadata_list else None
    file_path = (metadata or {}).get("file_path") or (metadata or {}).get("image_path")
    if file_path:
        # Resolve cả hai phía để chặn đường dẫn kiểu ../uploads hoặc symlink
        # thoát khỏi thư mục upload; chỉ file nằm trong uploads mới được unlink.
        upload_root = Path("uploads").resolve()
        candidate = Path(file_path).resolve()
        if candidate.is_relative_to(upload_root) and candidate.is_file():
            try:
                candidate.unlink()
            except OSError as exc:
                # Trên Windows, nếu file đang bị tiến trình khác giữ handle (file lock / WinError 32),
                # thử kích hoạt thu gom rác (garbage collection) và thử lại 1 lần nữa.
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

    # Xóa tất cả chunks theo metadata ngay trong Chroma, không tải danh sách IDs về app.
    collection.delete(where=where_filter)
    return True


def list_uploaded_images(thread_id: str, user_id: str | None = None) -> list[dict]:
    """Return image metadata for one conversation, falling back to user images."""
    conditions = [{"thread_id": thread_id}, {"file_type": "image"}]
    if user_id:
        conditions.append({"user_id": str(user_id)})

    where_filter = {"$and": conditions}

    result = get_vectorstore()._collection.get(
        where=where_filter,
        include=["metadatas"],
    )
    metadatas = result.get("metadatas") or []

    # Fallback: Nếu thread hiện tại chưa có ảnh nào nhưng có user_id, tìm các ảnh user từng upload
    if not metadatas and user_id:
        where_filter = {"$and": [{"user_id": str(user_id)}, {"file_type": "image"}]}
        result = get_vectorstore()._collection.get(
            where=where_filter,
            include=["metadatas"],
        )
        metadatas = result.get("metadatas") or []

    images = {}
    for metadata in metadatas:
        if metadata and metadata.get("source") and metadata.get("image_path"):
            images[(metadata["source"], metadata["image_path"])] = {
                "source": metadata["source"],
                "image_path": metadata["image_path"],
            }
    return sorted(images.values(), key=lambda item: item["source"].casefold())
