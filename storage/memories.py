"""Long-term memory persistence and semantic retrieval."""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from storage.mongo_client import get_collection as _mongo_get_collection
from storage._helpers import _now as _now_impl, _to_object_id as _to_object_id_impl, database_callable, logger
from storage.conversations import get_conversation as _get_conversation_impl

get_collection = database_callable("get_collection", _mongo_get_collection)
_now = database_callable("_now", _now_impl)
_to_object_id = database_callable("_to_object_id", _to_object_id_impl)
get_conversation = database_callable("get_conversation", _get_conversation_impl)

def _generate_embedding(text: str) -> list[float]:
    """Tạo vector nhúng 384 chiều đa ngữ (MiniLM-L12-v2) không dùng regex."""
    if not text or not text.strip():
        return []
    try:
        from services.rag import get_embeddings
        emb = get_embeddings()
        return emb.embed_query(text.strip())
    except Exception as exc:
        logger.warning(f"Could not generate embedding for memory: {exc}")
        return []


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Tính độ tương đồng Cosine giữa 2 vector số thực thuần."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def save_memory(thread_id: str, memory: str, user_id: str | None = None) -> str:
    """Lưu một đoạn bộ nhớ dài hạn vào collection ``long_term_memory``.

    Bộ nhớ dài hạn gắn với ``user_id`` và dùng chung xuyên suốt TẤT CẢ
    các cuộc trò chuyện của người dùng đó (Cross-chat memory).

    Chỉ lưu khi đã đăng nhập (có user_id). Guest không có long-term memory.

    Args:
        thread_id: ID luồng chat hiện tại (chỉ lưu để trace, không dùng để query).
        memory: Nội dung cần ghi nhớ.
        user_id: ID người dùng — bắt buộc để lưu.

    Returns:
        Chuỗi xác nhận thành công hoặc thông báo yêu cầu đăng nhập.
    """
    oid = _to_object_id(user_id)
    if not oid:
        return "Memory is only available for signed-in users. Please sign in to save memories."

    if thread_id:
        conv = get_conversation(thread_id)
        if conv and conv.get("is_temporary"):
            return "Temporary Chat is active: memory is not persisted."

    coll = get_collection("long_term_memory")
    now = _now()
    clean_mem = memory.strip()
    doc: dict[str, Any] = {
        "user_id": oid,
        "memory": clean_mem,
        "embedding": _generate_embedding(clean_mem),
        "is_deleted": False,
        "version": 1,  # tránh race_condition khi update
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    if thread_id:
        doc["thread_id"] = thread_id  # lưu để trace nguồn gốc, không dùng để query
    coll.insert_one(doc)
    return "Memory saved successfully."


def get_user_memories(
    user_id: str | None = None,
    query: str = "",
    allow_full_fallback: bool = True,
) -> dict[str, Any]:
    """Return semantic matches, or all active memories when no semantic match exists.

    Hoàn toàn loại bỏ Regex: dùng Cosine Similarity vector đa ngữ.
    Nếu query không rỗng mà điểm số tương đồng không đạt ngưỡng -> Fallback đẩy TOÀN BỘ
    ký ức cho LLM đọc và suy luận ngữ nghĩa.
    """
    oid = _to_object_id(user_id)
    if not oid:
        return {
            "status": "unauthenticated",
            "source": "none",
            "memories": [],
        }

    coll = get_collection("long_term_memory")
    clean_query = (query or "").strip()
    base_filter = {"user_id": oid, "is_deleted": False}
    projection = {
        "_id": 1,
        "memory": 1,
        "embedding": 1,
        "version": 1,
        "updated_at": 1,
    }

    def _format_docs(docs) -> list[dict[str, Any]]:
        return [
            {
                "memory_id": str(doc["_id"]),
                "memory": doc.get("memory", ""),
                "version": int(doc.get("version", 1)),
                "updated_at": doc.get("updated_at").isoformat()
                if hasattr(doc.get("updated_at"), "isoformat")
                else doc.get("updated_at"),
            }
            for doc in docs
        ]

    def _all_memories(source: str) -> dict[str, Any]:
        cursor = coll.find(base_filter, projection).sort(
            [("updated_at", -1), ("created_at", -1)]
        )
        memories = _format_docs(cursor)
        return {
            "status": "ok" if memories else "empty",
            "source": source,
            "memories": memories,
        }

    # 1. An empty query is the explicit full-memory read requested by the agent.
    if not clean_query:
        if not allow_full_fallback:
            return {
                "status": "full_read_already_used",
                "source": "none",
                "memories": [],
            }
        return _all_memories("all_requested")

    # 2. Semantic Search using Vector Cosine Similarity
    all_docs = list(coll.find(base_filter, projection))
    if not all_docs:
        return {"status": "empty", "source": "none", "memories": []}

    query_vec = _generate_embedding(clean_query)
    scored_docs: list[tuple[float, dict[str, Any]]] = []
    SIMILARITY_THRESHOLD = 0.38

    for doc in all_docs:
        doc_vec = doc.get("embedding")
        if not doc_vec:
            doc_vec = _generate_embedding(doc.get("memory", ""))
            if doc_vec:
                coll.update_one({"_id": doc["_id"]}, {"$set": {"embedding": doc_vec}})
        score = _cosine_similarity(query_vec, doc_vec)
        if score >= SIMILARITY_THRESHOLD:
            scored_docs.append((score, doc))

    if scored_docs:
        scored_docs.sort(key=lambda item: item[0], reverse=True)
        top_matches = [doc for _, doc in scored_docs[:5]]
        return {
            "status": "ok",
            "source": "semantic_match",
            "memories": _format_docs(top_matches),
        }

    # 3. Fallback khi semantic search không đạt ngưỡng: Đẩy toàn bộ ký ức cho LLM
    if not allow_full_fallback:
        return {
            "status": "full_read_already_used",
            "source": "none",
            "memories": [],
        }
    return _all_memories("fallback_no_semantic_match")


def update_memory_by_id(
    user_id: str | None,
    memory_id: str,
    new_memory: str,
    version: int
) -> str:
    """Bước 4: Sửa memory bằng ID + user_id + is_deleted + version."""
    oid = _to_object_id(user_id)
    mem_oid = _to_object_id(memory_id)
    if not oid:
        return "Vui lòng đăng nhập để cập nhật bộ nhớ."
    if not mem_oid:
        return "Mã memory_id không hợp lệ."

    coll = get_collection("long_term_memory")
    now = _now()
    clean_mem = new_memory.strip()

    filter_q = {
        "_id": mem_oid,
        "user_id": oid,
        "is_deleted": False,
        "version": int(version),
    }
    update_q = {
        "$set": {
            "memory": clean_mem,
            "embedding": _generate_embedding(clean_mem),
            "updated_at": now,
        },
        "$inc": {"version": 1},
    }

    res = coll.update_one(filter_q, update_q)
    if res.matched_count == 1:
        return f"Đã cập nhật thành công memory [{memory_id}] thành: '{new_memory.strip()}' (version mới: {int(version) + 1})."

    # Kiểm tra nguyên nhân không khớp
    existing = coll.find_one({"_id": mem_oid, "user_id": oid})
    if not existing:
        return f"Lỗi: Không tìm thấy memory với ID [{memory_id}]."
    if existing.get("is_deleted", False):
        return f"Lỗi: Memory [{memory_id}] đã bị xóa trước đó."
    current_ver = existing.get("version", 1)
    if current_ver != int(version):
        return f"Lỗi xung đột phiên bản: Memory [{memory_id}] hiện là version {current_ver}, khác với version {version} bạn cung cấp."

    return f"Lỗi: Không thể cập nhật memory [{memory_id}]."


def delete_memory_by_id(
    user_id: str | None,
    memory_id: str,
    version: int
) -> str:
    """Bước 4: Xóa mềm (soft-delete) memory bằng ID + user_id + is_deleted + version."""
    oid = _to_object_id(user_id)
    mem_oid = _to_object_id(memory_id)
    if not oid:
        return "Vui lòng đăng nhập để xóa bộ nhớ."
    if not mem_oid:
        return "Mã memory_id không hợp lệ."

    coll = get_collection("long_term_memory")
    now = _now()

    filter_q = {
        "_id": mem_oid,
        "user_id": oid,
        "is_deleted": False,
        "version": int(version),
    }
    update_q = {
        "$set": {
            "is_deleted": True,
            "deleted_at": now,
            "updated_at": now,
        },
        "$inc": {"version": 1},
    }

    res = coll.update_one(filter_q, update_q)
    if res.matched_count == 1:
        return f"Đã xóa thành công memory [{memory_id}]."

    existing = coll.find_one({"_id": mem_oid, "user_id": oid})
    if not existing:
        return f"Lỗi: Không tìm thấy memory với ID [{memory_id}]."
    if existing.get("is_deleted", False):
        return f"Memory [{memory_id}] đã được xóa từ trước."
    current_ver = existing.get("version", 1)
    if current_ver != int(version):
        return f"Lỗi xung đột phiên bản: Memory [{memory_id}] hiện là version {current_ver}, khác với version {version} bạn cung cấp."

    return f"Lỗi: Không thể xóa memory [{memory_id}]."
