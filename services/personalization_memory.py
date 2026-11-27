"""Service xử lý Memory Summary và cập nhật bộ nhớ cá nhân bằng LLM."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from prompts.personalization import (
    MEMORY_SUMMARY_DEFAULT_LANGUAGE_RULE,
    MEMORY_SUMMARY_SYSTEM_PROMPT,
    MEMORY_UPDATE_SYSTEM_PROMPT,
)
from services.token_limit_handler import invoke_with_rate_limit_retry
from storage.memories import (
    delete_memory_by_id,
    get_user_memories,
    save_memory_entry,
    update_memory_by_id,
)
from utils.usage_logging import log_token_usage



# =============================================================================
# 01. Schema kế hoạch cập nhật bộ nhớ
# =============================================================================

class MemoryActionItem(BaseModel):
    """Một thao tác thêm, sửa hoặc xóa bộ nhớ cá nhân do LLM đề xuất."""

    # Loại thao tác LLM muốn thực hiện: "add" (thêm mới), "update" (sửa bản ghi cũ), "delete" (xóa bản ghi cũ)
    action: Literal["add", "update", "delete"]
    # ID của bản ghi trong MongoDB cần sửa/xóa (bỏ trống khi action == "add")
    memory_id: str = Field(default="")
    # Số phiên bản hiện tại của bản ghi để chống ghi đè xung đột (optimistic locking)
    version: int = Field(default=1)
    # Nội dung văn bản mới sẽ lưu vào DB (dùng cho "add" và "update")
    new_memory: str = Field(default="")


class MemoryActionPlan(BaseModel):
    """Kế hoạch cập nhật bộ nhớ dựa trên yêu cầu bằng ngôn ngữ tự nhiên của user."""

    # Danh sách các thao tác cần chạy tuần tự lên MongoDB
    actions: list[MemoryActionItem] = Field(default_factory=list)
    # Câu tóm tắt ngắn gọn kết quả để hiển thị cho người dùng trên giao diện
    note: str = Field(default="")



# =============================================================================
# 02. Model và chuẩn hóa dữ liệu LLM
# =============================================================================

def _make_memory_llm(max_tokens: int = 700) -> Any:
    """Khởi tạo ChatGroq client gọn nhẹ cho tác vụ tóm tắt và cập nhật bộ nhớ."""
    from langchain_groq import ChatGroq

    # Đọc tên model từ biến môi trường; nếu không cấu hình thì fallback sang gpt-oss-20b
    model_name = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"
    # Dùng temperature thấp (0.2) để AI bám sát dữ liệu thật, không bịa thông tin; tắt streaming vì chỉ cần kết quả trọn gói
    return ChatGroq(
        model=model_name,
        temperature=0.2,
        streaming=False,
        max_tokens=max_tokens,
    )


def _extract_text(response: Any) -> str:
    """Trích xuất chuỗi text thuần từ đối tượng phản hồi của LLM."""
    # Phản hồi của LangChain thường là AIMessage có thuộc tính .content; nếu là string thô thì lấy trực tiếp
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    # Một số model trả content dạng list các block (ví dụ: [{"type": "text", "text": "..."}]) -> ghép text lại
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        ).strip()
    return str(content or "").strip()


def _build_summary_system_prompt(language: str | None = None) -> str:
    """Tạo system prompt tóm tắt bộ nhớ theo ngôn ngữ chỉ định hoặc tự khớp ngôn ngữ dữ liệu."""
    clean_lang = (language or "").strip()
    if clean_lang:
        rule = f"Write the entire summary (including section headings) in `{clean_lang}`."
    else:
        rule = MEMORY_SUMMARY_DEFAULT_LANGUAGE_RULE
    return MEMORY_SUMMARY_SYSTEM_PROMPT.format(language_rule=rule)



# =============================================================================
# 03. Tóm tắt hồ sơ bộ nhớ cá nhân
# =============================================================================

def summarize_user_memories(
    user_id: str,
    *,
    memories: list[dict[str, Any]] | None = None,
    llm: Any | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Lấy toàn bộ long_term_memory cá nhân theo user_id và gọi LLM tóm tắt thành hồ sơ dễ đọc."""
    # Bước 1: Nếu chưa được truyền sẵn danh sách memories thì query toàn bộ bộ nhớ cá nhân của user từ MongoDB
    # query="" + allow_full_fallback=True đảm bảo lấy hết mọi bản ghi active thay vì lọc semantic top-K
    if memories is None:
        loaded = get_user_memories(user_id=user_id, query="", allow_full_fallback=True)
        memories = loaded.get("memories", [])

    # Bước 2: Nếu user chưa có ghi nhớ nào -> trả về thông báo mặc định ngay, không gọi LLM (đỡ tốn token)
    if not memories:
        return {
            "summary": "Chưa có thông tin bộ nhớ cá nhân nào được lưu.",
            "memories": [],
            "count": 0,
        }

    # Bước 3: Gom các câu memory thành danh sách gạch đầu dòng sạch để đưa vào prompt cho LLM
    fact_lines = [
        f"- {item.get('memory', '')}"
        for item in memories
        if str(item.get("memory") or "").strip()
    ]
    client = llm or _make_memory_llm(max_tokens=700)
    messages = [
        {"role": "system", "content": _build_summary_system_prompt(language)},
        {"role": "user", "content": "Saved personal memories:\n" + "\n".join(fact_lines)},
    ]

    # Bước 4: Gọi LLM tóm tắt kèm cơ chế tự động retry khi gặp rate limit (429) của Groq
    response = invoke_with_rate_limit_retry(
        lambda: client.invoke(messages),
        max_retries=2,
        operation="Memory Summary",
    )
    # Ghi log số lượng token đã tiêu thụ cho tác vụ Memory Summary
    model_name = getattr(client, "model_name", None) or getattr(client, "model", "Groq")
    log_token_usage(str(model_name), "Memory Summary", response)

    # Bước 5: Rút trích văn bản Markdown tóm tắt; nếu LLM trả rỗng thì fallback về danh sách gạch đầu dòng gốc
    summary_text = _extract_text(response) or "\n".join(fact_lines)
    return {
        "summary": summary_text,
        "memories": memories,
        "count": len(memories),
    }



