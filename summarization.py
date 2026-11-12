"""Map-reduce summarization for complete uploaded documents."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Sequence

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from token_utils import estimate_tokens
from usage_logging import log_token_usage

logger = logging.getLogger(__name__)


DEFAULT_INPUT_TOKEN_BUDGET = 3500
SUMMARY_OUTPUT_TOKENS = 900


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


def _parse_wait_time(err_msg: str) -> float | None:
    match = re.search(r"try again in (?:(\d+)\s*m\s*)?([\d\.]+)\s*s", err_msg, re.IGNORECASE)
    if match:
        minutes = float(match.group(1)) if match.group(1) else 0.0
        seconds = float(match.group(2))
        return minutes * 60.0 + seconds
    return None


def _invoke(
    llm,
    prompt: str,
    stage: str,
    group_number: int,
    max_retries: int | None = None,
    target_language: str = "auto",
) -> str:
    """Invoke the summarization LLM for a prompt unit.

    - Nếu chạm giới hạn TPM (8.000 token/phút): Tự động chờ (sleep) theo thời gian
      Groq yêu cầu và retry liên tục cho đến khi hoàn thành ("chạy khi nào xong thì thôi").
    - Chỉ khi chạm giới hạn TPD (200.000 token/ngày): Fail-fast ngay lập tức và ném lỗi
      rõ ràng để báo ra màn hình vì quota trong ngày đã hết.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            lang_rule = ""
            if target_language and target_language.lower() in ("vietnamese", "tiếng việt", "tieng viet"):
                lang_rule = (
                    "\n\nCRITICAL LANGUAGE REQUIREMENT: You MUST write the entire summary in fluent, natural Vietnamese (Tiếng Việt). "
                    "Translate and explain all section titles, roles, project descriptions, and key takeaways into Vietnamese. "
                    "Preserve proper nouns, names of people, universities, programming languages, and technical libraries intact."
                )
            elif target_language and target_language.lower() in ("auto", "original", "default", "ngôn ngữ gốc"):
                lang_rule = (
                    "\n\nCRITICAL LANGUAGE REQUIREMENT: Write the summary in the same primary language as the source document "
                    "(e.g., if the document is in English, write the summary in English; if in Vietnamese, write in Vietnamese)."
                )
            elif target_language:
                lang_rule = f"\n\nCRITICAL LANGUAGE REQUIREMENT: You MUST write the entire summary in {target_language}."

            response = llm.invoke([
                SystemMessage(content=(
                    "You summarize source material faithfully. Preserve the important "
                    "claims, definitions, figures, conditions, and conclusions. Avoid "
                    "repetition. Do not add facts or imply coverage beyond the supplied text."
                    + lang_rule
                )),
                HumanMessage(content=prompt),
            ])
            model_name = getattr(llm, "model_name", None) or getattr(llm, "model", "Groq-LLM")
            log_token_usage(str(model_name), f"Summarization ({stage}, group {group_number})", response)
            result = _content_text(response)
            if not result:
                raise RuntimeError(
                    f"Document summarization failed during {stage}, group {group_number}: the model returned an empty result."
                )
            return result
        except Exception as exc:
            err_str = str(exc).lower()
            is_rate_limit = (
                "429" in err_str
                or "rate_limit_exceeded" in err_str
                or "tokens per minute" in err_str
                or "tokens per day" in err_str
                or "tpm" in err_str
                or "tpd" in err_str
                or "rate limit" in err_str
                or "ratelimit" in err_str
            )

            if not is_rate_limit:
                logger.error(f"[SummarizationError] Failed during {stage}, group {group_number}: {exc}")
                raise RuntimeError(
                    f"Document summarization failed during {stage}, group {group_number}: {exc}"
                ) from exc

            # Kiểm tra nếu là chạm trần NGÀY (TPD - Tokens Per Day, ví dụ 200.000 token)
            is_daily_limit = (
                "tokens per day" in err_str
                or "tpd" in err_str
                or "daily" in err_str
                or "per day" in err_str
            )

            parsed_wait = _parse_wait_time(str(exc))

            # Nếu là giới hạn ngày (TPD) hoặc thời gian chờ quá lớn (> 3 phút - biểu hiện của quota ngày/giờ):
            if is_daily_limit or (parsed_wait is not None and parsed_wait > 180):
                logger.error(
                    f"[RateLimit-TPD] Summarization hit daily token limit (TPD 200.000) during {stage}, group {group_number}: {exc}"
                )
                raise RuntimeError(
                    f"Document summarization rate limit exceeded (Đã hết hạn mức 200.000 token trong ngày): {exc}"
                ) from exc

            # Nếu có cấu hình max_retries hữu hạn và đã vượt quá:
            if max_retries is not None and attempt > max_retries:
                logger.error(
                    f"[RateLimit-TPM] Exceeded maximum retries ({max_retries}) during {stage}, group {group_number}: {exc}"
                )
                raise RuntimeError(f"Document summarization rate limit exceeded: {exc}") from exc

            # Nếu là chạm trần PHÚT (8.000 TPM) hoặc RPM: tự động chờ rồi retry cho tới khi xong
            wait_seconds = max(1.0, (parsed_wait + 0.5)) if parsed_wait is not None else 6.0
            logger.warning(
                f"[RateLimit-TPM] Reached 8.000 TPM limit at {stage}, group {group_number} (Lần thử {attempt}). "
                f"Tự động đợi {wait_seconds:.2f}s để qua cửa sổ phút tiếp theo rồi chạy tiếp..."
            )
            time.sleep(wait_seconds)


