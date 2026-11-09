"""Map-reduce summarization for complete uploaded documents."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import os
from typing import Sequence

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from context_manager import validate_and_isolate_summarization_context


DEFAULT_INPUT_TOKEN_BUDGET = 18000
DEFAULT_MAX_WORKERS = 4
SUMMARY_OUTPUT_TOKENS = 1200


def estimate_tokens(text: str) -> int:
    """Conservative token estimate that also works for Vietnamese text.

    The estimate uses a character bound instead of requiring an extra tokenizer
    package. The budget is configurable and reserved for prompt instructions.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3))


def _input_budget() -> int:
    try:
        value = int(os.getenv("SUMMARIZATION_MAX_INPUT_TOKENS", DEFAULT_INPUT_TOKEN_BUDGET))
    except (TypeError, ValueError):
        value = DEFAULT_INPUT_TOKEN_BUDGET
    if value < 256:
        raise ValueError("SUMMARIZATION_MAX_INPUT_TOKENS must be at least 256.")
    return value


def _format_document(doc: Document) -> str:
    page = doc.metadata.get("page")
    label = f"[Page {page}]\n" if page is not None else ""
    return f"{label}{doc.page_content.strip()}"


def _groups_by_budget(items: Sequence[str], budget: int) -> list[list[str]]:
    """
    Đi lần lượt qua từng đoạn văn bản.
    Ước tính độ dài của đoạn.
    Nếu đoạn phù hợp với nhóm hiện tại, thêm vào nhóm.
    Nếu thêm vào sẽ vượt ngân sách, đóng nhóm hiện tại và mở nhóm mới.
    Nếu một đoạn riêng lẻ quá dài, chia đoạn đó nhỏ hơn trước.
    """
    """Pack whole text units into groups using estimated input tokens."""
    groups: list[list[str]] = []
    current: list[str] = []
    used = 0
    for item in items:
        item_cost = estimate_tokens(item)
        if item_cost > budget:
            # Split a single unusually large unit at character boundaries.
            chars_per_part = budget * 3
            parts = [item[i:i + chars_per_part] for i in range(0, len(item), chars_per_part)]
        else:
            parts = [item]
        for part in parts:
            cost = estimate_tokens(part)
            if current and used + cost > budget:
                groups.append(current)
                current, used = [], 0
            current.append(part)
            used += cost
    if current:
        groups.append(current)
    return groups


def _content_text(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            item if isinstance(item, str) else item.get("text", "")
            for item in content
            if isinstance(item, str) or (isinstance(item, dict) and item.get("type") == "text")
        ).strip()
    return ""


def _invoke(llm, prompt: str, stage: str, group_number: int) -> str:
    try:
        result = _content_text(llm.invoke([
            SystemMessage(content=(
                "You summarize source material faithfully. Preserve the important "
                "claims, definitions, figures, conditions, and conclusions. Avoid "
                "repetition. Do not add facts or imply coverage beyond the supplied text."
            )),
            HumanMessage(content=prompt),
        ]))
    except Exception as exc:
        raise RuntimeError(f"Document summarization failed during {stage}, group {group_number}: {exc}") from exc
    if not result:
        raise RuntimeError(f"Document summarization failed during {stage}, group {group_number}: the model returned an empty result.")
    return result


