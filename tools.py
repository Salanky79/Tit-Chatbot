import math
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
from database import save_memory, search_memory
from rag import get_document_chunks, list_document_sources, retrieve_from_rag
from summarization import summarize_documents


load_dotenv()


CURRENT_THREAD_ID = "default"


def set_current_thread_id(thread_id: str):
    global CURRENT_THREAD_ID
    CURRENT_THREAD_ID = thread_id


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
    """Search the content of documents uploaded by the user in this session.

    Use this tool when the user:
    - Asks about an uploaded PDF, DOCX, TXT, Markdown, CSV, or Python file.
    - Says "according to the document / file / report / paper ...".
    - Asks to explain or find specific information from an upload.
    - References "the file I uploaded" or "the document you have".

    For a summary of an entire file, use summarize_uploaded_document instead.
    Do NOT use this for general knowledge questions that do not relate to an
    uploaded file. Returns the most relevant passages found in the documents.
    """
    return retrieve_from_rag(query=query, thread_id=CURRENT_THREAD_ID)


@tool
def summarize_uploaded_document(filename: str = "") -> str:
    """Summarize the complete contents of one uploaded file in this conversation.

    Use this when the user asks for a summary of an entire uploaded document or
    file. Pass its exact filename when it is named or clearly identified in the
    conversation. If only one file is uploaded, an empty filename selects that
    file. If several files exist and the user has not identified one, call with
    an empty filename to return a clarification request. Never combine files.
    """
    sources = list_document_sources(CURRENT_THREAD_ID)
    if not sources:
        return "No uploaded documents were found in this conversation."

    requested = (filename or "").strip().strip('"\'')
    if not requested:
        if len(sources) != 1:
            names = ", ".join(f'"{source}"' for source in sources)
            return f"Which file should I summarize? Files in this conversation: {names}."
        selected = sources[0]
    else:
        exact = [source for source in sources if source.casefold() == requested.casefold()]
        if len(exact) == 1:
            selected = exact[0]
        else:
            partial = [source for source in sources if requested.casefold() in source.casefold()]
            if len(partial) == 1:
                selected = partial[0]
            elif len(partial) > 1:
                names = ", ".join(f'"{source}"' for source in partial)
                return f"The file name matches multiple uploads. Which one did you mean: {names}?"
            else:
                names = ", ".join(f'"{source}"' for source in sources)
                return f'No uploaded file named "{requested}" was found. Available files: {names}.'

    try:
        chunks = get_document_chunks(selected, CURRENT_THREAD_ID)
        summary = summarize_documents(chunks)
    except Exception as exc:
        return f'Could not summarize "{selected}": {exc}'
    return f'Summary of "{selected}" (all {len(chunks)} stored chunks):\n\n{summary}'


# ---------------------------------------------------------------------------
# Long-term memory - Save
# ---------------------------------------------------------------------------
@tool
def remember_this(memory: str) -> str:
    """Save an important fact, preference, or piece of information to
    long-term memory so it can be recalled in future conversations.

    Use this tool when the user explicitly asks you to remember something,
    for example:
    - "Remember that my name is Nam."
    - "Save this: I prefer dark mode."
    - "Keep in mind that my birthday is March 15."
    - "Store this for later: ..."

    The memory string should be a concise, self-contained sentence that
    captures the key fact (e.g. "User's name is Nam.").
    Returns a confirmation message on success.
    """
    return save_memory(thread_id=CURRENT_THREAD_ID, memory=memory)


# ---------------------------------------------------------------------------
# Long-term memory - Recall
# ---------------------------------------------------------------------------
@tool
def recall_memory(query: str) -> str:
    """Search and retrieve previously saved long-term memories relevant to
    the given query.

    Use this tool when the user:
    - Asks "What do you remember about me?"
    - Refers to something they told you to remember earlier.
    - Asks about their saved preferences, name, or personal details.
    - Uses phrases like "recall", "do you remember", "what did I tell you".

    Returns a list of matching memory entries, or a message if none are found.
    """
    return search_memory(thread_id=CURRENT_THREAD_ID, query=query)


# ---------------------------------------------------------------------------
# Tool registry – thêm tool mới vào đây, không cần sửa agent.py hay prompt
# ---------------------------------------------------------------------------
tools = [
    calculator,
    search_uploaded_documents,
    summarize_uploaded_document,
    remember_this,
    recall_memory,
    web_search,
]
