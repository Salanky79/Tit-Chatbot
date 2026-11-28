"""Summarize one authorized upload, resuming only missing groups."""

from __future__ import annotations

import config as cfg

import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.documents import Document
from langchain_groq import ChatGroq

from prompts.summarization import FINAL_REDUCE_PROMPT, MAP_GROUP_PROMPT, REDUCE_PROMPT, SINGLE_SUMMARY_PROMPT
from services.language import normalize_summary_language
from services.summary_jobs import SummaryJobStore
from services.summary_model import FINAL_SUMMARY_OUTPUT_TOKENS, MAP_SUMMARY_OUTPUT_TOKENS, _build_groq_llm, _invoke
from services.summary_planning import (
    SummarySettings, groups_by_budget, language_phrase, page_texts, text_budget,
)
from services.token_limit_handler import DailyTokenLimitInterrupted, assemble_partial_summary, is_daily_token_limit
from utils.token_utils import estimate_tokens


@dataclass(frozen=True)
class SummaryResult:
    text: str
    status: Literal["completed", "partial", "error", "clarification"] = "completed"

    def artifact(self) -> dict[str, str]:
        return {"kind": "document_summary", "status": self.status}


def _invoke_groups_parallel(
    prompts: list[str], stage: str, llm: Any, target_language: str,
    *, settings: SummarySettings, job_store: SummaryJobStore | None = None,
) -> list[str]:
    results = job_store.get_results(stage) if job_store else {}
    missing = (i for i in range(len(prompts)) if i not in results)
    index = next(missing, None)
    errors = []
    next_submit = 0.0
    with ThreadPoolExecutor(max_workers=settings.workers) as pool:
        running = {}
        while running or (index is not None and not errors):
            can_submit = index is not None and not errors and len(running) < settings.workers
            delay = max(0.0, next_submit - time.monotonic()) if can_submit else None
            if can_submit and delay == 0:
                future = pool.submit(copy_context().run, _invoke, llm, prompts[index],
                                     stage, index + 1, target_language=target_language)
                running[future] = index
                next_submit = time.monotonic() + settings.stagger_seconds
                index = next(missing, None)
                continue
            if not running:
                time.sleep(delay)
                continue
            done, _ = wait(running, timeout=delay, return_when=FIRST_COMPLETED)
            for future in done:
                group = running.pop(future)
                try:
                    text = future.result()
                    results[group] = job_store.save_result(stage, group, text) if job_store else text
                except Exception as exc:
                    errors.append(exc)

    for error in errors:
        if not is_daily_token_limit(error):
            raise error
    if errors:
        completed = [(i + 1, results[i]) for i in sorted(results)]
        raise DailyTokenLimitInterrupted(completed, len(prompts), errors[0])
    return [results[i] for i in range(len(prompts))]


def _model_with_limit(llm: Any, limit: int) -> Any:
    if isinstance(llm, ChatGroq):
        return _build_groq_llm(llm.model_name, max_tokens=limit)
    return llm.bind(max_tokens=limit) if hasattr(llm, "bind") else llm


def _execute_map_reduce(documents: Sequence[Document], llm: Any, settings: SummarySettings,
                        target_language: str, job_store: SummaryJobStore | None) -> SummaryResult:
    texts = page_texts(documents)
    language = language_phrase(target_language)
    final_model = _model_with_limit(llm, FINAL_SUMMARY_OUTPUT_TOKENS)

    def finish(text, template, stage):
        prompt = template.format(language=language, documents=text)
        return SummaryResult(_invoke(final_model, prompt, stage, 1, target_language=target_language))

    def run_groups(groups, template, stage):
        prompts = [template.format(language=language, documents="\n\n".join(group)) for group in groups]
        return _invoke_groups_parallel(prompts, stage, map_model, target_language,
                                       settings=settings, job_store=job_store)

    stage = "single"
    summaries = []
    map_count = 0
    try:
        text = "\n\n".join(texts)
        single_budget = text_budget(settings.input_tokens, SINGLE_SUMMARY_PROMPT, target_language, FINAL_SUMMARY_OUTPUT_TOKENS)
        if estimate_tokens(text) <= single_budget: # chạy single prompt nếu đủ budget, tránh mất thời gian map-reduce
            return finish(text, SINGLE_SUMMARY_PROMPT, stage)

        stage = "map"
        map_model = _model_with_limit(llm, MAP_SUMMARY_OUTPUT_TOKENS)
        final_budget = text_budget(settings.reduce_tokens, FINAL_REDUCE_PROMPT, target_language, FINAL_SUMMARY_OUTPUT_TOKENS)
        if final_budget < 1:
            raise ValueError("Reduce budget cannot accommodate the prompt and reserved output.")
        map_budget = text_budget(settings.input_tokens, MAP_GROUP_PROMPT, target_language, MAP_SUMMARY_OUTPUT_TOKENS)
        groups = groups_by_budget(texts, map_budget) #logic trong planning => chia văn bản thành từng nhóm
        map_count = len(groups)
        summaries = run_groups(groups, MAP_GROUP_PROMPT, stage) # gọi llm chạy
        reduce_budget = text_budget(settings.reduce_tokens, REDUCE_PROMPT, target_language, MAP_SUMMARY_OUTPUT_TOKENS)
        level = 1
        while True:
            text = "\n\n".join(summaries)
            if estimate_tokens(text) <= final_budget:
                stage = "final" # kết thúc map-reduce, chạy final prompt
                return finish(text, FINAL_REDUCE_PROMPT, stage)
            stage = f"reduce:{level}"
            groups = groups_by_budget(summaries, reduce_budget)
            if len(groups) >= len(summaries):
                raise ValueError("Reduce budget cannot combine summaries safely.")
            summaries = run_groups(groups, REDUCE_PROMPT, stage)
            level += 1
    except Exception as exc:
        if not isinstance(exc, DailyTokenLimitInterrupted) and not is_daily_token_limit(exc):
            raise
        if summaries:
            draft = list(enumerate(summaries, 1))
            total = len(summaries)
        elif isinstance(exc, DailyTokenLimitInterrupted):
            draft, total = exc.completed_groups, exc.total_groups
        else:
            draft, total = [], 1
        text = assemble_partial_summary(draft, total, documents[0].metadata.get("source") or "document",
                                        target_language, stage=stage, map_count=map_count)
        return SummaryResult(text, status="partial")


def summarize_documents(
    documents: Sequence[Document] | Callable[[], Sequence[Document]], llm: Any | None = None, *,
    input_token_budget: int | None = None, target_language: str = cfg.SUMMARY_LANGUAGE,
    job_context: dict[str, str] | None = None,
) -> SummaryResult:
    language = normalize_summary_language(target_language)
    store = None
    if job_context:
        store = SummaryJobStore.open(job_context, language)
        if store.final_summary:
            return SummaryResult(store.final_summary)

    settings = SummarySettings.from_env(input_token_budget)
    chunks = documents() if callable(documents) else documents
    if llm is None:
        llm = _build_groq_llm((job_context or {}).get("model"))
    result = _execute_map_reduce(chunks, llm, settings, language, store)
    if not store:
        return result
    if result.status == "completed":
        return SummaryResult(store.complete(result.text))
    # A concurrent request may have finished while this one was waiting for quota.
    final = store.read_final()
    return SummaryResult(final) if final else result