def _build_groq_llm(model_name: str | None = None) -> ChatGroq:
    model = model_name or os.getenv("SUMMARIZATION_MODEL", "openai/gpt-oss-20b")
    return ChatGroq(
        model=model,
        temperature=0.1,
        max_tokens=SUMMARY_OUTPUT_TOKENS,
    )


def _execute_map_reduce(
    clean_documents: Sequence[Document],
    llm,
    effective_chunk_budget: int,
    target_language: str = "auto",
) -> str:
    units = [_format_document(doc) for doc in clean_documents if doc.page_content.strip()]
    map_groups = _groups_by_budget(units, effective_chunk_budget)

    lang_phrase = ""
    if target_language and target_language.lower() not in ("auto", "original", "default", "ngôn ngữ gốc"):
        lang_phrase = f" in {target_language}"

    # =========================================================================
    # TRƯỜNG HỢP 1: TÀI LIỆU NGẮN (vừa vặn trong 1 nhóm <= ngân sách token)
    # Gọi LLM đúng 1 lần duy nhất để tóm tắt và trả về luôn, KHÔNG cần Map-Reduce!
    # =========================================================================
    if len(map_groups) == 1:
        prompt = (
            f"Summarize this part of the document{lang_phrase}. Keep specific facts and caveats.\n\n"
            + "\n\n".join(map_groups[0])
        )
        return _invoke(llm, prompt, "map", 1, target_language=target_language)

    # =========================================================================
    # TRƯỜNG HỢP 2: TÀI LIỆU DÀI (nhiều hơn 1 nhóm > ngân sách token)
    # Bắt buộc phải thực hiện quy trình MAP - REDUCE đệ quy nhiều tầng
    # =========================================================================
    # Pha 1 (Map): Chạy tuần tự từng nhóm để tránh nghẽn trần Rate Limit (8.000 TPM)
    summaries: list[str] = []
    for group_number, group in enumerate(map_groups, start=1):
        prompt = (
            f"Summarize this part of the document{lang_phrase}. Keep specific facts and caveats.\n\n"
            + "\n\n".join(group)
        )
        summaries.append(_invoke(llm, prompt, "map", group_number, target_language=target_language))

    # Pha 2 (Reduce): Tóm tắt đệ quy các bản tóm tắt trung gian cho đến khi còn đúng 1 bản
    level = 1
    while len(summaries) > 1:
        reduced = []
        groups = _groups_by_budget(summaries, effective_chunk_budget)
        if len(groups) >= len(summaries):
            smaller_budget = max(256, effective_chunk_budget // 2)
            groups = _groups_by_budget(summaries, smaller_budget)
            if len(groups) >= len(summaries) and smaller_budget == 256:
                raise RuntimeError("Document summaries are too large to reduce safely.")

        for group_number, group in enumerate(groups, start=1):
            prompt = (
                f"Combine these summaries into one faithful summary{lang_phrase}. Preserve key "
                "claims, definitions, numbers, conditions, and conclusions; remove "
                "repetition and do not add facts.\n\n"
                + "\n\n".join(group)
            )
            reduced.append(
                _invoke(llm, prompt, f"reduce level {level}", group_number, target_language=target_language)
            )

        if len(reduced) >= len(summaries):
            raise RuntimeError("Document summarization could not make progress reducing intermediate summaries.")
        summaries = reduced
        level += 1

    return summaries[0]


def summarize_documents(
    documents: Sequence[Document],
    llm=None,
    *,
    input_token_budget: int | None = None,
    target_language: str = "auto",
) -> str:
    """Summarize supplied chunks with sequential recursive map-reduce passes.

    Tách context theo tác vụ:
    - Xác thực và cô lập hoàn toàn tài liệu: chỉ đưa các chunk và summary cần thiết
      vào pipeline Map-Reduce, tuyệt đối không gửi lịch sử chat.
    - Bất kỳ lỗi map/reduce nào đều raise error; không bao giờ trả về bản tóm tắt thiếu sót.
    """
    clean_documents = validate_and_isolate_summarization_context(documents)

    budget = input_token_budget or _input_budget()
    prompt_overhead = 150 + SUMMARY_OUTPUT_TOKENS
    effective_chunk_budget = max(256, budget - prompt_overhead) if budget > prompt_overhead else budget

    if llm is not None:
        return _execute_map_reduce(clean_documents, llm, effective_chunk_budget, target_language=target_language)

    primary_model = os.getenv("SUMMARIZATION_MODEL", "openai/gpt-oss-120b")
    active_llm = _build_groq_llm(primary_model)
    return _execute_map_reduce(clean_documents, active_llm, effective_chunk_budget, target_language=target_language)