def summarize_documents(
    documents: Sequence[Document],
    llm=None,
    *,
    input_token_budget: int | None = None,
) -> str:
    """Summarize all supplied chunks with recursive map-reduce passes.

    Tách context theo tác vụ:
    - Xác thực và cô lập hoàn toàn tài liệu: chỉ đưa các chunk và summary cần thiết
      vào pipeline Map-Reduce, tuyệt đối không gửi lịch sử chat.
    - Bất kỳ lỗi map/reduce nào đều raise error; không bao giờ trả về bản tóm tắt thiếu sót.
    """
    clean_documents = validate_and_isolate_summarization_context(documents)

    if llm is None:
        summarization_model = os.getenv("SUMMARIZATION_MODEL", "openai/gpt-oss-20b")
        llm = ChatGroq(
            model=summarization_model,
            temperature=0.1,
            max_tokens=SUMMARY_OUTPUT_TOKENS,
        )

    budget = input_token_budget or _input_budget()
    # 4. Giới hạn token: Đảm bảo tổng chunk + prompt overhead + output không vượt quá budget
    prompt_overhead = 150 + SUMMARY_OUTPUT_TOKENS
    effective_chunk_budget = max(256, budget - prompt_overhead) if budget > prompt_overhead else budget

    units = [_format_document(doc) for doc in clean_documents if doc.page_content.strip()]
    map_groups = _groups_by_budget(units, effective_chunk_budget)

    # Pha 1 (Map): Nếu chỉ có 1 nhóm thì thực thi trực tiếp, nếu nhiều nhóm thì chạy song song đa luồng
    if len(map_groups) == 1:
        summaries = [_invoke(
            llm,
            "Summarize this part of the document. Keep specific facts and caveats.\n\n" + "\n\n".join(map_groups[0]),
            "map",
            1,
        )]
    else:
        try:
            configured_workers = int(os.getenv("SUMMARIZATION_MAX_WORKERS", str(DEFAULT_MAX_WORKERS)))
        except (TypeError, ValueError):
            configured_workers = DEFAULT_MAX_WORKERS
        max_workers = max(1, min(len(map_groups), configured_workers))

        def _map_worker(item: tuple[int, list[str]]) -> tuple[int, str]:
            idx, grp = item
            prompt = "Summarize this part of the document. Keep specific facts and caveats.\n\n" + "\n\n".join(grp)
            return idx, _invoke(llm, prompt, "map", idx)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            map_results = list(executor.map(_map_worker, enumerate(map_groups, start=1)))
        map_results.sort(key=lambda x: x[0])
        summaries = [res[1] for res in map_results]

    level = 1
    while len(summaries) > 1:
        reduced = []
        groups = _groups_by_budget(summaries, effective_chunk_budget)
        # If summaries are so large that grouping did not reduce their count,
        # lower the next pass budget to ensure the reduction makes progress.
        if len(groups) >= len(summaries):
            smaller_budget = max(256, effective_chunk_budget // 2)
            groups = _groups_by_budget(summaries, smaller_budget)
            if len(groups) >= len(summaries) and smaller_budget == 256:
                raise RuntimeError("Document summaries are too large to reduce safely.")

        if len(groups) == 1:
            reduced = [_invoke(
                llm,
                "Combine these summaries into one faithful summary. Preserve key "
                "claims, definitions, numbers, conditions, and conclusions; remove "
                "repetition and do not add facts.\n\n" + "\n\n".join(groups[0]),
                f"reduce level {level}",
                1,
            )]
        else:
            try:
                configured_workers = int(os.getenv("SUMMARIZATION_MAX_WORKERS", str(DEFAULT_MAX_WORKERS)))
            except (TypeError, ValueError):
                configured_workers = DEFAULT_MAX_WORKERS
            max_workers = max(1, min(len(groups), configured_workers))

            def _reduce_worker(item: tuple[int, list[str], int]) -> tuple[int, str]:
                idx, grp, lvl = item
                prompt = (
                    "Combine these summaries into one faithful summary. Preserve key "
                    "claims, definitions, numbers, conditions, and conclusions; remove "
                    "repetition and do not add facts.\n\n" + "\n\n".join(grp)
                )
                return idx, _invoke(llm, prompt, f"reduce level {lvl}", idx)

            worker_inputs = [(i, g, level) for i, g in enumerate(groups, start=1)]
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                reduce_results = list(executor.map(_reduce_worker, worker_inputs))
            reduce_results.sort(key=lambda x: x[0])
            reduced = [res[1] for res in reduce_results]

        if len(reduced) >= len(summaries):
            raise RuntimeError("Document summarization could not make progress reducing intermediate summaries.")
        summaries = reduced
        level += 1

    return summaries[0]
