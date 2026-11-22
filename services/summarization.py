"""Map-reduce summarization for complete uploaded documents."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Sequence

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from prompts.summarization import (
    CUSTOM_LANGUAGE_RULE,
    EMERGENCY_REDUCE_PROMPT,
    FINAL_REDUCE_PROMPT,
    MAP_GROUP_PROMPT,
    ORIGINAL_LANGUAGE_RULE,
    REDUCE_PROMPT,
    SINGLE_SUMMARY_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    VIETNAMESE_LANGUAGE_RULE,
)
from utils.token_utils import estimate_tokens
from utils.usage_logging import log_token_usage
from services.token_limit_handler import (
    is_daily_token_limit,
    invoke_with_rate_limit_retry,
    assemble_partial_summary,
    DailyTokenLimitInterrupted,
)
from services.summary_jobs import SummaryJobStore

logger = logging.getLogger(__name__)


DEFAULT_INPUT_TOKEN_BUDGET = 3200
REDUCE_INPUT_TOKEN_BUDGET = 5500
MAP_SUMMARY_OUTPUT_TOKENS = 350
FINAL_SUMMARY_OUTPUT_TOKENS = 900
SUMMARY_OUTPUT_TOKENS = FINAL_SUMMARY_OUTPUT_TOKENS  # tương thích ngược

# Giới hạn số worker chạy song song khi tóm tắt các nhóm (2 workers là ngưỡng vàng an toàn với 8.000 TPM của Groq)
SUMMARIZATION_MAX_WORKERS = int(os.getenv("SUMMARIZATION_MAX_WORKERS", "2"))
# Thời gian chờ nhẹ giữa các lần submit task vào ThreadPool để tránh burst request cùng mili-giây
SUMMARIZATION_SUBMIT_STAGGER_S = float(os.getenv("SUMMARIZATION_SUBMIT_STAGGER_S", "0.3"))


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


def _invoke(
    llm: Any,
    prompt: str,
    stage: str,
    group_number: int,
    max_retries: int | None = None,
    target_language: str = "auto",
) -> str:
    """Invoke the summarization LLM for a prompt unit.

    Retry rate limit theo chính sách hiện có; nếu model lỗi hoặc hết quota ngày,
    lần lượt thử các model dự phòng đã cấu hình.
    """
    lang_rule = ""
    if target_language and target_language.lower() in ("vietnamese", "tiếng việt", "tieng viet"):
        lang_rule = VIETNAMESE_LANGUAGE_RULE
    elif target_language and target_language.lower() in ("auto", "original", "default", "ngôn ngữ gốc"):
        lang_rule = ORIGINAL_LANGUAGE_RULE
    elif target_language:
        lang_rule = CUSTOM_LANGUAGE_RULE.format(language=target_language)

    messages = [
        SystemMessage(content=SUMMARY_SYSTEM_PROMPT + lang_rule),
        HumanMessage(content=prompt),
    ]
    operation = f"Summarization ({stage}, group {group_number})"
    candidates = [llm]
    primary_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
    if isinstance(llm, ChatGroq):
        configured = os.getenv(
            "SUMMARIZATION_FALLBACK_MODELS",
            "openai/gpt-oss-20b,qwen/qwen3.8-27b,openai/gpt-oss-120b",
        )
        fallback_names = [name.strip() for name in configured.split(",") if name.strip()]
        fallback_names = [name for name in dict.fromkeys(fallback_names) if name != primary_name]
        candidates.extend(
            _build_groq_llm(name, max_tokens=getattr(llm, "max_tokens", MAP_SUMMARY_OUTPUT_TOKENS))
            for name in fallback_names
        )

    last_error: Exception | None = None
    for index, candidate in enumerate(candidates):
        model_name = getattr(candidate, "model_name", None) or getattr(candidate, "model", "Groq-LLM")
        try:
            response = invoke_with_rate_limit_retry(
                lambda candidate=candidate: candidate.invoke(messages),
                max_retries=max_retries,
                operation=operation,
            )
        except Exception as exc:
            last_error = exc
            if index + 1 < len(candidates):
                logger.warning(
                    "[ModelFailover] %s failed on %s; trying the next configured model: %s",
                    operation,
                    model_name,
                    exc,
                )
            continue

        result = _content_text(response)
        if result:
            log_token_usage(str(model_name), operation, response)
            return result
        last_error = RuntimeError("the model returned an empty result")

    if last_error is None:
        raise RuntimeError(f"Document summarization failed during {operation}.")
    if is_daily_token_limit(last_error):
        logger.error("[RateLimit-TPD] All configured models stopped during %s: %s", operation, last_error)
        raise RuntimeError(
            f"Document summarization rate limit exceeded (Đã hết hạn mức token trong ngày): {last_error}"
        ) from last_error
    logger.error("[SummarizationError] All configured models failed during %s: %s", operation, last_error)
    raise RuntimeError(f"Document summarization failed during {operation}: {last_error}") from last_error


def _build_groq_llm(model_name: str | None = None, max_tokens: int = FINAL_SUMMARY_OUTPUT_TOKENS) -> ChatGroq:
    model = model_name or os.getenv("SUMMARIZATION_MODEL") or "openai/gpt-oss-20b"
    return ChatGroq(
        model=model,
        temperature=0.1,
        max_tokens=max_tokens,
    )


def _invoke_groups_parallel(
    prompts: list[str],
    stage: str,
    llm,
    target_language: str,
    max_workers: int = SUMMARIZATION_MAX_WORKERS,
    job_store: SummaryJobStore | None = None,
    checkpoint_stage: str | None = None,
) -> list[str]:
    """Invoke _invoke() cho nhiều prompts song song bằng ThreadPoolExecutor.

    - Giữ nguyên thứ tự kết quả (index-based mapping).
    - Micro-stagger 0.1s giữa các submit để tránh burst TPM.
    - Nếu chỉ có 1 prompt → gọi _invoke() trực tiếp (không overhead thread).
    - Lưu kết quả thành công ngay; sau khi thu thập xong, ném lỗi nếu còn worker thất bại.
    """
    # Trường hợp biên: Nếu chỉ có đúng 1 nhóm prompt, gọi thẳng _invoke trên luồng hiện tại
    # để tránh lãng phí chi phí khởi tạo ThreadPoolExecutor
    saved = job_store.get_results(checkpoint_stage) if job_store and checkpoint_stage else {}
    results: list[str | None] = [saved.get(index) for index in range(len(prompts))]
    pending = [index for index in range(len(prompts)) if results[index] is None]
    if len(prompts) == 1 and pending:
        idx = pending[0]
        try:
            result = _invoke(llm, prompts[idx], stage, idx + 1, target_language=target_language)
        except Exception as exc:
            if is_daily_token_limit(exc):
                completed = [(i + 1, value) for i, value in enumerate(results) if value is not None]
                raise DailyTokenLimitInterrupted(completed, len(prompts), exc) from exc
            raise
        if job_store and checkpoint_stage:
            job_store.save_result(checkpoint_stage, idx, result)
        return [result]
    if not pending:
        return [value or "" for value in results]

    stagger = float(os.getenv("SUMMARIZATION_SUBMIT_STAGGER_S", str(SUMMARIZATION_SUBMIT_STAGGER_S)))
    # Mảng kết quả khởi tạo sẵn với độ dài cố định để gán kết quả theo đúng index gốc
    logger.info(f"[Parallel] Submitting {len(pending)} groups for stage '{stage}' with max_workers={max_workers}")

    # Sử dụng ThreadPoolExecutor để chạy các lời gọi LLM I/O-bound song song
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Dictionary ánh xạ từng future về vị trí ban đầu (original index) của prompt
        future_to_idx: dict = {}
        for idx in pending:
            prompt = prompts[idx]
            # Submit tác vụ tóm tắt nhóm idx + 1 vào thread pool
            future = executor.submit(
                _invoke, llm, prompt, stage, idx + 1, None, target_language
            )
            future_to_idx[future] = idx

            # Micro-stagger: Giãn cách 0.1s giữa các lần gửi request liên tiếp
            # để tránh 3-5 request đập vào API Groq tại cùng 1 mili-giây gây nghẽn burst rate
            if idx < len(prompts) - 1:
                time.sleep(stagger)

        # as_completed: Lấy kết quả ngay khi bất kỳ worker nào hoàn thành trước
        daily_limit_exc = None
        other_errors: list[Exception] = []
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                # Lưu kết quả đúng vào vị trí idx ban đầu để tài liệu không bị đảo lộn thứ tự
                results[idx] = future.result()
                if job_store and checkpoint_stage:
                    job_store.save_result(checkpoint_stage, idx, results[idx] or "")
            except Exception as exc:
                if is_daily_token_limit(exc):
                    daily_limit_exc = exc
                    logger.warning(
                        f"[DailyLimit] Stage '{stage}', group {idx+1} hit daily token limit: {exc}"
                    )
                else:
                    # Ghi nhận lỗi để vẫn lưu kết quả của các worker còn lại.
                    other_errors.append(exc)

        if other_errors:
            raise other_errors[0]

        # Nếu có bất kỳ worker nào chạm giới hạn ngày (TPD):
        if daily_limit_exc is not None:
            completed_groups = [(i + 1, r) for i, r in enumerate(results) if r is not None]
            raise DailyTokenLimitInterrupted(completed_groups, len(prompts), daily_limit_exc)

    return [value or "" for value in results]



def _execute_map_reduce(
    clean_documents: Sequence[Document],
    llm: Any,
    effective_chunk_budget: int,
    target_language: str = "auto",
    job_store: SummaryJobStore | None = None,
) -> str:
    units = [_format_document(doc) for doc in clean_documents if doc.page_content.strip()]
    map_groups = _groups_by_budget(units, effective_chunk_budget)

    lang_phrase = ""
    if target_language and target_language.lower() in ("vietnamese", "tiếng việt", "tieng viet"):
        lang_phrase = " in Vietnamese"
    elif target_language and target_language.lower() in ("english", "tiếng anh", "tieng anh"):
        lang_phrase = " in English"
    elif target_language and target_language.lower() not in ("auto", "original", "default", "ngôn ngữ gốc"):
        lang_phrase = f" in {target_language}"

    # Chuẩn bị model riêng cho từng pha:
    # - map_llm: output gọn (~350 tokens) để trích xuất ý chính, không phình to context
    # - final_llm: output đầy đủ (~900 tokens) để viết bản tóm tắt hoàn chỉnh cuối cùng
    model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
    if isinstance(llm, ChatGroq):
        map_llm = _build_groq_llm(model_name, max_tokens=MAP_SUMMARY_OUTPUT_TOKENS)
        final_llm = _build_groq_llm(model_name, max_tokens=FINAL_SUMMARY_OUTPUT_TOKENS)
    else:
        map_llm = getattr(llm, "bind", lambda **kw: llm)(max_tokens=MAP_SUMMARY_OUTPUT_TOKENS)
        final_llm = getattr(llm, "bind", lambda **kw: llm)(max_tokens=FINAL_SUMMARY_OUTPUT_TOKENS)

    source_name = "tài liệu"
    if clean_documents and hasattr(clean_documents[0], "metadata"):
        source_name = clean_documents[0].metadata.get("source") or "tài liệu"

    # =========================================================================
    # TRƯỜNG HỢP 1: TÀI LIỆU NGẮN (vừa vặn trong 1 nhóm <= ngân sách token)
    # Gọi LLM đúng 1 lần duy nhất để tóm tắt hoàn chỉnh và trả về luôn!
    # =========================================================================
    if len(map_groups) == 1:
        cached_single = job_store.get_results("single") if job_store else {}
        if 0 in cached_single:
            return cached_single[0]
        prompt = SINGLE_SUMMARY_PROMPT.format(
            language=lang_phrase, documents="\n\n".join(map_groups[0])
        )
        try:
            result = _invoke(final_llm, prompt, "map (single)", 1, target_language=target_language)
            if job_store:
                job_store.save_result("single", 0, result)
            return result
        except Exception as exc:
            if is_daily_token_limit(exc):
                logger.warning(f"[DailyLimit] Single map hit daily limit: {exc}")
                return assemble_partial_summary([], 1, filename=source_name, language=target_language)
            raise

    # =========================================================================
    # TRƯỜNG HỢP 2: TÀI LIỆU DÀI (nhiều hơn 1 nhóm > ngân sách token)
    # Bắt buộc phải thực hiện quy trình MAP - REDUCE đệ quy nhiều tầng
    # =========================================================================
    # Pha 1 (Map): Trích xuất ý chính súc tích (bullet points, số liệu, luận điểm)
    # Giới hạn output ~350 token giúp KHÔNG BAO GIỜ bị phình to context hay tràn TPM Groq
    map_prompts = [
        MAP_GROUP_PROMPT.format(language=lang_phrase, documents="\n\n".join(group))
        for group in map_groups
    ]
    logger.info(f"[MapReduce] Map phase: {len(map_prompts)} groups → running in parallel with max_workers={SUMMARIZATION_MAX_WORKERS}")

    try:
        summaries: list[str] = _invoke_groups_parallel(
            map_prompts, "map", map_llm, target_language,
            job_store=job_store, checkpoint_stage="map",
        )
    except DailyTokenLimitInterrupted as e:
        logger.warning(
            f"[DailyLimit] Map phase interrupted: completed {len(e.completed_groups)}/{e.total_groups} groups"
        )
        # Cứu hộ dữ liệu: Trả về bản tóm tắt bán phần (Partial Summary) của các nhóm đã xong!
        return assemble_partial_summary(
            e.completed_groups, e.total_groups, filename=source_name, language=target_language
        )

    # Pha 2 (Reduce): Gộp các bản tóm tắt trung gian
    # Sử dụng ngân sách gom nhóm lớn (5.500 token) để gộp được nhiều bản tóm tắt cùng lúc
    reduce_budget = int(os.getenv("SUMMARIZATION_REDUCE_INPUT_TOKENS", str(REDUCE_INPUT_TOKEN_BUDGET)))
    level = 1

    while len(summaries) > 1:
        groups = _groups_by_budget(summaries, reduce_budget)

        # Đảm bảo toán học: mỗi tầng Reduce luôn giảm đáng kể số lượng (ít nhất giảm 50%)
        if len(groups) >= len(summaries):
            step = 3
            groups = [summaries[i:i + step] for i in range(0, len(summaries), step)]

        # Nếu chỉ còn đúng 1 nhóm: Đây là bước Reduce CUỐI CÙNG để tạo bản tóm tắt hoàn chỉnh
        is_final_step = (len(groups) == 1)
        active_reduce_llm = final_llm if is_final_step else map_llm

        if is_final_step:
            reduce_prompts = [
                FINAL_REDUCE_PROMPT.format(language=lang_phrase, documents="\n\n".join(group))
                for group in groups
            ]
        else:
            reduce_prompts = [
                REDUCE_PROMPT.format(language=lang_phrase, documents="\n\n".join(group))
                for group in groups
            ]

        logger.info(f"[MapReduce] Reduce level {level}: {len(reduce_prompts)} groups (is_final_step={is_final_step})")
        try:
            reduced: list[str] = _invoke_groups_parallel(
                reduce_prompts, f"reduce level {level}", active_reduce_llm, target_language,
                job_store=job_store, checkpoint_stage=f"reduce:{level}",
            )
        except DailyTokenLimitInterrupted as e:
            logger.warning(
                f"[DailyLimit] Reduce level {level} interrupted. Fallback to Map summaries as partial result."
            )
            # Nếu đụng trần TPD ở Reduce, ta đã có toàn bộ summaries từ Pha Map!
            map_completed = [(i + 1, s) for i, s in enumerate(summaries)]
            return assemble_partial_summary(
                map_completed, len(summaries), filename=source_name, language=target_language
            )

        if len(reduced) >= len(summaries):
            # Cưỡng bức kết hợp nếu không thể giảm thêm
            step = 2
            emergency_groups = [reduced[i:i + step] for i in range(0, len(reduced), step)]
            emergency_prompts = [
                EMERGENCY_REDUCE_PROMPT.format(language=lang_phrase, documents="\n\n".join(g))
                for g in emergency_groups
            ]
            try:
                reduced = _invoke_groups_parallel(
                    emergency_prompts, f"reduce level {level} (converge)", final_llm, target_language,
                    job_store=job_store, checkpoint_stage=f"reduce:{level}:emergency",
                )
            except DailyTokenLimitInterrupted:
                map_completed = [(i + 1, s) for i, s in enumerate(summaries)]
                return assemble_partial_summary(
                    map_completed, len(summaries), filename=source_name, language=target_language
                )

        summaries = reduced
        level += 1

    return summaries[0]


def summarize_documents(
    documents: Sequence[Document],
    llm: Any | None = None,
    *,
    input_token_budget: int | None = None,
    target_language: str = "auto",
    job_context: dict[str, str] | None = None,
) -> str:
    """Summarize supplied chunks with sequential recursive map-reduce passes.

    Tách context theo tác vụ:
    - Xác thực và cô lập hoàn toàn tài liệu: chỉ đưa các chunk và summary cần thiết
      vào pipeline Map-Reduce, tuyệt đối không gửi lịch sử chat.
    - Bất kỳ lỗi map/reduce nào đều raise error; không bao giờ trả về bản tóm tắt thiếu sót.
    """
    clean_documents = validate_and_isolate_summarization_context(documents)

    budget = input_token_budget or _input_budget()
    prompt_overhead = 150 + MAP_SUMMARY_OUTPUT_TOKENS
    effective_chunk_budget = max(256, budget - prompt_overhead) if budget > prompt_overhead else budget

    active_llm = llm
    if active_llm is None:
        primary_model = (
            (job_context or {}).get("model")
            or os.getenv("SUMMARIZATION_MODEL")
            or "openai/gpt-oss-20b"
        )
        active_llm = _build_groq_llm(primary_model, max_tokens=FINAL_SUMMARY_OUTPUT_TOKENS)

    job_store = _create_job_store(
        clean_documents, job_context, target_language, effective_chunk_budget
    )
    return _run_summary(
        clean_documents, active_llm, effective_chunk_budget, target_language, job_store
    )


def _create_job_store(
    documents: list[Document],
    context: dict[str, str] | None,
    target_language: str,
    input_token_budget: int,
) -> SummaryJobStore | None:
    if not context:
        return None
    return SummaryJobStore.for_document(
        documents,
        context,
        target_language=target_language,
        input_token_budget=input_token_budget,
    )


def _run_summary(
    documents: list[Document],
    llm: Any,
    input_token_budget: int,
    target_language: str,
    job_store: SummaryJobStore | None,
) -> str:
    if job_store:
        cached = job_store.get_final()
        if cached is not None:
            return cached
        job_store.start()

    try:
        summary = _execute_map_reduce(
            documents,
            llm,
            input_token_budget,
            target_language=target_language,
            job_store=job_store,
        )
    except Exception as exc:
        if job_store:
            job_store.pause(str(exc))
        raise

    is_partial = "partial draft:" in summary.lower() or "hoàn thành:" in summary.lower()
    if job_store:
        if is_partial:
            job_store.pause("Daily model quota reached; summary can be resumed.")
        else:
            job_store.complete(summary)
    return summary
