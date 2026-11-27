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


# =============================================================================
# 01. Embedding và bộ lọc phạm vi dữ liệu
# =============================================================================

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


def _memory_scope_filter(user_id: str | None = None, project_id: str | None = None) -> dict[str, Any] | None:
    """Tạo điều kiện truy vấn memory cá nhân hoặc memory của một Project."""
    if project_id:
        project_oid = _to_object_id(project_id)
        return {"project_id": project_oid} if project_oid else None
    user_oid = _to_object_id(user_id)
    return {"user_id": user_oid} if user_oid else None



# =============================================================================
# 02. Tạo bộ nhớ và API lưu tương thích
# =============================================================================

def save_memory_entry(
    thread_id: str,
    memory: str,
    user_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    """Lưu memory vào long_term_memory theo user_id hoặc project_id."""
    scope_filter = _memory_scope_filter(user_id=user_id, project_id=project_id)
    if not scope_filter:
        return None
    if thread_id:
        conv = get_conversation(thread_id)
        if conv and conv.get("is_temporary"):
            return None
    clean_mem = (memory or "").strip()
    if not clean_mem:
        return None
    now = _now()
    doc: dict[str, Any] = {
        **scope_filter,
        "memory": clean_mem,
        "embedding": _generate_embedding(clean_mem),
        "is_deleted": False,
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    if project_id:
        doc["created_by"] = _to_object_id(user_id)
    if thread_id:
        doc["thread_id"] = thread_id
    result = get_collection("long_term_memory").insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


def save_memory(
    thread_id: str,
    memory: str,
    user_id: str | None = None,
    project_id: str | None = None,
) -> str:
    """Lưu memory cá nhân theo user_id hoặc memory Project theo project_id."""
    if not project_id and not _to_object_id(user_id):
        return "Memory is only available for signed-in users. Please sign in to save memories."
    if thread_id:
        conv = get_conversation(thread_id)
        if conv and conv.get("is_temporary"):
            return "Temporary Chat is active: memory is not persisted."
    doc = save_memory_entry(thread_id, memory, user_id=user_id, project_id=project_id)
    if not doc:
        return "Memory was not saved. Check the scope and memory text."
    return "Memory saved successfully."



# =============================================================================
# 03. Truy xuất bộ nhớ theo ngữ nghĩa
# =============================================================================

def get_user_memories(
    user_id: str | None = None,
    query: str = "",
    allow_full_fallback: bool = True,
    project_id: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Return semantic matches, or all active memories when no semantic match exists.

    Hoàn toàn loại bỏ Regex: dùng Cosine Similarity vector đa ngữ.
    Nếu query không rỗng mà điểm số tương đồng không đạt ngưỡng -> Fallback đẩy TOÀN BỘ
    ký ức cho LLM đọc và suy luận ngữ nghĩa.
    """
    scope_filter = _memory_scope_filter(user_id=user_id, project_id=project_id)
    if not scope_filter:
        return {
            "status": "invalid_scope",
            "source": "none",
            "memories": [],
        }

    coll = get_collection("long_term_memory")
    clean_query = (query or "").strip()
    base_filter = {**scope_filter, "is_deleted": False}
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
        top_matches = [doc for _, doc in scored_docs[:max(1, limit)]]
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



# =============================================================================
# 04. Cập nhật và xóa mềm theo phiên bản
# =============================================================================

def update_memory_by_id(
    user_id: str | None,
    memory_id: str,
    new_memory: str,
    version: int,
    project_id: str | None = None,
    updated_by: str | None = None,
) -> str:
    """Sửa memory theo scope, ID và version rồi tạo lại embedding."""
    scope_filter = _memory_scope_filter(user_id=user_id, project_id=project_id)
    mem_oid = _to_object_id(memory_id)
    if not scope_filter:
        return "Vui lòng đăng nhập để cập nhật bộ nhớ."
    if not mem_oid:
        return "Mã memory_id không hợp lệ."

    coll = get_collection("long_term_memory")
    now = _now()
    clean_mem = (new_memory or "").strip()
    if not clean_mem:
        return "Nội dung memory không được để trống."

    filter_q = {
        **scope_filter,
        "_id": mem_oid,
        "is_deleted": False,
        "version": int(version),
    }
    set_fields = {
        "memory": clean_mem,
        "embedding": _generate_embedding(clean_mem),
        "updated_at": now,
    }
    if project_id and updated_by:
        set_fields["updated_by"] = _to_object_id(updated_by)
    update_q = {
        "$set": set_fields,
        "$inc": {"version": 1},
    }

    res = coll.update_one(filter_q, update_q)
    if res.matched_count == 1:
        return f"Đã cập nhật thành công memory [{memory_id}] thành: '{new_memory.strip()}' (version mới: {int(version) + 1})."

    # Kiểm tra nguyên nhân không khớp
    existing = coll.find_one({"_id": mem_oid, **scope_filter})
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
    version: int,
    project_id: str | None = None,
    deleted_by: str | None = None,
) -> str:
    """Xóa mềm memory theo scope, ID và version."""
    scope_filter = _memory_scope_filter(user_id=user_id, project_id=project_id)
    mem_oid = _to_object_id(memory_id)
    if not scope_filter:
        return "Vui lòng đăng nhập để xóa bộ nhớ."
    if not mem_oid:
        return "Mã memory_id không hợp lệ."

    coll = get_collection("long_term_memory")
    now = _now()

    filter_q = {
        **scope_filter,
        "_id": mem_oid,
        "is_deleted": False,
        "version": int(version),
    }
    set_fields = {
        "is_deleted": True,
        "deleted_at": now,
        "updated_at": now,
    }
    if project_id and deleted_by:
        set_fields["deleted_by"] = _to_object_id(deleted_by)
    update_q = {
        "$set": set_fields,
        "$inc": {"version": 1},
    }

    res = coll.update_one(filter_q, update_q)
    if res.matched_count == 1:
        return f"Đã xóa thành công memory [{memory_id}]."

    existing = coll.find_one({"_id": mem_oid, **scope_filter})
    if not existing:
        return f"Lỗi: Không tìm thấy memory với ID [{memory_id}]."
    if existing.get("is_deleted", False):
        return f"Memory [{memory_id}] đã được xóa từ trước."
    current_ver = existing.get("version", 1)
    if current_ver != int(version):
        return f"Lỗi xung đột phiên bản: Memory [{memory_id}] hiện là version {current_ver}, khác với version {version} bạn cung cấp."

    return f"Lỗi: Không thể xóa memory [{memory_id}]."



# =============================================================================
# 05. Thao tác bộ nhớ từ cài đặt cá nhân
# =============================================================================

def delete_user_memory_by_id_for_settings(user_id: str, memory_id: str) -> bool:
    """Xóa mềm một bản ghi memory cá nhân từ giao diện Settings (không yêu cầu version)."""
    user_oid = _to_object_id(user_id)
    mem_oid = _to_object_id(memory_id)
    if not user_oid or not mem_oid:
        return False
    now = _now()
    res = get_collection("long_term_memory").update_one(
        {"_id": mem_oid, "user_id": user_oid, "is_deleted": False},
        {"$set": {"is_deleted": True, "deleted_at": now, "updated_at": now}, "$inc": {"version": 1}},
    )
    return res.matched_count == 1


def delete_all_personal_memories(user_id: str) -> int:
    """Xóa mềm toàn bộ bộ nhớ cá nhân (không đụng tới memory của Project) thuộc user_id."""
    user_oid = _to_object_id(user_id)
    if not user_oid:
        return 0
    now = _now()
    res = get_collection("long_term_memory").update_many(
        {
            "user_id": user_oid,
            "is_deleted": False,
            "$or": [{"project_id": {"$exists": False}}, {"project_id": None}],
        },
        {"$set": {"is_deleted": True, "deleted_at": now, "updated_at": now}, "$inc": {"version": 1}},
    )
    return int(res.modified_count)
