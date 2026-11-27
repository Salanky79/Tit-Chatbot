"""Backward-compatible exports for the agent tool collection."""


# =============================================================================
# 01. Phụ thuộc và lớp tương thích công khai
# =============================================================================

from contextvars import ContextVar
from typing import Annotated, Any
import json
import math

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langchain_tavily import TavilySearch

from storage.database import (
    delete_memory_by_id,
    get_user_memories as load_user_memories,
    save_memory,
    update_memory_by_id,
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
    assemble_partial_summary,
    format_tool_limit_notice,
    is_daily_token_limit,
)

load_dotenv()

from agent_tools.context import (
    SUMMARY_RESULT_PREFIX,
    SUMMARY_RESULT_PREFIX_VI,
    _current_model_name,
    _current_thread_id,
    _current_user_id,
    get_current_model_name,
    get_current_personalization,
    get_current_project_id,
    get_current_project_memory_mode,
    get_current_thread_id,
    get_current_user_id,
    set_current_model_name,
    set_current_personalization,
    set_current_project,
    set_project_prompt_context,
    set_current_thread_id,
    set_current_user_id,
)
from agent_tools.general import calculator, web_search
from agent_tools.documents import (
    _clean_source_name,
    _select_uploaded_name,
    analyze_uploaded_image,
    search_uploaded_documents,
    summarize_uploaded_document,
)
from agent_tools.memory import (
    _current_turn_memory_results,
    forget_memory_entry,
    get_user_memories,
    remember_this,
    update_memory_entry,
)
from agent_tools.project_memory import (
    forget_project_fact,
    remember_project_fact,
    search_project_facts,
    update_project_fact,
)

tools = [
    calculator,
    search_uploaded_documents,
    summarize_uploaded_document,
    analyze_uploaded_image,
    remember_this,
    get_user_memories,
    update_memory_entry,
    forget_memory_entry,
    forget_project_fact,
    remember_project_fact,
    search_project_facts,
    update_project_fact,
    web_search,
]
