"""Explicit summary outcomes and normalized language selection."""

from dataclasses import dataclass
from typing import Literal

SummaryStatus = Literal["completed", "partial", "error", "clarification"]


def normalize_language(language: str | None) -> str:
    value = (language or "auto").strip().casefold()
    if value in {"", "auto", "original", "default", "ngôn ngữ gốc"}:
        return "auto"
    if value in {"vi", "vietnamese", "tiếng việt", "tieng viet"}:
        return "Vietnamese"
    if value in {"en", "english", "tiếng anh", "tieng anh"}:
        return "English"
    return value


@dataclass(frozen=True)
class SummaryResult:
    text: str
    status: SummaryStatus = "completed"

    def artifact(self) -> dict[str, str]:
        """Machine-readable routing metadata, separate from user-visible text."""
        return {"kind": "document_summary", "status": self.status}
