"""Tools for shared facts scoped to the active project."""

import json
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from agent_tools.context import (
    get_current_project_id,
    get_current_project_memory_mode,
    get_current_thread_id,
    get_current_user_id,
)
from storage.memories import delete_memory_by_id, update_memory_by_id
from storage.projects import (
    can_edit_project,
    create_project_memory,
    get_membership,
    search_project_memory_entries,
)


def _project_memory_enabled() -> bool:
    """Return whether project memory is enabled for the current request."""
    return bool(get_current_project_id()) and get_current_project_memory_mode() != "current_chat_only"

# trước khi update hoặc delete một project memory, cần kiểm tra xem memory đó có được search ra trong cùng request hay không
def _current_turn_project_memory_results(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return project memories retrieved during the current chat turn."""
    memories: list[dict[str, Any]] = []
    messages = state.get("messages", []) if state else []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if not isinstance(message, ToolMessage) or getattr(message, "name", None) != "search_project_facts":
            continue
        try:
            result = json.loads(message.content) if isinstance(message.content, str) else {}
        except (TypeError, json.JSONDecodeError):
            continue
        if result.get("scope") == "project":
            memories.extend(result.get("memories", []))
    return memories # trả về danh sách các project memory được search ra trong cùng request, để check trước khi update hoặc delete


@tool
def remember_project_fact(fact: str) -> str:
    """Save one durable fact shared by members of the active project."""
    project_id = get_current_project_id()
    if not project_id:
        return "Project memory is available only inside a project chat."
    if not _project_memory_enabled():
        return "Project memory is disabled in current-chat-only mode."
    if not (fact or "").strip():
        return "Provide a project fact to save."
    return create_project_memory(
        project_id=project_id,
        user_id=get_current_user_id(),
        memory=fact,
        thread_id=get_current_thread_id(),
    )


@tool
def search_project_facts(query: str = "") -> str:
    """Search the active project's saved facts and return IDs and versions."""
    project_id = get_current_project_id()
    if not project_id:
        return json.dumps({"status": "outside_project", "scope": "project", "memories": []})
    if not _project_memory_enabled():
        return json.dumps({"status": "disabled_in_project", "scope": "project", "memories": []})
    if not get_membership(project_id, get_current_user_id()):
        return json.dumps({"status": "forbidden", "scope": "project", "memories": []})
    result = search_project_memory_entries(project_id, query, limit=5)
    return json.dumps(result, ensure_ascii=False, default=str)


@tool
def update_project_fact(
    state: Annotated[dict[str, Any], InjectedState],
    memory_id: str,
    new_memory: str,
    version: int,
) -> str:
    """Update a project memory retrieved during the current chat turn."""
    project_id = get_current_project_id()
    user_id = get_current_user_id()
    if not project_id or not _project_memory_enabled():
        return "Project memory is unavailable in the current chat."
    if not can_edit_project(project_id, user_id):
        return "Only project owners and editors can update shared project memory."
    if not (new_memory or "").strip():
        return "Provide the updated project fact."
    if not any(
        entry.get("memory_id") == memory_id and int(entry.get("version", -1)) == int(version)
        for entry in _current_turn_project_memory_results(state)
    ):
        return "Project memory update blocked: search for this exact memory ID and version during the current request first."
    return update_memory_by_id(
        user_id=user_id,
        project_id=project_id,
        memory_id=memory_id,
        new_memory=new_memory,
        version=version,
        updated_by=user_id,
    )


@tool
def forget_project_fact(
    state: Annotated[dict[str, Any], InjectedState],
    memory_id: str,
    version: int,
) -> str:
    """Soft-delete a project memory retrieved during the current chat turn."""
    project_id = get_current_project_id()
    user_id = get_current_user_id()
    if not project_id or not _project_memory_enabled():
        return "Project memory is unavailable in the current chat."
    if not can_edit_project(project_id, user_id):
        return "Only project owners and editors can delete shared project memory."
    if not any(
        entry.get("memory_id") == memory_id and int(entry.get("version", -1)) == int(version)
        for entry in _current_turn_project_memory_results(state)
    ):
        return "Project memory deletion blocked: search for this exact memory ID and version during the current request first."
    return delete_memory_by_id(
        user_id=user_id,
        project_id=project_id,
        memory_id=memory_id,
        version=version,
        deleted_by=user_id,
    )
