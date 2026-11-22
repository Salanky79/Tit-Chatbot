from contextvars import ContextVar
from typing import Annotated, Any
import json
import math
import re
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.prebuilt import InjectedState
from langchain_tavily import TavilySearch
from storage.database import (
    save_memory,
    get_user_memories as load_user_memories,
    update_memory_by_id,
    delete_memory_by_id,
)
from services.rag import (
    get_document_chunks,
    list_document_sources,
    list_uploaded_images,
    retrieve_from_rag,
)
from services.image_analysis import analyze_image
from services.summarization import summarize_documents
from services.token_limit_handler import (
    is_daily_token_limit,
    assemble_partial_summary,
    format_tool_limit_notice,
)


load_dotenv()


# Ngữ cảnh theo từng request (ContextVar) — an toàn tuyệt đối khi nhiều người dùng chat đồng thời
_current_thread_id: ContextVar[str] = ContextVar("current_thread_id", default="default")
_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")

# Tiền tố của kết quả tóm tắt THÀNH CÔNG. agent.py dựa vào đây để trả thẳng bản tóm tắt
# cho người dùng mà không gọi LLM lần nữa (lỗi / câu hỏi làm rõ không có tiền tố này).
SUMMARY_RESULT_PREFIX = "Summary of "
SUMMARY_RESULT_PREFIX_VI = "B\u1ea3n t\u00f3m t\u1eaft c\u1ee7a "


def set_current_thread_id(thread_id: str) -> None:
    _current_thread_id.set(thread_id)


def set_current_user_id(user_id: str | None) -> None:
    _current_user_id.set(user_id or "")


def get_current_thread_id() -> str:
    return _current_thread_id.get()


def get_current_user_id() -> str:
    return _current_user_id.get()


# ---------------------------------------------------------------------------
# Web Search
# ---------------------------------------------------------------------------
web_search = TavilySearch(
    name="web_search",
    description=(
        "Search the internet for real-time and up-to-date information. "
        "Use this tool when the user asks about: latest news, current events, "
        "today's date or time, recent updates, current prices, stock prices, "
        "weather, sports scores, trending topics, new product releases, "
        "current software versions, who currently holds a position/role, "
        "or anything that may have changed after the model's training cutoff. "
        "Always cite that the answer is based on web search results."
    ),
    max_results=5,
    topic="general",
    search_depth="advanced",
)


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------
@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression and return the numeric result.

    Use this tool when the user asks to calculate, compute, or solve a math
    problem. Input must be a valid Python math expression.

    Supported: +, -, *, /, **, //, %, math.sqrt(), math.log(), math.sin(),
    abs(), round(), min(), max(), sum().

    Examples:
        "2 + 2"            -> "4"
        "math.sqrt(144)"   -> "12.0"
        "125 * 48 / 6"     -> "1000.0"
    """
    try:
        allowed = {
            "math": math,
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": sum,
        }
        result = eval(expression, {"__builtins__": {}}, allowed)
        return str(result)
    except Exception as e:
        return f"Calculation error: {str(e)}"


# ---------------------------------------------------------------------------
# RAG - Uploaded documents
# ---------------------------------------------------------------------------
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
        user_id=get_current_user_id() or None
    )


def _clean_source_name(name: str) -> str:
    """Normalize a source name by removing UUID prefix and unifying whitespace/underscores."""
    cleaned = re.sub(r'^[a-f0-9\-]{36}_', '', name, flags=re.IGNORECASE)
    return re.sub(r'[\s_]+', ' ', cleaned).strip().casefold()


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
    """
    user_id = get_current_user_id() or None
    sources = list_document_sources(get_current_thread_id(), user_id=user_id)
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
        chunks = get_document_chunks(selected, get_current_thread_id(), user_id=user_id)
        summary = summarize_documents(chunks, target_language=target_language)
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
    images = list_uploaded_images(get_current_thread_id(), user_id=user_id)
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


# ---------------------------------------------------------------------------
# Long-term memory - Save
# ---------------------------------------------------------------------------
@tool
def remember_this(memory: str) -> str:
    """Save an important fact, preference, identity, or detail to the user's permanent long-term memory.

    CRITICAL: This memory is permanently saved for the CURRENT USER across ALL their chat
    conversations. When saved here, it can be recalled in any other chat of this user.

    Use this tool whenever the user explicitly asks you to remember or store something,
    for example:
    - "Nhớ rằng tôi tên là Sơn."
    - "Remember that my name is Nam."
    - "Tôi thích giao diện dark mode và lập trình Python."
    - "Keep in mind that my birthday is March 15."
    - "Lưu lại điều này: tôi đang học Đại học Công nghệ (UET)."

    The memory string should be a concise, self-contained statement
    capturing the key fact (e.g. "Tên người dùng là Sơn.").
    Returns a confirmation message on success.
    """
    user_id = get_current_user_id() or None
    return save_memory(thread_id=get_current_thread_id(), memory=memory, user_id=user_id)


