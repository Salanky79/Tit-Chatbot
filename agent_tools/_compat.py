"""Compatibility lookup for callers that patch the legacy ``tools`` module."""

from __future__ import annotations

import sys
from typing import Any, Callable


def tool_attribute(name: str, default: Any) -> Any:
    module = sys.modules.get("tools")
    return getattr(module, name, default) if module is not None else default


def tool_callable(name: str, default: Callable[..., Any]) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        return tool_attribute(name, default)(*args, **kwargs)

    return call
