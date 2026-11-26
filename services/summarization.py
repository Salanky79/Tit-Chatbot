"""Map-reduce summarization for complete uploaded documents."""

from __future__ import annotations

import logging
import hashlib
import math
import os
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Sequence

from langchain_core.documents import Document
from langchain_groq import ChatGroq

from prompts.summarization import (
    CUSTOM_LANGUAGE_RULE,
    ORIGINAL_LANGUAGE_RULE,
    SUMMARY_SYSTEM_PROMPT,
    VIETNAMESE_LANGUAGE_RULE,
    FINAL_REDUCE_PROMPT,
    MAP_GROUP_PROMPT,
    REDUCE_PROMPT,
    SINGLE_SUMMARY_PROMPT,
)
from utils.token_utils import CHARS_PER_TOKEN, estimate_tokens
from services.token_limit_handler import (
    is_daily_token_limit,
    assemble_partial_summary,
    DailyTokenLimitInterrupted,
)
from services.summary_jobs import SummaryJobStore
from services.summary_model import (
    _invoke, _build_groq_llm, MAP_SUMMARY_OUTPUT_TOKENS, FINAL_SUMMARY_OUTPUT_TOKENS,
)
from services.summary_result import SummaryResult, normalize_language
from contextvars import copy_context

logger = logging.getLogger(__name__)


DEFAULT_INPUT_TOKEN_BUDGET = 3200
REDUCE_INPUT_TOKEN_BUDGET = 5500
SUMMARY_OUTPUT_TOKENS = FINAL_SUMMARY_OUTPUT_TOKENS  # tương thích ngược

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

    @property
    def chunk_tokens(self) -> int:
        return max(256, self.input_tokens - 150 - MAP_SUMMARY_OUTPUT_TOKENS)


