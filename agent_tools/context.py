"""Request-scoped context shared by all agent tools."""

from contextvars import ContextVar

_current_thread_id: ContextVar[str] = ContextVar("current_thread_id", default="default")
_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
_current_model_name: ContextVar[str] = ContextVar("current_model_name", default="")
_current_project_id: ContextVar[str] = ContextVar("current_project_id", default="")
_current_project_memory_mode: ContextVar[str] = ContextVar("current_project_memory_mode", default="")
_current_project_chat_enabled: ContextVar[bool] = ContextVar("current_project_chat_enabled", default=False)
_current_project_instructions: ContextVar[str] = ContextVar("current_project_instructions", default="")
_current_project_context: ContextVar[str] = ContextVar("current_project_context", default="")

SUMMARY_RESULT_PREFIX = "Summary of "
SUMMARY_RESULT_PREFIX_VI = "B\u1ea3n t\u00f3m t\u1eaft c\u1ee7a "


def set_current_thread_id(thread_id: str) -> None:
    _current_thread_id.set(thread_id)


def set_current_user_id(user_id: str | None) -> None:
    _current_user_id.set(user_id or "")


def set_current_model_name(model_name: str | None) -> None:
    _current_model_name.set(model_name or "")


def set_current_project(project_id: str | None, memory_mode: str | None = None) -> None:
    """Đặt Project ID và memory mode cho request agent hiện tại."""
    _current_project_id.set(project_id or "")
    _current_project_memory_mode.set(memory_mode or "")
    _current_project_chat_enabled.set(bool(project_id))


def set_project_prompt_context(instructions: str | None, project_context: str | None) -> None:
    """Đặt Project Instructions và context dùng trong prompt."""
    _current_project_instructions.set(instructions or "")
    _current_project_context.set(project_context or "")


def get_current_thread_id() -> str:
    return _current_thread_id.get()


def get_current_user_id() -> str:
    return _current_user_id.get()


def get_current_model_name() -> str:
    return _current_model_name.get()


def get_current_project_id() -> str:
    """Lấy Project ID của request agent hiện tại."""
    return _current_project_id.get()


def get_current_project_memory_mode() -> str:
    """Lấy memory mode của Project hiện tại."""
    return _current_project_memory_mode.get()


def is_project_chat() -> bool:
    """Cho biết request hiện tại có thuộc một Project hay không."""
    return _current_project_chat_enabled.get()


def get_current_project_instructions() -> str:
    """Lấy Project Instructions đã nạp cho request hiện tại."""
    return _current_project_instructions.get()


def get_current_project_context() -> str:
    """Lấy ngữ cảnh Project đã nạp cho request hiện tại."""
    return _current_project_context.get()
