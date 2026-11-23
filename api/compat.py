"""Legacy-module compatibility helpers for the split API routers."""

from __future__ import annotations

import sys
from typing import Any, Callable


def app_value(name: str, default: Any) -> Any:
    module = sys.modules.get("app")
    return getattr(module, name, default) if module is not None else default


def app_callable(name: str, default: Callable[..., Any]) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        return app_value(name, default)(*args, **kwargs)

    return call


class AppValueProxy:
    def __init__(self, name: str, default: Any):
        self.name = name
        self.default = default

    def __getattr__(self, name: str) -> Any:
        return getattr(app_value(self.name, self.default), name)

    def __bool__(self) -> bool:
        return bool(app_value(self.name, self.default))