# ---------------------------------------------------------------------------
# Long-term memory - Search and recall
# ---------------------------------------------------------------------------
def _current_turn_memory_results(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Parse memory retrievals from this turn, excluding checkpointed prior turns."""
    memories: list[dict[str, Any]] = []
    messages = state.get("messages", []) if state else []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if not isinstance(message, ToolMessage) or getattr(message, "name", None) != "get_user_memories":
            continue
        try:
            result = json.loads(message.content) if isinstance(message.content, str) else {}
        except (TypeError, json.JSONDecodeError):
            continue
        memories.extend(result.get("memories", []))
    return memories


@tool
def get_user_memories(
    state: Annotated[dict[str, Any], InjectedState],
    query: str = "",
) -> str:
    """Retrieve this user's long-term memories for the agent to inspect.

    A non-empty query first uses safe regex matching. If that finds no entries,
    all active memories are returned for semantic review by the LLM. Use an
    empty query to explicitly retrieve all active memories. The memory list is
    for internal agent reasoning; never present it as a choice list to the user.
    """
    # Read only messages from the current user turn, not previous checkpointed
    # turns, so the one-full-read guard resets naturally on the next request.
    messages = state.get("messages", []) if state else []
    full_read_already_used = False
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if not isinstance(message, ToolMessage) or getattr(message, "name", None) != "get_user_memories":
            continue # nếu không phải là ToolMessage của get_user_memories thì bỏ qua
        try:
            prior_result = json.loads(message.content) if isinstance(message.content, str) else {}
        except (TypeError, json.JSONDecodeError):
            prior_result = {}
        if prior_result.get("source") in {"all_requested", "fallback_no_regex_match"}: # query rỗng hoặc không tìm thấy regex, đã đọc toàn bộ memory
            full_read_already_used = True
            break

    clean_query = (query or "").strip()
    if full_read_already_used and not clean_query:
        return json.dumps(
            {
                "status": "full_read_already_used",
                "source": "none",
                "memories": [],
                "message": "The full-memory fallback has already been used for this user request. Do not call this tool again for the same request.",
            },
            ensure_ascii=False,
        )

    result = load_user_memories(
        user_id=get_current_user_id() or None,
        query=clean_query,
        allow_full_fallback=not full_read_already_used,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Long-term memory - Update by ID (Bước 4)
# ---------------------------------------------------------------------------
@tool
def update_memory_entry(
    state: Annotated[dict[str, Any], InjectedState],
    memory_id: str,
    new_memory: str,
    version: int,
) -> str:
    """Update an existing long-term memory entry using its exact memory_id and current version.

    CRITICAL RULES (Bước 4):
    1. NEVER guess or invent a memory_id. You MUST obtain the memory_id and current version from get_user_memories first.
    2. Pass the exact memory_id and version received.
    3. Pass the full, updated statement in new_memory (e.g. 'Tên người dùng là Nam.').
    """
    if not any(
        entry.get("memory_id") == memory_id and int(entry.get("version", -1)) == int(version)
        for entry in _current_turn_memory_results(state)
    ):
        return "Memory update blocked: retrieve this exact memory ID and version with get_user_memories during the current request first."

    user_id = get_current_user_id() or None
    return update_memory_by_id(
        user_id=user_id,
        memory_id=memory_id,
        new_memory=new_memory,
        version=version,
    )


# ---------------------------------------------------------------------------
# Long-term memory - Forget / Soft-Delete by ID (Bước 4)
# ---------------------------------------------------------------------------
@tool
def forget_memory_entry(
    state: Annotated[dict[str, Any], InjectedState],
    memory_id: str,
    version: int,
) -> str:
    """Soft-delete an existing long-term memory entry using its exact memory_id and current version.

    CRITICAL RULES (Bước 4):
    1. NEVER guess or invent a memory_id. You MUST obtain the memory_id and current version from get_user_memories first.
    2. Pass the exact memory_id and version received.
    """
    if not any(
        entry.get("memory_id") == memory_id and int(entry.get("version", -1)) == int(version)
        for entry in _current_turn_memory_results(state)
    ):
        return "Memory deletion blocked: retrieve this exact memory ID and version with get_user_memories during the current request first."

    user_id = get_current_user_id() or None
    return delete_memory_by_id(
        user_id=user_id,
        memory_id=memory_id,
        version=version,
    )


# ---------------------------------------------------------------------------
# Tool registry – thêm tool mới vào đây, không cần sửa agent.py hay prompt
# ---------------------------------------------------------------------------
tools = [
    calculator,
    search_uploaded_documents,
    summarize_uploaded_document,
    analyze_uploaded_image,
    remember_this,
    get_user_memories,
    update_memory_entry,
    forget_memory_entry,
    web_search,
]