# =============================================================================
# 04. Phân tích kế hoạch và áp dụng thao tác có kiểm tra
# =============================================================================

def _parse_action_plan(result: Any) -> MemoryActionPlan:
    """Chuẩn hóa kết quả structured output từ LLM về đối tượng MemoryActionPlan."""
    # Trường hợp 1: LangChain trả thẳng đúng schema Pydantic MemoryActionPlan
    if isinstance(result, MemoryActionPlan):
        return result
    # Trường hợp 2: Trả về dict JSON đã parse sẵn -> validate lại theo MemoryActionPlan
    if isinstance(result, dict):
        return MemoryActionPlan.model_validate(result)
    # Trường hợp 3: Đối tượng Pydantic khác có hỗ trợ model_dump()
    if hasattr(result, "model_dump"):
        return MemoryActionPlan.model_validate(result.model_dump())
    # Trường hợp 4: LLM trả chuỗi JSON thô (đôi khi bọc trong markdown ```json ... ```) -> bóc fence rồi parse JSON
    text = _extract_text(result)
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return MemoryActionPlan.model_validate_json(text)


def apply_llm_memory_instruction(
    user_id: str,
    instruction: str,
    *,
    llm: Any | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Dùng LLM phân tích yêu cầu người dùng để thêm/sửa/xóa long_term_memory theo đúng version."""
    # Bước 1: Kiểm tra đầu vào không được rỗng
    clean_instruction = (instruction or "").strip()
    if not clean_instruction:
        raise ValueError("Vui lòng nhập nội dung yêu cầu cập nhật bộ nhớ.")

    # Bước 2: Tải toàn bộ bộ nhớ cá nhân hiện có và tạo bảng map {memory_id -> version}
    # Bảng valid_versions giúp xác thực ID tồn tại và truyền đúng version thực tế từ DB (tránh để LLM đoán/bịa)
    loaded = get_user_memories(user_id=user_id, query="", allow_full_fallback=True)
    current_memories = loaded.get("memories", [])
    valid_versions = {
        str(item.get("memory_id")): int(item.get("version", 1))
        for item in current_memories
        if item.get("memory_id")
    }

    # Bước 3: Đóng gói yêu cầu tự nhiên của user + danh sách bộ nhớ hiện tại thành JSON gửi LLM phân tích
    client = llm or _make_memory_llm(max_tokens=800)
    payload = {
        "instruction": clean_instruction,
        "existing_memories": [
            {
                "memory_id": item.get("memory_id"),
                "version": int(item.get("version", 1)),
                "memory": item.get("memory", ""),
            }
            for item in current_memories
        ],
    }
    messages = [
        {"role": "system", "content": MEMORY_UPDATE_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    # Bước 4: Ép LLM trả JSON đúng cấu trúc MemoryActionPlan (ưu tiên json_schema; nếu provider báo 400 thì lùi về json_mode)
    def _invoke_plan() -> MemoryActionPlan:
        if hasattr(client, "with_structured_output"):
            try:
                bound = client.with_structured_output(MemoryActionPlan, method="json_schema", strict=False)
                return _parse_action_plan(bound.invoke(messages))
            except Exception as exc:
                err_msg = str(exc).lower()
                if "json_validate_failed" in err_msg or "badrequesterror" in err_msg or "400" in err_msg:
                    bound_fallback = client.with_structured_output(MemoryActionPlan, method="json_mode")
                    return _parse_action_plan(bound_fallback.invoke(messages))
                raise
        return _parse_action_plan(client.invoke(messages))

    plan = invoke_with_rate_limit_retry(
        _invoke_plan,
        max_retries=2,
        operation="Memory AI Update",
    )

    # Bước 5: Duyệt từng hành động trong plan và thực thi xuống MongoDB
    applied: list[dict[str, str]] = []
    for item in plan.actions:
        if item.action == "add":
            new_text = item.new_memory.strip()
            if not new_text:
                continue
            # Lưu mục nhớ mới (không gắn với thread_id cụ thể nên truyền "")
            saved = save_memory_entry("", new_text, user_id=user_id)
            if saved:
                applied.append({"action": "add", "detail": f"Đã thêm: {new_text}"})
        elif item.action == "update":
            mem_id = item.memory_id.strip()
            new_text = item.new_memory.strip()
            # Chỉ sửa khi memory_id thực sự thuộc bộ nhớ của user hiện tại
            if mem_id not in valid_versions or not new_text:
                continue
            ver = valid_versions[mem_id]
            msg = update_memory_by_id(user_id=user_id, memory_id=mem_id, new_memory=new_text, version=ver)
            applied.append({"action": "update", "detail": msg})
        elif item.action == "delete":
            mem_id = item.memory_id.strip()
            # Chỉ xóa mềm khi memory_id hợp lệ, kèm version từ DB để khớp optimistic lock
            if mem_id not in valid_versions:
                continue
            ver = valid_versions[mem_id]
            msg = delete_memory_by_id(user_id=user_id, memory_id=mem_id, version=ver)
            applied.append({"action": "delete", "detail": msg})

    # Bước 6: Tóm tắt lại toàn bộ bộ nhớ sau khi đã thêm/sửa/xóa để UI cập nhật ngay bản mới nhất
    refreshed = summarize_user_memories(user_id, llm=client, language=language)
    return {
        "success": True,
        "applied_actions": applied,
        "note": plan.note,
        **refreshed,
    }
