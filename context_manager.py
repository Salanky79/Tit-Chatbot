"""context_manager.py - Quản lý ngữ cảnh và tối ưu hóa token cho Tit-Chatbot.

Module này giải quyết 4 yêu cầu cải thiện hiệu năng & độ tin cậy:
1. Trimming conversation history: Giới hạn số lượt chat đưa vào LLM, tóm tắt các lượt cũ.
2. Loại bỏ tool results cũ: Loại bỏ kết quả web search, RAG hoặc nội dung tool dài từ các lượt đã hoàn thành.
3. Tách context theo tác vụ: Đảm bảo các pipeline chuyên biệt (Map-Reduce PDF, Image Analysis, Follow-ups)
   hoàn toàn độc lập với lịch sử chat.
4. Giới hạn token trước mỗi lần gọi LLM: Tính toán chính xác ngân sách token (system prompt, lịch sử,
   tool schemas, tài liệu/tool results, output dự kiến) và tự động trim/cắt nhỏ khi vượt ngưỡng.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any, List, Optional, Sequence, Tuple

from langchain_core.documents import Document
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_groq import ChatGroq

logger = logging.getLogger(__name__)

# Cấu hình mặc định (có thể điều chỉnh qua biến môi trường)
DEFAULT_MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "2"))
DEFAULT_MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "6000"))
DEFAULT_EXPECTED_OUTPUT_TOKENS = int(os.getenv("EXPECTED_OUTPUT_TOKENS", "1200"))
DEFAULT_MAX_TOOL_RESULT_TOKENS = int(os.getenv("MAX_TOOL_RESULT_TOKENS", "1800"))

# Model helper dùng cho tóm tắt background để bảo vệ quota TPM của model chính
SUMMARY_BACKGROUND_MODEL = "openai/gpt-oss-20b"

# Ngân sách token động theo model nhằm ngăn chặn triệt để lỗi Groq Rate Limit 429 TPM
MODEL_TOKEN_BUDGETS = {
    # Groq free tier limit cho gpt-oss-120b chỉ có 8,000 TPM
    # Cần giới hạn tổng context <= 2,800 token để 2 lần gọi trong 1 lượt (tool call + answer) <= 7,000 TPM
    "openai/gpt-oss-120b": {
        "max_context_tokens": 2800,
        "max_tool_result_tokens": 1000,
        "expected_output_tokens": 800,
    },
    # gpt-oss-20b có giới hạn TPM cao (> 150,000 TPM)
    "openai/gpt-oss-20b": {
        "max_context_tokens": 6000,
        "max_tool_result_tokens": 1800,
        "expected_output_tokens": 1200,
    },
    "qwen/qwen3.8-27b": {
        "max_context_tokens": 5000,
        "max_tool_result_tokens": 1500,
        "expected_output_tokens": 1000,
    },
}


def get_model_token_budget(model_name: str | None) -> dict:
    """Lấy cấu hình ngân sách token phù hợp với từng mô hình nhằm ngăn chặn lỗi 429 Rate Limit."""
    if not model_name:
        return {
            "max_context_tokens": DEFAULT_MAX_CONTEXT_TOKENS,
            "max_tool_result_tokens": DEFAULT_MAX_TOOL_RESULT_TOKENS,
            "expected_output_tokens": DEFAULT_EXPECTED_OUTPUT_TOKENS,
        }
    normalized = model_name.strip()
    return MODEL_TOKEN_BUDGETS.get(normalized, {
        "max_context_tokens": DEFAULT_MAX_CONTEXT_TOKENS,
        "max_tool_result_tokens": DEFAULT_MAX_TOOL_RESULT_TOKENS,
        "expected_output_tokens": DEFAULT_EXPECTED_OUTPUT_TOKENS,
    })


# ===========================================================================
# 1. TOKEN COUNTING & ESTIMATION (Ước tính token nhất quán, an toàn)
# ===========================================================================

def estimate_tokens(text: str | None) -> int:
    """Ước tính số token an toàn cho văn bản (hỗ trợ tiếng Việt và tiếng Anh).

    Dùng cận trên len(text) / 3 để đảm bảo không bị vượt quá giới hạn token
    thực tế, đồng thời không cần kết nối mạng để tải bộ tokenizer ngoài.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(str(text)) / 3.0))


