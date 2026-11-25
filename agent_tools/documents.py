"""Tools for uploaded documents and images."""

from typing import Any

from langchain_core.tools import tool

from agent_tools._compat import tool_callable
from agent_tools.context import (
    SUMMARY_RESULT_PREFIX,
    SUMMARY_RESULT_PREFIX_VI,
    get_current_model_name as _get_current_model_name,
    get_current_project_id as _get_current_project_id,
    get_current_project_memory_mode as _get_current_project_memory_mode,
    get_current_thread_id as _get_current_thread_id,
    get_current_user_id as _get_current_user_id,
)
from services.image_analysis import analyze_image as _analyze_image
from services.rag import (
    get_document_chunks as _get_document_chunks,
    list_document_sources as _list_document_sources,
    list_uploaded_images as _list_uploaded_images,
    retrieve_from_rag as _retrieve_from_rag,
)
from services.summarization import summarize_documents as _summarize_documents
from services.token_limit_handler import (
    assemble_partial_summary as _assemble_partial_summary,
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
list_document_sources = tool_callable("list_document_sources", _list_document_sources)
list_uploaded_images = tool_callable("list_uploaded_images", _list_uploaded_images)
retrieve_from_rag = tool_callable("retrieve_from_rag", _retrieve_from_rag)
summarize_documents = tool_callable("summarize_documents", _summarize_documents)
assemble_partial_summary = tool_callable("assemble_partial_summary", _assemble_partial_summary)
format_tool_limit_notice = tool_callable("format_tool_limit_notice", _format_tool_limit_notice)
is_daily_token_limit = tool_callable("is_daily_token_limit", _is_daily_token_limit)

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
    )


def _clean_source_name(name: str) -> str:
    """Normalize a source name by removing UUID prefix and unifying whitespace/underscores."""
    cleaned = name
    if len(cleaned) > 37 and cleaned[36] == "_":
        prefix = cleaned[:36]
        if all(c in "0123456789abcdefABCDEF-" for c in prefix) and prefix.count("-") == 4:
            cleaned = cleaned[37:]
    return " ".join(cleaned.replace("_", " ").split()).strip().casefold()


def _select_uploaded_name(
    names: list[str], requested: str
) -> tuple[str | None, list[str]]:
    """Select an uploaded source, returning ambiguous matches when necessary."""
    if not requested or len(names) == 1:
        return names[0], []

    exact_matches = [name for name in names if name.casefold() == requested.casefold()]
    if len(exact_matches) == 1:
        return exact_matches[0], []

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


@tool
def summarize_uploaded_document(filename: str = "", target_language: str = "auto") -> str:
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
    user_id = get_current_user_id() or None
    project_id = get_current_project_id() or None
    current_chat_only = get_current_project_memory_mode() == "current_chat_only"
    sources = list_document_sources(
        get_current_thread_id(), user_id=user_id, project_id=project_id,
        current_chat_only=current_chat_only,
    )
    if not sources:
        return "No uploaded documents were found in this conversation or your account."

    requested = (filename or "").strip().strip('"\'')
    selected, matches = _select_uploaded_name(sources, requested)
    if selected is None:
        if matches:
            names = ", ".join(f'"{source}"' for source in matches)
            return f"The file name matches multiple uploads. Which one did you mean: {names}?"
        available = ", ".join(f'"{source}"' for source in sources)
        return f'No uploaded file named "{requested}" was found. Available files: {available}.'

    try:
        chunks = get_document_chunks(
            selected, get_current_thread_id(), user_id=user_id, project_id=project_id,
            current_chat_only=current_chat_only,
        )
        summary = summarize_documents(
            chunks,
            target_language=target_language,
            job_context={
                "user_id": str(user_id or ""),
                "thread_id": get_current_thread_id(),
                "project_id": str(project_id or ""),
                "source": selected,
                "model": get_current_model_name(),
            },
        )
    except Exception as exc:
        if is_daily_token_limit(exc):
            return assemble_partial_summary([], 1, filename=selected, language=target_language)
        return f'Could not summarize "{selected}": {exc}'

    # Nếu bản tóm tắt đã có tiền tố (ví dụ bản tóm tắt bán phần cứu hộ), trả về luôn
    if summary.startswith(SUMMARY_RESULT_PREFIX) or summary.startswith(SUMMARY_RESULT_PREFIX_VI):
        return summary

    if target_language and target_language.lower() in ("vietnamese", "tiếng việt", "tieng viet"):
        prefix = f'{SUMMARY_RESULT_PREFIX_VI}"{selected}" (toàn bộ {len(chunks)} phần nội dung):\n\n'
    else:
        prefix = f'{SUMMARY_RESULT_PREFIX}"{selected}" (all {len(chunks)} stored chunks):\n\n'
    return f'{prefix}{summary}'


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
    selected_name, matches = _select_uploaded_name(image_names, requested)
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
