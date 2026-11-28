"""Deterministic input layout shared across summary attempts."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Sequence

from langchain_core.documents import Document

from services.summary_model import summary_system_prompt
from utils.token_utils import CHARS_PER_TOKEN, estimate_prompt_tokens, estimate_tokens

DEFAULT_INPUT_TOKEN_BUDGET = 3200
REDUCE_INPUT_TOKEN_BUDGET = 5500


@dataclass(frozen=True)
class SummarySettings:
    """Validate environment configuration per request, rather than at import."""

    input_tokens: int = DEFAULT_INPUT_TOKEN_BUDGET
    reduce_tokens: int = REDUCE_INPUT_TOKEN_BUDGET
    workers: int = 2
    stagger_seconds: float = 0.3

    def __post_init__(self) -> None:
        if self.input_tokens < 256 or self.reduce_tokens < 256:
            raise ValueError("Summary input and reduce budgets must be at least 256 tokens.")
        if self.workers < 1:
            raise ValueError("SUMMARIZATION_MAX_WORKERS must be positive.")
        if not math.isfinite(self.stagger_seconds) or self.stagger_seconds < 0:
            raise ValueError("Summary stagger must be finite and non-negative.")

    @classmethod
    def from_env(cls, input_tokens: int | None = None) -> SummarySettings:
        return cls(
            input_tokens=int(os.getenv("SUMMARIZATION_MAX_INPUT_TOKENS", "3200"))
            if input_tokens is None else input_tokens,
            reduce_tokens=int(os.getenv("SUMMARIZATION_REDUCE_INPUT_TOKENS", "5500")),
            workers=int(os.getenv("SUMMARIZATION_MAX_WORKERS", "2")),
            stagger_seconds=float(os.getenv("SUMMARIZATION_SUBMIT_STAGGER_S", "0.3")),
        )


def text_budget(limit: int, template: str, language: str, output_tokens: int) -> int:
    instruction = template.format(language=language_phrase(language), documents="")
    overhead = estimate_prompt_tokens(summary_system_prompt(language) + instruction)
    return limit - output_tokens - overhead - 32  # message framing and safety margin


def language_phrase(language: str) -> str:
    return "" if language == "auto" else f" in {language}"


def _verified_overlap(previous: Document | None, current: Document) -> int:
    if previous is None or current.metadata.get("page") is None:
        return 0
    if previous.metadata.get("page") != current.metadata["page"]:
        return 0
    start, prior = current.metadata.get("start_index"), previous.metadata.get("start_index")
    if not isinstance(start, int) or not isinstance(prior, int) or not 0 <= prior <= start:
        return 0
    offset = start - prior
    old, new = previous.page_content, current.page_content
    count = min(len(old) - offset, len(new))
    if count > 0 and old[offset:offset + count] == new[:count]:
        return count
    return 0


def page_texts(documents: Sequence[Document]) -> list[str]:
    """Rebuild runs of adjacent chunks when their offsets and shared text agree."""
    parts: list[str] = []
    fragments: list[str] = []
    previous = None

    for doc in documents:
        text = doc.page_content
        if not text or not text.strip():
            continue
        overlap = _verified_overlap(previous, doc)
        if overlap == len(text):
            continue
        if overlap:
            fragments.append(text[overlap:])
        else:
            if fragments:
                parts.append("".join(fragments))
            page = doc.metadata.get("page")
            fragments = [f"[Page {page}]\n" if page is not None else "", text]
        previous = doc

    if not fragments:
        raise ValueError("The selected document has no readable content to summarize.")
    parts.append("".join(fragments))
    return parts


def groups_by_budget(items: Sequence[str], budget: int) -> list[list[str]]:
    """Pack text without losing characters or exceeding the estimated text budget."""
    if budget < 1:
        raise ValueError("Text budget must be positive.")
    part_size = max(1, int(budget * CHARS_PER_TOKEN))
    groups: list[list[str]] = []
    current: list[str] = []
    for item in items:
        for offset in range(0, len(item), part_size):
            part = item[offset:offset + part_size]
            if current and estimate_tokens("\n\n".join([*current, part])) > budget:
                groups.append(current)
                current = []
            current.append(part)
    if current:
        groups.append(current)
    return groups
