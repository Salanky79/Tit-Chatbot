"""Tools for uploaded documents and images."""

import config as cfg

import logging

from langchain_core.tools import tool

from agent_tools._compat import tool_callable
from agent_tools.context import (
    SUMMARY_ERROR_PREFIX,
    SUMMARY_RESULT_PREFIX,
    SUMMARY_RESULT_PREFIX_VI,
    get_current_attachment_source,
    get_attachment_document_id,
    get_current_model_name as _get_current_model_name,
    get_current_project_id as _get_current_project_id,
    get_current_project_memory_mode as _get_current_project_memory_mode,
    get_current_thread_id as _get_current_thread_id,
    get_current_user_id as _get_current_user_id,
)
from services.image_analysis import analyze_image as _analyze_image
from services.language import normalize_summary_language
from services.rag import (
    get_document_chunks as _get_document_chunks,
    list_text_uploads,
    list_uploaded_images as _list_uploaded_images,
    retrieve_from_rag as _retrieve_from_rag,
)
from services.summarization import SummaryResult, summarize_documents as _summarize_documents
from services.token_limit_handler import (
    format_tool_limit_notice as _format_tool_limit_notice,
    is_daily_token_limit as _is_daily_token_limit,
)

get_current_model_name = tool_callable("get_current_model_name", _get_current_model_name)
get_current_project_id = tool_callable("get_current_project_id", _get_current_project_id)
get_current_project_memory_mode = tool_callable("get_current_project_memory_mode", _get_current_project_memory_mode)
get_current_thread_id = tool_callable("get_current_thread_id", _get_current_thread_id)
get_current_user_id = tool_callable("get_current_user_id", _get_current_user_id)
analyze_image = tool_callable("analyze_image", _analyze_image)
get_document_chunks = tool_callable("get_document_chunks", _get_document_chunks)
list_uploaded_images = tool_callable("list_uploaded_images", _list_uploaded_images)
retrieve_from_rag = tool_callable("retrieve_from_rag", _retrieve_from_rag)
summarize_documents = tool_callable("summarize_documents", _summarize_documents)
format_tool_limit_notice = tool_callable("format_tool_limit_notice", _format_tool_limit_notice)
is_daily_token_limit = tool_callable("is_daily_token_limit", _is_daily_token_limit)
logger = logging.getLogger(__name__)


# =============================================================================
# 01. Tool tìm kiếm nội dung tài liệu
# =============================================================================

@tool
def search_uploaded_documents(query: str) -> str:
    """Search specific passages or answers in uploaded documents.

    CRITICAL: DO NOT use this tool if the user asks for a summary of a file
    (e.g., 'tóm tắt file', 'tóm tắt tài liệu', 'summarize', 'tóm tắt nội dung').
    Use summarize_uploaded_document instead.

    Use this tool ONLY to answer specific factual questions or retrieve
    relevant excerpts from an uploaded PDF, DOCX, TXT, Markdown, CSV, or Python file.
    """
    return retrieve_from_rag(
        query=query,
        thread_id=get_current_thread_id(),
        user_id=get_current_user_id() or None,
        project_id=get_current_project_id() or None,
        current_chat_only=get_current_project_memory_mode() == "current_chat_only",
        source=get_current_attachment_source() or None,
    )



# =============================================================================
# 02. Chuẩn hóa và chọn đúng tài liệu
# =============================================================================

def _clean_source_name(name: str) -> str:
    """Normalize a source name by removing UUID prefix and unifying whitespace/underscores."""
    cleaned = name
    if len(cleaned) > 37 and cleaned[36] == "_":
        prefix = cleaned[:36]
        if all(c in "0123456789abcdefABCDEF-" for c in prefix) and prefix.count("-") == 4:
            cleaned = cleaned[37:]
    return " ".join(cleaned.replace("_", " ").split()).strip().casefold()


def _select_uploaded_name(
    names: list[str], requested: str, *, attached_source: str = ""
) -> tuple[str | None, list[str]]:
    """Select an uploaded source, returning ambiguous matches when necessary."""
    # This preference is only valid within the sources the caller may access.
    # An explicit request for another file still takes precedence.
    exact_matches = [name for name in names if name.casefold() == requested.casefold()]
    if len(exact_matches) == 1:
        return exact_matches[0], []
    if attached_source and (
        not requested or _clean_source_name(requested) == _clean_source_name(attached_source)
    ):
        return (attached_source, []) if attached_source in names else (None, [])
    if not requested:
        return (names[0], []) if len(names) == 1 else (None, names)
    normalized_request = _clean_source_name(requested)
    normalized_matches = [
        name for name in names
        if _clean_source_name(name) == normalized_request
    ]
    if len(normalized_matches) == 1:
        return normalized_matches[0], []

    partial_matches = [
        name for name in names
        if normalized_request in _clean_source_name(name)
        or _clean_source_name(name) in normalized_request
    ]
    if len(partial_matches) == 1:
        return partial_matches[0], []
    return None, partial_matches



# =============================================================================
# 03. Tool tóm tắt và xử lý kết quả
# =============================================================================

