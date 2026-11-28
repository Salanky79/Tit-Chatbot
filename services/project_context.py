"""Build bounded cross-chat context for a project-scoped agent request."""

from __future__ import annotations

import config as cfg
from config import MAX_PROJECT_CONTEXT_CHARS

from bson import ObjectId

from storage.mongo_client import get_collection
from storage.projects import search_project_memories





# =============================================================================
# 01. Ngữ cảnh giới hạn từ các chat khác và bộ nhớ Project
# =============================================================================

def build_project_context(
    project_id: str,
    current_thread_id: str,
    query: str,
    memory_mode: str = cfg.DEFAULT_MEMORY_MODE,
) -> str:
    """Tạo ngữ cảnh giới hạn từ chat gần đây và memory của Project."""
    # Cách ly ngữ cảnh nếu ID không hợp lệ hoặc Project đang bật chế độ chỉ nhớ trong chat hiện tại
    if not ObjectId.is_valid(project_id) or memory_mode == "current_chat_only":
        return ""
    project_oid = ObjectId(project_id)
    # Loại trừ current_thread_id vì lịch sử của thread đang chat đã được LangGraph tự nạp
    conversations = list(
        get_collection("conversations").find(
            {"project_id": project_oid, "thread_id": {"$ne": current_thread_id}},
            {"thread_id": 1, "title": 1},
        ).sort("updated_at", -1).limit(cfg.PROJECT_CONTEXT_THREADS) # tối đa 12 cuộc trò chuyện khác
    )
    thread_ids = [row["thread_id"] for row in conversations if row.get("thread_id")]
    messages = []
    if thread_ids:
        messages = list(
            get_collection("chat_messages").find(
                {"thread_id": {"$in": thread_ids}},
                {"role": 1, "content": 1, "thread_id": 1, "created_at": 1, "sender_id": 1, "user_id": 1},
            ).sort("created_at", -1).limit(cfg.PROJECT_CONTEXT_MESSAGES) # tối đa 24 tin nhắn mới nhất từ chat_message
        )
        # Đảo lại từ cũ -> mới để LLM đọc đúng trình tự hội thoại
        messages.reverse()

    pieces: list[str] = []
    if messages:
        title_by_thread = {row["thread_id"]: row.get("title", "Project chat") for row in conversations}
        # Gom danh sách sender_id để query tên user 1 lần duy nhất, tránh N+1 query
        sender_ids = {
            sender_id
            for message in messages
            for sender_id in (message.get("sender_id"), message.get("user_id"))
            if isinstance(sender_id, ObjectId)
        }
        user_names = {
            str(user["_id"]): user.get("full_name") or user.get("username", "Project member")
            for user in get_collection("users").find(
                {"_id": {"$in": list(sender_ids)}}, {"full_name": 1, "username": 1}
            )
        } if sender_ids else {}
        dialogue = []
        for message in messages:
            # Gắn tên thành viên cho tin nhắn user để LLM biết ai đang nói trong Project chung
            if message.get("role") == "user":
                sender_id = message.get("sender_id") or message.get("user_id")
                role = f"User ({user_names.get(str(sender_id), 'Project member')})" if sender_id else "User"
            else:
                role = "Assistant"
            content = str(message.get("content", "")).strip()
            if content:
                dialogue.append(f"[{title_by_thread.get(message.get('thread_id'), 'Project chat')}] {role}: {content}")
        if dialogue:
            pieces.append("Recent conversations in this project:\n" + "\n".join(dialogue))

    # Bổ sung thêm các fact/quyết định dài hạn của riêng Project khớp ngữ nghĩa với câu hỏi
    memories = search_project_memories(project_id, query, limit=cfg.MEMORY_TOP_K)
    if memories:
        pieces.append("Saved project facts and decisions:\n" + "\n".join(f"- {item}" for item in memories))

    content = "\n\n".join(pieces)
    # Cắt từ cuối lên để ưu tiên giữ phần mới nhất và saved facts, giới hạn trong MAX_PROJECT_CONTEXT_CHARS
    return content[-MAX_PROJECT_CONTEXT_CHARS:] if content else ""
