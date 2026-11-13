"""Centralized handler for token limits and graceful partial recovery.

Xử lý tập trung các tình huống khi LLM chạm ngưỡng giới hạn token trong ngày (TPD):
- Không ném exception hoặc vứt lỗi đỏ ra màn hình.
- Thu thập và in toàn bộ kết quả đang làm dở (Partial Result).
- Gắn thêm thông báo lịch sự, rõ ràng để người dùng vẫn sử dụng được dữ liệu đã sinh ra.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

logger = logging.getLogger(__name__)

_VIETNAMESE_LANGUAGES = {"vi", "vietnamese", "tiếng việt", "tieng viet"}


def _is_vietnamese(language: str) -> bool:
    return language.lower() in _VIETNAMESE_LANGUAGES


class DailyTokenLimitInterrupted(Exception):
    """Exception mang theo kết quả của các nhóm đã hoàn thành trước khi chạm trần token ngày."""

    def __init__(
        self,
        completed_groups: list[tuple[int, str]],
        total_groups: int,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(str(original_error or "Daily token limit reached"))
        self.completed_groups = completed_groups
        self.total_groups = total_groups
        self.original_error = original_error


def parse_wait_time(err_msg: str) -> float | None:
    """Trích xuất thời gian chờ (giây) từ thông báo lỗi của Groq nếu có."""
    match = re.search(r"try again in (?:(\d+)\s*m\s*)?([\d\.]+)\s*s", str(err_msg), re.IGNORECASE)
    if match:
        minutes = float(match.group(1)) if match.group(1) else 0.0
        seconds = float(match.group(2))
        return minutes * 60.0 + seconds
    return None


def is_daily_token_limit(exc: Exception | str) -> bool:
    """Kiểm tra xem lỗi có phải do chạm trần token trong ngày (TPD / daily quota) hay không.

    Dấu hiệu:
    - Chứa từ khóa 'tokens per day', 'tpd', 'daily', 'per day', 'quota_exceeded'
    - Hoặc thời gian chờ > 180s (biểu hiện của quota chu kỳ ngày/giờ, không phải rate limit phút thông thường).
    """
    err_str = str(exc).lower()
    if (
        "tokens per day" in err_str
        or "tpd" in err_str
        or "daily" in err_str
        or "per day" in err_str
        or "quota_exceeded" in err_str
        or "insufficient_quota" in err_str
    ):
        return True

    wait_time = parse_wait_time(err_str)
    if wait_time is not None and wait_time > 180.0:
        return True

    return False


def is_rate_limit(exc: Exception | str) -> bool:
    """Kiểm tra xem lỗi có phải là bất kỳ dạng rate limit nào từ Groq (TPM, RPM, TPD)."""
    err_str = str(exc).lower()
    return (
        "429" in err_str
        or "rate_limit_exceeded" in err_str
        or "tokens per minute" in err_str
        or "tokens per day" in err_str
        or "tpm" in err_str
        or "tpd" in err_str
        or "rate limit" in err_str
        or "ratelimit" in err_str
    )


def handle_partial_stream(
    accumulated_text: str,
    model_name: str,
    language: str = "vi",
) -> str:
    """Xử lý câu trả lời Stream chat đang làm dở khi chạm trần token ngày.

    - Nếu đã có accumulated_text -> giữ nguyên và gắn thông báo đuôi.
    - Nếu accumulated_text rỗng -> trả về câu thông báo lịch sự hướng dẫn chọn model khác.
    """
    is_vi = _is_vietnamese(language)
    clean_accumulated = (accumulated_text or "").strip()

    if clean_accumulated:
        if is_vi:
            notice = (
                f"\n\n---\n"
                f"*⚠️ **Thông báo gián đoạn:** Câu trả lời tạm dừng do mô hình `{model_name}` "
                f"đã đạt hạn mức token trong ngày (TPD limit) của Groq API. "
                f"Phần nội dung phía trên là kết quả đã được xử lý thành công. "
                f"Bạn có thể chọn mô hình khác ở danh sách góc dưới để tiếp tục trò chuyện.*"
            )
        else:
            notice = (
                f"\n\n---\n"
                f"*⚠️ **Notice:** The response was interrupted because model `{model_name}` "
                f"reached its daily token limit (TPD limit) on Groq API. "
                f"The content above is what was successfully generated. "
                f"You can switch to another model from the bottom selector to continue.*"
            )
        return f"{clean_accumulated}{notice}"

    # Trường hợp chưa sinh được chữ nào
    if is_vi:
        return (
            f"*⚠️ **Thông báo:** Mô hình `{model_name}` hiện tại đã đạt giới hạn hạn mức token trong ngày "
            f"(Daily Token Limit) của Groq API. "
            f"Bạn vui lòng chuyển sang mô hình khác (ví dụ: Qwen 3.8 27B hoặc GPT-OSS 120B) ở góc dưới "
            f"để tiếp tục trò chuyện nhé!*"
        )
    return (
        f"*⚠️ **Notice:** Model `{model_name}` has reached its daily token limit on Groq API. "
        f"Please select another model from the bottom selector to continue chatting!*"
    )


def assemble_partial_summary(
    completed_groups: Sequence[tuple[int, str]],
    total_groups: int,
    filename: str = "",
    language: str = "auto",
) -> str:
    """Ghép các phần tóm tắt đã hoàn thành thành một bản tóm tắt bán phần (Partial Draft) hoàn chỉnh.

    Bắt đầu bằng tiền tố hợp lệ để agent.py trả thẳng kết quả cho người dùng qua emit_summary.
    """
    is_vi = _is_vietnamese(language) or language.lower() == "auto"
    safe_filename = filename.strip() or "tài liệu"

    # Sắp xếp theo đúng thứ tự nhóm ban đầu (1-based index)
    sorted_groups = sorted(completed_groups, key=lambda item: item[0])
    completed_count = len(sorted_groups)

    if is_vi:
        prefix = f'Bản tóm tắt của tài liệu "{safe_filename}" (Một phần đã hoàn thành: {completed_count}/{total_groups} phần nội dung):\n\n'
        body_parts = []
        for idx, summary_text in sorted_groups:
            body_parts.append(f"### [Phần {idx}/{total_groups}]\n{summary_text.strip()}")

        body = "\n\n".join(body_parts)
        footer = (
            f"\n\n---\n"
            f"*⚠️ **Lưu ý:** Quá trình tóm tắt tài liệu tạm dừng do mô hình đã đạt giới hạn hạn mức token trong ngày "
            f"(TPD limit) của Groq API. Trên đây là bản tóm tắt đầy đủ của {completed_count}/{total_groups} phần "
            f"tài liệu đã hoàn thành. Các phần còn lại chưa thể xử lý. "
            f"Bạn có thể chọn mô hình khác ở góc dưới để tiếp tục.*"
        )
        return f"{prefix}{body}{footer}"

    # Tiếng Anh
    prefix = f'Summary of "{safe_filename}" (Partial draft: {completed_count}/{total_groups} sections completed):\n\n'
    body_parts = []
    for idx, summary_text in sorted_groups:
        body_parts.append(f"### [Section {idx}/{total_groups}]\n{summary_text.strip()}")

    body = "\n\n".join(body_parts)
    footer = (
        f"\n\n---\n"
        f"*⚠️ **Notice:** Document summarization paused because the model reached its daily token limit (TPD limit) on Groq API. "
        f"The sections above represent the {completed_count}/{total_groups} sections successfully completed. "
        f"You can switch to another model from the bottom selector to continue.*"
    )
    return f"{prefix}{body}{footer}"


def format_tool_limit_notice(
    tool_name: str,
    partial_info: str = "",
    language: str = "vi",
) -> str:
    """Tạo câu trả lời cứu trợ thân thiện khi các tool (Vision, RAG Search) gặp giới hạn ngày."""
    is_vi = _is_vietnamese(language)
    clean_partial = partial_info.strip()

    if is_vi:
        msg = (
            f"⚠️ Tính năng `{tool_name}` tạm thời không thể hoàn thành do mô hình "
            f"đã đạt giới hạn hạn mức token trong ngày của Groq API. "
        )
        if clean_partial:
            msg += f"\n\nDưới đây là thông tin thu thập được trước khi gián đoạn:\n{clean_partial}"
        else:
            msg += "\n\nBạn vui lòng thử lại sau hoặc chọn mô hình khác để tiếp tục."
        return msg

    msg = (
        f"⚠️ Feature `{tool_name}` could not complete because the model "
        f"reached its daily token limit on Groq API. "
    )
    if clean_partial:
        msg += f"\n\nHere is the information gathered before interruption:\n{clean_partial}"
    else:
        msg += "\n\nPlease try again later or switch to another model."
    return msg