@tool(response_format="content_and_artifact")
def summarize_uploaded_document(filename: str = "", target_language: str = cfg.SUMMARY_LANGUAGE) -> tuple[str, dict[str, str]]:
    """Summarize the complete contents of one uploaded file in this conversation.

    CRITICAL: ALWAYS use this tool (and NEVER search_uploaded_documents) whenever
    the user asks to summarize, outline, or give an overview of a newly uploaded file
    or document (e.g. 'tóm tắt file', 'tóm tắt tài liệu', 'tóm tắt văn bản',
    'summarize', 'overview', 'synopsis').

    Pass its exact filename when it is named or clearly identified in the
    conversation. If only one file is uploaded, an empty filename selects that
    file. If several files exist and the user has not identified one, call with
    an empty filename to return a clarification request. Never combine files.

    Pass target_language:
    - 'auto' (default): Keep the document's original language (e.g., English file -> English summary, Vietnamese file -> Vietnamese summary).
    - 'Vietnamese': If the user explicitly requested Vietnamese for an English document.
    - 'English': If the user explicitly requested English.

    If the same document has an interrupted summary in this conversation, this
    tool automatically resumes from saved progress instead of starting over.
    """
    result = _summarize_upload(filename, target_language)
    return result.text, result.artifact()


def _summarize_upload(filename: str, target_language: str) -> SummaryResult:
    """Resolve a source, invoke the service, and format one terminal tool outcome."""
    selected = (filename or "").strip().strip('"\'') or "document"
    language = normalize_summary_language(target_language)
    try:
        thread_id = get_current_thread_id()
        user_id = get_current_user_id() or None
        project_id = get_current_project_id() or None
        scope = {
            "user_id": user_id,
            "project_id": project_id,
            "current_chat_only": get_current_project_memory_mode() == "current_chat_only",
        }
        uploads = list_text_uploads(thread_id, **scope)
        sources = sorted(set(uploads.values()), key=str.casefold)
        attachment_id = get_attachment_document_id()
        attached = uploads.get(attachment_id, "") if attachment_id else get_current_attachment_source()
        if not sources:
            return SummaryResult("No uploaded documents were found in this conversation or your account.", "clarification")
        requested = (filename or "").strip().strip('"\'')
        if attachment_id and attachment_id not in uploads and (
            not requested or _clean_source_name(requested) == _clean_source_name(get_current_attachment_source())
        ):
            return SummaryResult("The attached document is no longer available in this conversation.", "clarification")
        selected, matches = _select_uploaded_name(
            sources, requested, attached_source=attached,
        )
        if selected is None:
            if matches:
                names = ", ".join(f'"{source}"' for source in matches)
                return SummaryResult(f"Which uploaded file did you mean: {names}?", "clarification")
            available = ", ".join(f'"{source}"' for source in sources)
            return SummaryResult(f'No uploaded file named "{requested}" was found. Available files: {available}.', "clarification")
        ids = [doc_id for doc_id, source in uploads.items() if source == selected]
        if attachment_id in ids:
            document_id = attachment_id
        elif len(ids) == 1:
            document_id = ids[0]
        else:
            return SummaryResult(f'Multiple uploads are named "{selected}". Attach the intended file.', "clarification")

        result = summarize_documents(
            lambda: get_document_chunks(selected, thread_id, document_id=document_id, **scope),
            target_language=language,
            job_context={
                "user_id": str(user_id or ""), "thread_id": thread_id,
                "project_id": str(project_id or ""), "document_id": document_id,
                "model": get_current_model_name(),
            },
        )
        if result.status == "partial":
            return result
        if language == "Vietnamese":
            prefix = f'{SUMMARY_RESULT_PREFIX_VI}"{selected}":\n\n'
        else:
            prefix = f'{SUMMARY_RESULT_PREFIX}"{selected}":\n\n'
        return SummaryResult(prefix + result.text, result.status)
    except Exception:
        logger.exception("Document summarization failed for %s", selected)
        return SummaryResult(
            f'{SUMMARY_ERROR_PREFIX}"{selected or filename}" do lỗi xử lý nội bộ. '
            "Chưa tạo được bản tóm tắt. Bạn có thể yêu cầu thử lại sau; không cần tải lại tài liệu.",
            "error",
        )



# =============================================================================
# 04. Tool phân tích ảnh
# =============================================================================

@tool
def analyze_uploaded_image(question: str, filename: str = "") -> str:
    """Answer questions about an image uploaded in this conversation.

    Use for image descriptions, visual questions, charts, diagrams, or text/OCR
    in an uploaded image. Pass the image filename when named or clearly
    identified. An empty filename selects the only uploaded image; when several
    images exist, ask which one instead of combining or guessing.
    """
    user_id = get_current_user_id() or None
    images = list_uploaded_images(
        get_current_thread_id(), user_id=user_id,
        project_id=get_current_project_id() or None,
        current_chat_only=get_current_project_memory_mode() == "current_chat_only",
    )
    if not images:
        return "No uploaded images were found in this conversation or your account. Upload a JPG, PNG, or WEBP image first."

    requested = (filename or "").strip().strip('"\'')
    image_names = [image["source"] for image in images]
    selected_name, matches = _select_uploaded_name(
        image_names, requested, attached_source=get_current_attachment_source(),
    )
    if selected_name is None:
        if matches:
            names = ", ".join(f'"{name}"' for name in matches)
            return f"The image name matches multiple uploads. Which one did you mean: {names}?"
        available = ", ".join(f'"{name}"' for name in image_names)
        return f'No uploaded image named "{requested}" was found. Available files: {available}.'
    selected = next(image for image in images if image["source"] == selected_name)
    try:
        answer = analyze_image(selected["image_path"], question)
    except Exception as exc:
        if is_daily_token_limit(exc):
            return format_tool_limit_notice("analyze_uploaded_image")
        return f'Could not analyze "{selected["source"]}": {exc}'
    return f'Image analysis of "{selected["source"]}":\n\n{answer}'
