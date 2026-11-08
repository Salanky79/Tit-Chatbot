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
DEFAULT_MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "4"))
DEFAULT_MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "6000"))
DEFAULT_EXPECTED_OUTPUT_TOKENS = int(os.getenv("EXPECTED_OUTPUT_TOKENS", "1200"))
DEFAULT_MAX_TOOL_RESULT_TOKENS = int(os.getenv("MAX_TOOL_RESULT_TOKENS", "1800"))


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


def summarize_old_messages(
    old_turns: list[list[BaseMessage]],
    existing_summary: str = "",
    model_name: Optional[str] = None,
) -> str:
    """Tóm tắt các lượt trò chuyện cũ để bảo toàn ngữ cảnh cốt lõi.

    Nếu có LLM khả dụng, gọi model tạo tóm tắt súc tích (2-3 gạch đầu dòng).
    Nếu gặp lỗi mạng hoặc API không sẵn sàng, fallback sang trích xuất cục bộ.
    """
    dialogue = _extract_dialogue_text(old_turns)
    if not dialogue.strip():
        return existing_summary

    # Prompt tóm tắt
    prompt_content = (
        "Summarize the key facts, user preferences, names, and topics discussed "
        "in the conversation below into 2-3 concise bullet points. "
        "Preserve important context without unnecessary pleasantries.\n\n"
    )
    if existing_summary:
        prompt_content += f"Previous conversation summary:\n{existing_summary}\n\n"
    prompt_content += f"New conversation turns:\n{dialogue}"

    try:
        summary_model = model_name or os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        llm = ChatGroq(
            model=summary_model,
            temperature=0.2,
            max_tokens=500,
        )
        response = llm.invoke([
            SystemMessage(content=(
                "You are a concise conversation summarizer. Output only the key bullet points. "
                "Ensure every sentence and bullet point is completely finished."
            )),
            HumanMessage(content=prompt_content),
        ])
        content = getattr(response, "content", "")
        if isinstance(content, str) and content.strip():
            text = content.strip()
            # Loại bỏ liên từ/dấu phẩy lơ lửng nếu bị cắt cụt giữa chừng
            text = re.sub(r"[,;:\s]+(?:and|in|or|và|là|của|trong)[\s,.:;]*$", ".", text, flags=re.I)
            return text
    except Exception as exc:
        logger.warning("LLM conversation summarization failed: %s; using local extractive fallback.", exc)

    # Fallback trích xuất cục bộ nếu LLM không khả dụng
    extracted_lines = []
    if existing_summary:
        extracted_lines.append(existing_summary)
    for turn in old_turns:
        for msg in turn:
            if isinstance(msg, HumanMessage):
                text = str(getattr(msg, "content", ""))[:120]
                extracted_lines.append(f"- User asked: {text}")
            elif isinstance(msg, AIMessage):
                text = str(getattr(msg, "content", ""))[:140]
                if text.strip():
                    extracted_lines.append(f"- Assistant answered: {text}")
    return "\n".join(extracted_lines[-6:])


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
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
) -> Tuple[list[BaseMessage], str]:
    """Quy trình hoàn chỉnh theo đúng thứ tự 4 bước:

    (1) Trimming conversation history: giới hạn số lượt chat, tóm tắt lượt cũ.
    (2) Loại bỏ tool results cũ: loại bỏ web search / RAG cũ khỏi các lượt đã hoàn thành.
    (3) Tách context theo tác vụ: đảm bảo context chat chỉ chứa thông tin hội thoại.
    (4) Giới hạn token trước mỗi lần gọi LLM: tính toán tổng token và trim/chia nhỏ nếu quá giới hạn.

    Returns:
        (final_messages_for_llm, updated_conversation_summary)
    """
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

    # Bước 4: Giới hạn token trước khi gọi LLM
    combined_system_text = "\n\n".join(str(m.content) for m in system_messages)
    budgeted_messages = enforce_token_budget(
        messages=pruned_messages,
        system_prompt=combined_system_text,
        tools=tools,
        max_context_tokens=max_context_tokens,
        expected_output_tokens=expected_output_tokens,
    )

    final_messages = system_messages + budgeted_messages
    return final_messages, updated_summary
