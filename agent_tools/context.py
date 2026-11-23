"""Request-scoped context shared by all agent tools."""

from contextvars import ContextVar

_current_thread_id: ContextVar[str] = ContextVar("current_thread_id", default="default")
_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
_current_model_name: ContextVar[str] = ContextVar("current_model_name", default="")

SUMMARY_RESULT_PREFIX = "Summary of "
SUMMARY_RESULT_PREFIX_VI = "B\u1ea3n t\u00f3m t\u1eaft c\u1ee7a "


def set_current_thread_id(thread_id: str) -> None:
    _current_thread_id.set(thread_id)


def set_current_user_id(user_id: str | None) -> None:
    _current_user_id.set(user_id or "")


def set_current_model_name(model_name: str | None) -> None:
    _current_model_name.set(model_name or "")


def get_current_thread_id() -> str:
    return _current_thread_id.get()


def get_current_user_id() -> str:
    return _current_user_id.get()


def get_current_model_name() -> str:
    return _current_model_name.get()