def validate_and_isolate_summarization_context(
    documents: Sequence[Document],
) -> list[Document]:
    """Pass only readable document text and necessary source metadata to summarization."""
    if not documents:
        raise ValueError("The selected document has no content to summarize.")

    isolated: list[Document] = []
    for document in documents:
        content = getattr(document, "page_content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        raw_metadata = getattr(document, "metadata", {}) or {}
        metadata = {
            "source": raw_metadata.get("source", "document"),
            "page": raw_metadata.get("page"),
            "chunk_index": raw_metadata.get("chunk_index"),
            "start_index": raw_metadata.get("start_index"),
        }
        isolated.append(Document(page_content=content.strip(), metadata=metadata))

    if not isolated:
        raise ValueError("The selected document has no readable content to summarize.")
    return isolated


def _format_document(doc: Document) -> str:
    page = doc.metadata.get("page")
    label = f"[Page {page}]\n" if page is not None else ""
    return f"{label}{doc.page_content.strip()}"


def _groups_by_budget(items: Sequence[str], budget: int) -> list[list[str]]:
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


def _invoke_groups_parallel(
    prompts: list[str], stage: str, llm: Any, target_language: str,
    *, settings: SummarySettings, job_store: SummaryJobStore | None = None,
) -> list[str]:
    """Run missing groups, persist successes, then report failures in input order."""
    saved = job_store.get_results(stage) if job_store else {}
    results = [saved.get(index) for index in range(len(prompts))]
    pending = [index for index, value in enumerate(results) if not value]
    errors: dict[int, Exception] = {}

    def collect(index: int, invoke) -> None:
        try:
            content = invoke()
            if job_store:
                job_store.save_result(stage, index, content)
            results[index] = content
        except Exception as exc:
            errors[index] = exc

    if len(pending) == 1:
        index = pending[0]
        collect(index, lambda: _invoke(llm, prompts[index], stage, index + 1, target_language=target_language))
    elif pending:
        with ThreadPoolExecutor(max_workers=settings.workers) as executor:
            futures = {}
            for position, index in enumerate(pending):
                if position and settings.stagger_seconds:
                    time.sleep(settings.stagger_seconds)
                future = executor.submit(
                    copy_context().run, _invoke, llm, prompts[index], stage, index + 1,
                    target_language=target_language,
                )
                futures[future] = index
            for future in as_completed(futures):
                collect(futures[future], future.result)

    for index in sorted(errors):
        if not is_daily_token_limit(errors[index]):
            raise errors[index]
    if errors:
        completed = [(index + 1, value) for index, value in enumerate(results) if value]
        raise DailyTokenLimitInterrupted(completed, len(prompts), errors[min(errors)])
    return [value for value in results if value is not None]


def _execute_map_reduce(
    documents: list[Document], llm: Any, settings: SummarySettings,
    target_language: str, job_store: SummaryJobStore | None,
) -> SummaryResult:
    language_phrase = "" if target_language == "auto" else f" in {target_language}"
    source = documents[0].metadata.get("source") or "document"
    map_groups = _groups_by_budget([_format_document(doc) for doc in documents], settings.chunk_tokens)
    model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
    if isinstance(llm, ChatGroq):
        map_llm = _build_groq_llm(model_name, max_tokens=MAP_SUMMARY_OUTPUT_TOKENS)
        final_llm = _build_groq_llm(model_name, max_tokens=FINAL_SUMMARY_OUTPUT_TOKENS)
    else:
        map_llm = llm.bind(max_tokens=MAP_SUMMARY_OUTPUT_TOKENS) if hasattr(llm, "bind") else llm
        final_llm = llm.bind(max_tokens=FINAL_SUMMARY_OUTPUT_TOKENS) if hasattr(llm, "bind") else llm

    def run(groups: list[list[str]], stage: str, prompt: str, model: Any) -> list[str]:
        prompts = [prompt.format(language=language_phrase, documents="\n\n".join(group)) for group in groups]
        return _invoke_groups_parallel(
            prompts, stage, model, target_language, settings=settings, job_store=job_store,
        )

    # If reduce stops, display the last fully completed level as a partial draft.
    summaries: list[str] = []
    try:
        if len(map_groups) == 1:
            return SummaryResult(run(map_groups, "single", SINGLE_SUMMARY_PROMPT, final_llm)[0])
        summaries = run(map_groups, "map", MAP_GROUP_PROMPT, map_llm)
        level = 1
        while len(summaries) > 1:
            groups = _groups_by_budget(summaries, settings.reduce_tokens)
            if len(groups) >= len(summaries):
                raise ValueError("Reduce budget cannot combine summaries safely; increase SUMMARIZATION_REDUCE_INPUT_TOKENS.")
            final = len(groups) == 1
            summaries = run(
                groups, f"reduce:{level}", FINAL_REDUCE_PROMPT if final else REDUCE_PROMPT,
                final_llm if final else map_llm,
            )
            level += 1
        return SummaryResult(summaries[0])
    except DailyTokenLimitInterrupted as exc:
        completed = list(enumerate(summaries, 1)) if summaries else exc.completed_groups
        total = len(summaries) if summaries else exc.total_groups
        return SummaryResult(
            assemble_partial_summary(completed, total, filename=source, language=target_language),
            status="partial",
        )


def summarize_documents(
    documents: Sequence[Document], llm: Any | None = None, *,
    input_token_budget: int | None = None, target_language: str = "auto",
    job_context: dict[str, str] | None = None,
) -> SummaryResult:
    """Summarize one document; expose completion status independently of prose."""
    clean = validate_and_isolate_summarization_context(documents)
    language = normalize_language(target_language)
    settings = SummarySettings.from_env(input_token_budget)
    store = SummaryJobStore.for_document(
        clean, job_context, target_language=language, input_token_budget=settings.chunk_tokens,
        pipeline_config={
            "reduce_tokens": settings.reduce_tokens,
            "map_output_tokens": MAP_SUMMARY_OUTPUT_TOKENS,
            "final_output_tokens": FINAL_SUMMARY_OUTPUT_TOKENS,
            "prompts": hashlib.sha256("".join((
                SINGLE_SUMMARY_PROMPT, MAP_GROUP_PROMPT, REDUCE_PROMPT, FINAL_REDUCE_PROMPT,
                SUMMARY_SYSTEM_PROMPT, ORIGINAL_LANGUAGE_RULE, VIETNAMESE_LANGUAGE_RULE,
                CUSTOM_LANGUAGE_RULE,
            )).encode()).hexdigest(),
            "chars_per_token": CHARS_PER_TOKEN,
        },
    ) if job_context else None
    if store:
        cached = store.get_final()
        if cached is not None:
            return SummaryResult(cached)

    try:
        if store:
            store.start()
        if llm is None:
            model = (job_context or {}).get("model") or os.getenv("SUMMARIZATION_MODEL")
            llm = _build_groq_llm(model, max_tokens=FINAL_SUMMARY_OUTPUT_TOKENS)
        result = _execute_map_reduce(clean, llm, settings, language, store)
        if store:
            if result.status == "partial":
                store.pause("Daily model quota reached; summary can be resumed.")
            else:
                store.complete(result.text)
        return result
    except Exception as exc:
        if store:
            try:
                store.pause(str(exc))
            except Exception:
                logger.exception("Could not persist summary failure for job %s", store.job_id)
        raise