def estimate_message_tokens(message: BaseMessage) -> int:
    """Ước tính số token của một BaseMessage, bao gồm nội dung và tool calls."""
    # 4 token overhead cho mỗi message (role, formatting)
    total = 4

    content = getattr(message, "content", "")
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                total += estimate_tokens(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                total += estimate_tokens(str(text))

    # Tính token của tool_calls nếu có trong AIMessage
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        try:
            total += estimate_tokens(json.dumps(tool_calls, ensure_ascii=False))
        except Exception:
            total += estimate_tokens(str(tool_calls))

    return total


def estimate_tool_schemas_tokens(tool_list: Sequence[Any] | None) -> int:
    """Ước tính số token tiêu tốn bởi tool schemas khi bind vào ChatGroq."""
    if not tool_list:
        return 0

    total = 0
    for t in tool_list:
        name = getattr(t, "name", "")
        desc = getattr(t, "description", "")
        args = getattr(t, "args", None) or getattr(t, "args_schema", None)
        schema_dict = {
            "name": name,
            "description": desc,
            "parameters": str(args) if args is not None else {},
        }
        try:
            total += estimate_tokens(json.dumps(schema_dict, ensure_ascii=False)) + 12
        except Exception:
            total += estimate_tokens(name) + estimate_tokens(desc) + 20

    return total


def calculate_token_breakdown(
    messages: Sequence[BaseMessage],
    system_prompt: str,
    tools: Sequence[Any] | None = None,
    expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
) -> dict:
    """Tính toán chi tiết số lượng token của tất cả thành phần trước khi gọi LLM."""
    system_tokens = estimate_tokens(system_prompt)
    tools_tokens = estimate_tool_schemas_tokens(tools)

    history_tokens = 0
    tool_results_tokens = 0

    for msg in messages:
        msg_tok = estimate_message_tokens(msg)
        if isinstance(msg, ToolMessage):
            tool_results_tokens += msg_tok
        else:
            history_tokens += msg_tok

    total_tokens = (
        system_tokens
        + tools_tokens
        + history_tokens
        + tool_results_tokens
        + expected_output_tokens
    )

    return {
        "system_tokens": system_tokens,
        "tool_schemas_tokens": tools_tokens,
        "history_tokens": history_tokens,
        "tool_results_tokens": tool_results_tokens,
        "expected_output_tokens": expected_output_tokens,
        "total_tokens": total_tokens,
    }


# ===========================================================================
# 2. STEP 1: TRIMMING CONVERSATION HISTORY & SUMMARIZING OLD TURNS
# ===========================================================================

def group_messages_into_turns(messages: Sequence[BaseMessage]) -> list[list[BaseMessage]]:
    """Gom các tin nhắn thành các lượt (turns).

    Mỗi turn bắt đầu bằng một HumanMessage và chứa các tin nhắn phản hồi
    liên quan (AIMessage gọi tool, ToolMessage, AIMessage kết luận).
    Nếu có tin nhắn trước HumanMessage đầu tiên (như SystemMessage), tạo nhóm riêng.
    """
    turns: list[list[BaseMessage]] = []
    current_turn: list[BaseMessage] = []

    for msg in messages:
        if isinstance(msg, HumanMessage):
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        else:
            current_turn.append(msg)

    if current_turn:
        turns.append(current_turn)

    return turns


def _extract_dialogue_text(turns: list[list[BaseMessage]]) -> str:
    """Trích xuất hội thoại dạng văn bản ngắn gọn từ các turn cũ (loại bỏ tool dumps)."""
    lines: list[str] = []
    for turn in turns:
        for msg in turn:
            if isinstance(msg, HumanMessage):
                content = getattr(msg, "content", "")
                if content:
                    lines.append(f"User: {content}")
            elif isinstance(msg, AIMessage):
                # Chỉ lấy câu trả lời text của AI, bỏ qua tool_calls
                content = getattr(msg, "content", "")
                if isinstance(content, str) and content.strip():
                    lines.append(f"Assistant: {content.strip()}")
    return "\n".join(lines)


def _deduplicate_summary_lines(summary_text: str) -> str:
    """Loại bỏ các dòng/gạch đầu dòng bị lặp lại hoặc gần trùng lặp trong bản tóm tắt."""
    if not summary_text or not summary_text.strip():
        return ""
    lines = [line.strip() for line in summary_text.strip().split("\n") if line.strip()]
    unique_lines: list[str] = []
    seen_canonical = set()
    for line in lines:
        canonical = re.sub(r"^[-*•\d.)\s]+", "", line).strip().lower()
        canonical = re.sub(r"[.,:;!?'\"]+$", "", canonical)
        if canonical and canonical not in seen_canonical:
            seen_canonical.add(canonical)
            unique_lines.append(line)
    return "\n".join(unique_lines)


def summarize_old_messages(
    old_turns: list[list[BaseMessage]],
    existing_summary: str = "",
    model_name: Optional[str] = None,
) -> str:
    """Tóm tắt các lượt trò chuyện cũ để bảo toàn ngữ cảnh cốt lõi.

    - Luôn dùng mô hình gọn nhẹ (gpt-oss-20b) để không tiêu tốn token TPM của mô hình chính (như 120b).
    - Chỉ lưu thông tin người dùng, sở thích lâu dài và chủ đề chính.
    - Tuyệt đối không lưu mô tả ảnh tạm thời hoặc trích xuất OCR chi tiết.
    - Tự động khử trùng lặp (deduplicate) để không bị tích lũy nhân bản qua nhiều lượt.
    """
    dialogue = _extract_dialogue_text(old_turns)
    if not dialogue.strip():
        return _deduplicate_summary_lines(existing_summary)

    # Prompt tóm tắt chặt chẽ, chống ô nhiễm ngữ cảnh
    prompt_content = (
        "You are updating a compact conversation memory.\n"
        "Instructions:\n"
        "- Extract ONLY user identity, permanent preferences, and high-level conversational topics.\n"
        "- DO NOT summarize descriptions of images, photos, OCR dumps, or temporary uploaded files.\n"
        "- Output 2-3 concise, non-redundant bullet points.\n"
        "- Do NOT repeat any points already present in the existing summary.\n\n"
    )
    if existing_summary:
        prompt_content += f"Existing conversation summary:\n{existing_summary.strip()}\n\n"
    prompt_content += f"New dialogue turns to incorporate:\n{dialogue.strip()}"

    try:
        # Luôn ưu tiên dùng model 20b với timeout ngắn để không làm nghẽn TPM hoặc treo ứng dụng
        summary_model = SUMMARY_BACKGROUND_MODEL
        llm = ChatGroq(
            model=summary_model,
            temperature=0.1,
            max_tokens=400,
            request_timeout=4.0,
        )
        response = llm.invoke([
            SystemMessage(content=(
                "You are a concise conversation summarizer. Output only 2-3 key bullet points. "
                "Never describe temporary images or repeat existing bullet points."
            )),
            HumanMessage(content=prompt_content),
        ])
        log_token_usage(summary_model, "Conversation Trimming Summary", response)
        content = getattr(response, "content", "")
        if isinstance(content, str) and content.strip():
            text = content.strip()
            text = re.sub(r"[,;:\s]+(?:and|in|or|và|là|của|trong)[\s,.:;]*$", ".", text, flags=re.I)
            combined = f"{existing_summary.strip()}\n{text}" if existing_summary.strip() else text
            return _deduplicate_summary_lines(combined)
    except Exception as exc:
        logger.warning("LLM conversation summarization failed: %s; using local extractive fallback.", exc)

    # Fallback trích xuất cục bộ an toàn, loại bỏ nội dung media/file tạm
    extracted_lines = []
    if existing_summary:
        for line in existing_summary.split("\n"):
            if line.strip():
                extracted_lines.append(line.strip())
    for turn in old_turns:
        for msg in turn:
            if isinstance(msg, HumanMessage):
                text = str(getattr(msg, "content", ""))[:120]
                if "[Attached " in text:
                    continue
                extracted_lines.append(f"- User asked: {text}")
            elif isinstance(msg, AIMessage):
                text = str(getattr(msg, "content", ""))[:140]
                if text.strip() and not text.startswith("Image analysis of") and not text.startswith("Summary of"):
                    extracted_lines.append(f"- Assistant answered: {text}")
    return _deduplicate_summary_lines("\n".join(extracted_lines[-6:]))


def trim_conversation_history(
    messages: Sequence[BaseMessage],
    max_turns: int = DEFAULT_MAX_HISTORY_TURNS,
    existing_summary: str = "",
    model_name: Optional[str] = None,
) -> Tuple[list[BaseMessage], str]:
    """1. Trimming conversation history: Giới hạn số lượt chat đưa vào LLM.

    - Gom tin nhắn thành các turn.
    - Giữ lại tối đa `max_turns` lượt gần nhất.
    - Các lượt cũ hơn được tóm tắt súc tích và cập nhật vào `existing_summary`.
    - Trả về (recent_messages, updated_summary).
    """
    turns = group_messages_into_turns(messages)

    if len(turns) <= max_turns:
        # Số lượt chưa vượt quá giới hạn, giữ nguyên toàn bộ tin nhắn
        return list(messages), existing_summary

    # Tách các turn cũ cần tóm tắt và các turn gần đây cần giữ lại
    split_index = len(turns) - max_turns
    old_turns = turns[:split_index]
    recent_turns = turns[split_index:]

    # Tóm tắt các turn cũ
    updated_summary = summarize_old_messages(
        old_turns,
        existing_summary=existing_summary,
        model_name=model_name,
    )

    # Gom các tin nhắn trong các turn gần đây
    recent_messages: list[BaseMessage] = []
    for turn in recent_turns:
        recent_messages.extend(turn)

    return recent_messages, updated_summary


# ===========================================================================
# 3. STEP 2: LOẠI BỎ TOOL RESULTS CŨ (Prune Old Web Search / RAG Outputs)
# ===========================================================================

def prune_old_tool_results(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """2. Loại bỏ tool results cũ:

    Không đưa lại kết quả web search, RAG hoặc nội dung tool dài không liên quan
    từ các lượt đã hoàn thành vào request tiếp theo.

    Quy tắc:
    - Tìm HumanMessage cuối cùng.
    - Từ HumanMessage cuối cùng trở đi: Đây là LƯỢT ĐANG HOẠT ĐỘNG (active turn).
      Mọi ToolMessage và AIMessage(tool_calls) trong lượt này là kết quả công cụ
      vừa chạy, BẮT BUỘC giữ nguyên để LLM đọc và sinh câu trả lời.
    - Trước HumanMessage cuối cùng: Đây là CÁC LƯỢT ĐÃ HOÀN THÀNH (completed turns).
      Các kết quả tool trung gian (web search, RAG chunks, memory search) đã được
      Assistant tổng hợp xong trong câu trả lời hoàn chỉnh của lượt đó.
      -> Loại bỏ các ToolMessage và AIMessage(tool_calls) trung gian của các lượt cũ,
         chỉ giữ lại HumanMessage và AIMessage phản hồi hoàn chỉnh.
    """
    if not messages:
        return []

    # Tìm chỉ số của HumanMessage cuối cùng
    last_human_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            last_human_idx = i
            break

    if last_human_idx == -1:
        # Không có HumanMessage, trả về nguyên bản
        return list(messages)

    past_messages = messages[:last_human_idx]
    active_messages = messages[last_human_idx:]

    # Xử lý past_messages: loại bỏ ToolMessage và AIMessage chỉ chứa tool_calls
    cleaned_past: list[BaseMessage] = []
    for msg in past_messages:
        if isinstance(msg, ToolMessage):
            # Bỏ qua hoàn toàn ToolMessage từ các lượt cũ đã hoàn thành
            continue

        if isinstance(msg, AIMessage):
            # Nếu AIMessage chỉ có tool_calls mà không có text hữu ích, bỏ qua
            has_tool_calls = bool(getattr(msg, "tool_calls", None))
            content = getattr(msg, "content", "")
            has_text = bool(isinstance(content, str) and content.strip())

            if has_tool_calls and not has_text:
                continue

            if has_tool_calls and has_text:
                # Nếu có cả text và tool_calls, tạo bản sao AIMessage chỉ giữ lại text
                cleaned_past.append(AIMessage(content=content))
                continue

        cleaned_past.append(msg)

    # Ghép lại với active_messages (giữ nguyên kết quả tool của lượt hiện tại)
    return cleaned_past + list(active_messages)


# ===========================================================================
# 4. STEP 3: TÁCH CONTEXT THEO TÁC VỤ (Task Context Isolation)
# ===========================================================================

def validate_and_isolate_summarization_context(
    documents: Sequence[Document],
) -> list[Document]:
    """3. Tách context theo tác vụ:

    Khi tóm tắt PDF/tài liệu, đảm bảo CHỈ đưa các chunk văn bản và metadata
    trang cần thiết vào pipeline Map-Reduce.
    Tuyệt đối không đưa bất kỳ lịch sử chat, tin nhắn trò chuyện hay ngữ cảnh ngoài nào vào.
    """
    if not documents:
        raise ValueError("The selected document has no content to summarize.")

    isolated_chunks: list[Document] = []
    for doc in documents:
        # Đảm bảo chỉ lấy Document hợp lệ có nội dung văn bản
        content = getattr(doc, "page_content", "")
        if not isinstance(content, str) or not content.strip():
            continue

        # Chỉ giữ lại metadata cần thiết cho việc tóm tắt (page, source)
        raw_meta = getattr(doc, "metadata", {}) or {}
        clean_meta = {
            "source": raw_meta.get("source", "document"),
            "page": raw_meta.get("page"),
            "chunk_index": raw_meta.get("chunk_index"),
            "start_index": raw_meta.get("start_index"),
        }
        isolated_chunks.append(Document(page_content=content.strip(), metadata=clean_meta))

    if not isolated_chunks:
        raise ValueError("The selected document has no readable content to summarize.")

    return isolated_chunks


def is_isolated_document_task(messages: Sequence[BaseMessage]) -> bool:
    """3. Tách context theo tác vụ (Task Context Isolation):

    Kiểm tra xem yêu cầu hiện tại của người dùng có phải là một tác vụ tài liệu/tệp tin
    độc lập (như tóm tắt PDF, phân tích ảnh mới, tra cứu tài liệu vừa tải lên) hay không.

    Nếu là tác vụ tài liệu độc lập:
    - KHÔNG được đưa các tóm tắt trò chuyện cũ hay lịch sử lan man không liên quan vào.
    - Context chỉ tập trung 100% vào tài liệu / tệp tin và yêu cầu của người dùng.
    """
    if not messages:
        return False

    last_human_msg = None
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_human_msg = msg
            break

    if not last_human_msg:
        return False

    content = getattr(last_human_msg, "content", "")
    if not isinstance(content, str):
        content = str(content)

    lower_content = content.lower()

    # Dấu hiệu 1: Có gắn tệp đính kèm hoặc đường dẫn tệp
    attachment_markers = [
        "[attached file:",
        "[attached image:",
        "uploads/",
        ".pdf",
        ".docx",
        ".txt",
        ".csv",
        ".md",
    ]
    if any(marker in lower_content for marker in attachment_markers):
        return True

    # Nếu không có tệp đính kèm mới, kiểm tra xem tin nhắn trước có phải là bản tóm tắt vừa tạo không.
    # Nếu tin nhắn trước đã là bản tóm tắt và người dùng đang yêu cầu dịch / viết lại sang tiếng khác
    # (ví dụ: "hãy tóm tắt bằng tiếng việt", "dịch sang tiếng việt", "translate to english"):
    # ĐÂY LÀ HỘI THOẠI TIẾP NỐI, KHÔNG PHẢI TÁC VỤ ĐỘC LẬP MỚI -> Giữ lại ngữ cảnh để LLM dịch trực tiếp!
    has_recent_summary = False
    for msg in reversed(messages[:-1]):
        if isinstance(msg, AIMessage):
            content_str = str(getattr(msg, "content", ""))
            if "Summary of " in content_str or "Tóm tắt" in content_str:
                has_recent_summary = True
                break

    if has_recent_summary:
        followup_translation_markers = [
            "tiếng việt",
            "tiếng anh",
            "tieng viet",
            "tieng anh",
            "dịch",
            "dich",
            "translate",
            "vietnamese",
            "english",
            "bản dịch",
            "ban dich",
        ]
        if any(marker in lower_content for marker in followup_translation_markers):
            return False

    # Dấu hiệu 2: Từ khóa tóm tắt tài liệu hoặc yêu cầu đọc tài liệu cụ thể
    doc_keywords = [
        "tóm tắt file",
        "tóm tắt tài liệu",
        "tóm tắt văn bản",
        "tóm tắt bài",
        "tóm tắt sách",
        "tóm tắt pdf",
        "tóm tắt nội dung file",
        "summarize this file",
        "summarize the document",
        "summarize this document",
        "file này",
        "tài liệu này",
        "bài tiểu luận",
    ]
    if any(kw in lower_content for kw in doc_keywords):
        return True

    return False


# ===========================================================================
# 5. STEP 4: GIỚI HẠN TOKEN TRƯỚC MỖI LẦN GỌI LLM (Token Budget Enforcement)
# ===========================================================================

def enforce_token_budget(
    messages: Sequence[BaseMessage],
    system_prompt: str,
    tools: Sequence[Any] | None = None,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
    max_tool_result_tokens: int = DEFAULT_MAX_TOOL_RESULT_TOKENS,
) -> list[BaseMessage]:
    """4. Giới hạn token trước mỗi lần gọi LLM:

    Tính tổng token gồm:
    - system prompt
    - lịch sử hội thoại
    - tool schemas
    - tài liệu / kết quả tool (active tool results)
    - output dự kiến (expected output tokens)

    Nếu tổng vượt quá `max_context_tokens`:
    - Bước A: Rút gọn các ToolMessage đang hoạt động nếu quá dài (ví dụ kết quả search/RAG quá đồ sộ).
    - Bước B: Cắt bớt các tin nhắn lịch sử cũ từ đầu cho đến khi vừa vặn.
    - Bước C: Rút gọn tin nhắn nếu vẫn còn vượt giới hạn.
    """
    system_tokens = estimate_tokens(system_prompt)
    tools_tokens = estimate_tool_schemas_tokens(tools)

    # Ngân sách khả dụng cho toàn bộ danh sách messages
    available_for_messages = max(
        256,
        max_context_tokens - system_tokens - tools_tokens - expected_output_tokens,
    )

    result_messages = list(messages)

    # Bước A: Kiểm tra và thu gọn các ToolMessage trong lượt hiện tại nếu quá dài
    compacted_messages: list[BaseMessage] = []
    for msg in result_messages:
        if isinstance(msg, ToolMessage):
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                msg_tokens = estimate_tokens(content)
                if msg_tokens > max_tool_result_tokens:
                    # Cắt bớt nội dung tool result để vừa ngân sách
                    allowed_chars = max_tool_result_tokens * 3
                    truncated_content = (
                        content[:allowed_chars]
                        + "\n\n[...Nội dung kết quả công cụ đã được rút gọn để đảm bảo giới hạn token...]"
                    )
                    compacted_messages.append(
                        ToolMessage(
                            content=truncated_content,
                            tool_call_id=getattr(msg, "tool_call_id", ""),
                            name=getattr(msg, "name", None),
                        )
                    )
                    continue
        compacted_messages.append(msg)

    result_messages = compacted_messages

    # Bước B: Tính tổng token hiện tại của danh sách messages
    current_messages_tokens = sum(estimate_message_tokens(m) for m in result_messages)

    # Nếu vẫn vượt quá ngân sách khả dụng, cắt tỉa từ các tin nhắn cũ phía trước
    # Lưu ý: Tuyệt đối không cắt rời cặp AIMessage(tool_calls) và ToolMessage của lượt hiện tại!
    if current_messages_tokens > available_for_messages and len(result_messages) > 1:
        # Xác định vị trí lượt active hiện tại (từ HumanMessage cuối cùng)
        last_human_idx = -1
        for i in range(len(result_messages) - 1, -1, -1):
            if isinstance(result_messages[i], HumanMessage):
                last_human_idx = i
                break

        if last_human_idx > 0:
            # Lần lượt loại bỏ các tin nhắn cũ từ đầu danh sách
            while (
                len(result_messages) > 1
                and last_human_idx > 0
                and current_messages_tokens > available_for_messages
            ):
                removed = result_messages.pop(0)
                last_human_idx -= 1
                current_messages_tokens -= estimate_message_tokens(removed)

    return result_messages


# ===========================================================================
# 6. PIPELINE HỢP NHẤT: CHUẨN BỊ MESSAGES TRƯỚC KHI GỌI LLM
# ===========================================================================

def prepare_context_for_llm(
    messages: Sequence[BaseMessage],
    system_prompt: str,
    tools: Sequence[Any] | None = None,
    existing_summary: str = "",
    model_name: Optional[str] = None,
    max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS,
    max_context_tokens: Optional[int] = None,
    expected_output_tokens: Optional[int] = None,
) -> Tuple[list[BaseMessage], str]:
    """Quy trình hoàn chỉnh theo đúng thứ tự 4 bước:

    (1) Trimming conversation history: giới hạn số lượt chat, tóm tắt lượt cũ (loại bỏ lặp lại).
    (2) Loại bỏ tool results cũ: loại bỏ web search / RAG cũ khỏi các lượt đã hoàn thành.
    (3) Tách context theo tác vụ: khi người dùng tóm tắt PDF/file/image mới,
        cô lập hoàn toàn ngữ cảnh, KHÔNG inject tóm tắt trò chuyện hay ảnh cũ vào prompt.
    (4) Giới hạn token trước mỗi lần gọi LLM: tính toán tổng token và tự động điều chỉnh theo budget của model
        (đặc biệt gpt-oss-120b với giới hạn 8,000 TPM của Groq).

    Returns:
        (final_messages_for_llm, updated_conversation_summary)
    """
    # Lấy ngân sách token tương ứng với model
    model_budget = get_model_token_budget(model_name)
    budget_context = max_context_tokens if max_context_tokens is not None else model_budget["max_context_tokens"]
    budget_output = expected_output_tokens if expected_output_tokens is not None else model_budget["expected_output_tokens"]
    budget_tool = model_budget["max_tool_result_tokens"]

    # Bước 3 (Kiểm tra tác vụ tài liệu độc lập):
    # Nếu là tác vụ tài liệu (PDF, file, ảnh vừa tải lên, tóm tắt file):
    # CÔ LẬP HOÀN TOÀN context để tránh Prompt Pollution và lặp lại nội dung cũ
    if is_isolated_document_task(messages):
        # 1. Tìm HumanMessage cuối cùng đại diện cho yêu cầu tài liệu hiện tại
        last_human_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                last_human_idx = i
                break
        active_messages = list(messages[last_human_idx:]) if last_human_idx >= 0 else list(messages)

        # 2. Xây dựng SystemMessage chuyên biệt cho tác vụ độc lập, KHÔNG kèm conversation summary
        full_system = system_prompt.strip() + (
            "\n\nCRITICAL TASK ISOLATION INSTRUCTIONS:\n"
            "- You are executing an isolated document or file processing task requested in the user's latest message.\n"
            "- Focus exclusively on the document, file, or image provided and the user's specific request.\n"
            "- DO NOT mention, assume, or refer to any previous conversations, past topics, or previously discussed images.\n"
            "- If summarizing, synthesize only the facts and structure of the requested document."
        )

        # 3. Giới hạn token cho active turn
        budgeted_messages = enforce_token_budget(
            messages=active_messages,
            system_prompt=full_system,
            tools=tools,
            max_context_tokens=budget_context,
            expected_output_tokens=budget_output,
            max_tool_result_tokens=budget_tool,
        )

        final_messages = [SystemMessage(content=full_system)] + budgeted_messages
        return final_messages, existing_summary

    # --- Trường hợp hội thoại bình thường ---
    # Bước 1: Trimming conversation history
    trimmed_messages, updated_summary = trim_conversation_history(
        messages=messages,
        max_turns=max_history_turns,
        existing_summary=existing_summary,
        model_name=model_name,
    )

    # Bước 2: Loại bỏ tool results cũ từ các lượt đã hoàn thành
    pruned_messages = prune_old_tool_results(trimmed_messages)

    # Tạo phần SystemMessage duy nhất (kèm theo bản tóm tắt và chỉ dẫn chống lặp lại)
    full_system = system_prompt.strip()
    if updated_summary.strip():
        full_system += (
            "\n\n[CONTEXT FROM EARLIER CONVERSATION - FOR INTERNAL REFERENCE ONLY]\n"
            f"{updated_summary.strip()}\n"
            "[END OF CONTEXT]\n\n"
            "CRITICAL INSTRUCTIONS FOR BACKGROUND CONTEXT:\n"
            "- The context above is solely for your internal memory and reference.\n"
            "- DO NOT repeat, quote, or output this summary in your response.\n"
            "- If the user's latest message is a new question or about a different topic, "
            "answer it directly without mentioning past files or conversations."
        )

    system_messages: list[BaseMessage] = [SystemMessage(content=full_system)]

    # Bước 4: Giới hạn token trước khi gọi LLM theo budget
    combined_system_text = "\n\n".join(str(m.content) for m in system_messages)
    budgeted_messages = enforce_token_budget(
        messages=pruned_messages,
        system_prompt=combined_system_text,
        tools=tools,
        max_context_tokens=budget_context,
        expected_output_tokens=budget_output,
        max_tool_result_tokens=budget_tool,
    )

    final_messages = system_messages + budgeted_messages
    return final_messages, updated_summary


# =============================================================================
# THEO DÕI VÀ GHI LOG SỐ LƯỢNG TOKEN GROQ (TOKEN USAGE MONITORING)
# =============================================================================

_SESSION_TOKEN_STATS = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "calls_count": 0,
}


def extract_token_usage(response: Any) -> dict[str, int]:
    """Trích xuất số lượng token từ response của LangChain / ChatGroq.

    Hỗ trợ cả response.usage_metadata (chuẩn LangChain)
    và response.response_metadata['token_usage'] (từ Groq API raw).
    """
    if response is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        prompt = int(usage.get("input_tokens") or 0)
        completion = int(usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    resp_meta = getattr(response, "response_metadata", {}) or {}
    token_usage = resp_meta.get("token_usage", {}) or {}
    if isinstance(token_usage, dict) and token_usage:
        prompt = int(token_usage.get("prompt_tokens") or 0)
        completion = int(token_usage.get("completion_tokens") or 0)
        total = int(token_usage.get("total_tokens") or (prompt + completion))
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def log_token_usage(model_name: str, task: str, response: Any) -> dict[str, int]:
    """Ghi log chi tiết số lượng token đã tiêu thụ sau mỗi lần gọi ChatGroq API.

    - In ra Git Bash / Console dạng chuẩn: Model, Tác vụ, Prompt, Completion, Total, Tích lũy phiên.
    - Cập nhật số token tích lũy toàn phiên (_SESSION_TOKEN_STATS).
    - Trả về dictionary thông số token.
    """
    usage = extract_token_usage(response)
    prompt_tokens = usage["prompt_tokens"]
    completion_tokens = usage["completion_tokens"]
    total_tokens = usage["total_tokens"]

    if total_tokens > 0:
        _SESSION_TOKEN_STATS["prompt_tokens"] += prompt_tokens
        _SESSION_TOKEN_STATS["completion_tokens"] += completion_tokens
        _SESSION_TOKEN_STATS["total_tokens"] += total_tokens
        _SESSION_TOKEN_STATS["calls_count"] += 1

        session_total = _SESSION_TOKEN_STATS["total_tokens"]
        calls = _SESSION_TOKEN_STATS["calls_count"]

        log_msg = (
            f"[Groq-Usage] Model: '{model_name}' | Tác vụ: {task} | "
            f"Prompt: {prompt_tokens:,} | Output: {completion_tokens:,} | "
            f"Lần này: {total_tokens:,} tokens | "
            f"Tích lũy phiên ({calls} cuộc gọi): {session_total:,} tokens"
        )
        logger.info(log_msg)
        print(log_msg, flush=True)

    return usage

