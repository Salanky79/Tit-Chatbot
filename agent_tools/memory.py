"""Tools for saving and managing long-term user memories."""

import json
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from agent_tools._compat import tool_callable
from agent_tools.context import (
    get_current_thread_id as _get_current_thread_id,
    get_current_user_id as _get_current_user_id,
)
from storage.database import (
    delete_memory_by_id as _delete_memory_by_id,
    get_user_memories as _load_user_memories,
    save_memory as _save_memory,
    update_memory_by_id as _update_memory_by_id,
)

get_current_thread_id = tool_callable("get_current_thread_id", _get_current_thread_id)
get_current_user_id = tool_callable("get_current_user_id", _get_current_user_id)
delete_memory_by_id = tool_callable("delete_memory_by_id", _delete_memory_by_id)
load_user_memories = tool_callable("load_user_memories", _load_user_memories)
save_memory = tool_callable("save_memory", _save_memory)
update_memory_by_id = tool_callable("update_memory_by_id", _update_memory_by_id)

@tool
def remember_this(memory: str) -> str:
    """Save persistent facts, personal attributes, preferences, or identity details to long-term memory.

    CRITICAL: This memory is permanently saved for the CURRENT USER across ALL their chat
    conversations. When saved here, it is automatically available in all future chats of this user.

    Use this tool whenever the user shares persistent personal information about themselves
    (such as their name, identity, profession, preferences, habits, or background) in any
    conversational style, or whenever they want a fact remembered for the future.

    The memory string should be a concise, self-contained factual statement
    (e.g. 'Tên người dùng là Sơn.').
    Returns a confirmation message on success.
    """
    user_id = get_current_user_id() or None
    return save_memory(thread_id=get_current_thread_id(), memory=memory, user_id=user_id)


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
    """Retrieve this user's long-term memories using semantic search and profile reasoning.

    A non-empty query searches memories by semantic concept. If no close semantic
    match is found, all active memories are automatically returned for the agent's review.
    Pass an empty query ("") to inspect all active memories directly.

    The memory list is for internal agent reasoning; never present it as a raw choice list to the user.
    """
    # Read only messages from the current user turn, not previous checkpointed
    # turns, so the one-full-read guard resets naturally on the next request.
    messages = state.get("messages", []) if state else []
    full_read_already_used = False
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if not isinstance(message, ToolMessage) or getattr(message, "name", None) != "get_user_memories":
            continue
        try:
            prior_result = json.loads(message.content) if isinstance(message.content, str) else {}
        except (TypeError, json.JSONDecodeError):
            prior_result = {}
        if prior_result.get("source") in {"all_requested", "fallback_no_semantic_match", "fallback_no_regex_match"}:
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
