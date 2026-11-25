"""MongoDB persistence for projects, memberships, and project-scoped memory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson import ObjectId

from storage.mongo_client import get_collection


VALID_MEMBER_ROLES = {"editor", "chat_only"}


def _oid(value: Any) -> ObjectId | None:
    """Chuyển ID hợp lệ sang MongoDB ObjectId."""
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


def _public(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Chuẩn hóa document MongoDB để trả về qua API."""
    if not doc:
        return None
    result = dict(doc)
    for key in ("_id", "owner_id", "project_id", "user_id", "invited_by", "created_by", "uploaded_by", "last_actor_id", "sender_id"):
        if isinstance(result.get(key), ObjectId):
            result[key] = str(result[key])
    for key in ("created_at", "updated_at", "accepted_at"):
        value = result.get(key)
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


def create_project(owner_id: str, name: str, description: str = "") -> dict[str, Any]:
    """Tạo Project mới thuộc sở hữu của user."""
    owner_oid = _oid(owner_id)
    clean_name = (name or "").strip()[:100]
    if not owner_oid:
        raise ValueError("Valid user is required to create a project.")
    if not clean_name:
        raise ValueError("Project name is required.")
    now = datetime.now(timezone.utc)
    doc = {
        "owner_id": owner_oid,
        "name": clean_name,
        "description": (description or "").strip()[:500],
        "instructions": "",
        "memory_mode": "project_only",
        "created_at": now,
        "updated_at": now,
    }
    result = get_collection("projects").insert_one(doc)
    doc["_id"] = result.inserted_id
    return _public(doc) or {}


def get_project(project_id: str) -> dict[str, Any] | None:
    """Lấy thông tin một Project theo ID."""
    project_oid = _oid(project_id)
    if not project_oid:
        return None
    return _public(get_collection("projects").find_one({"_id": project_oid}))


def list_projects_for_user(user_id: str) -> list[dict[str, Any]]:
    """Liệt kê Project user sở hữu hoặc đã tham gia."""
    user_oid = _oid(user_id)
    if not user_oid:
        return []
    membership_ids = [
        row["project_id"]
        for row in get_collection("project_members").find(
            {"user_id": user_oid, "status": "accepted"}, {"project_id": 1}
        )
        if row.get("project_id")
    ]
    cursor = get_collection("projects").find(
        {"$or": [{"owner_id": user_oid}, {"_id": {"$in": membership_ids}}]}
    ).sort("updated_at", -1)
    projects = []
    for doc in cursor:
        item = _public(doc) or {}
        item["role"] = "owner" if str(item.get("owner_id")) == str(user_oid) else "member"
        item["membership"] = get_membership(str(doc["_id"]), user_id)
        projects.append(item)
    return projects


def get_membership(project_id: str, user_id: str) -> dict[str, Any] | None:
    """Lấy quyền của user trong Project nếu membership đã được chấp nhận."""
    project_oid, user_oid = _oid(project_id), _oid(user_id)
    if not project_oid or not user_oid:
        return None
    project = get_collection("projects").find_one({"_id": project_oid}, {"owner_id": 1})
    if project and str(project.get("owner_id")) == str(user_oid):
        return {"project_id": str(project_oid), "user_id": str(user_oid), "role": "owner", "status": "accepted"}
    row = get_collection("project_members").find_one(
        {"project_id": project_oid, "user_id": user_oid, "status": "accepted"}
    )
    return _public(row)


def can_edit_project(project_id: str, user_id: str) -> bool:
    """Kiểm tra user có quyền owner hoặc editor hay không."""
    membership = get_membership(project_id, user_id)
    return bool(membership and membership.get("role") in {"owner", "editor"})


def update_project(project_id: str, user_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Cập nhật thông tin Project nếu user có quyền chỉnh sửa."""
    project_oid = _oid(project_id)
    if not project_oid or not can_edit_project(project_id, user_id):
        return None
    allowed: dict[str, Any] = {}
    if "name" in fields:
        name = str(fields["name"] or "").strip()[:100]
        if not name:
            raise ValueError("Project name is required.")
        allowed["name"] = name
    if "description" in fields:
        allowed["description"] = str(fields["description"] or "").strip()[:500]
    if "instructions" in fields:
        allowed["instructions"] = str(fields["instructions"] or "").strip()[:8000]
    if "memory_mode" in fields:
        mode = fields["memory_mode"]
        if mode not in {"project_only", "project_and_personal", "current_chat_only"}:
            raise ValueError("Invalid project memory mode.")
        allowed["memory_mode"] = mode
    if not allowed:
        return get_project(project_id)
    allowed["updated_at"] = datetime.now(timezone.utc)
    get_collection("projects").update_one({"_id": project_oid}, {"$set": allowed})
    return get_project(project_id)


def delete_project(project_id: str, user_id: str) -> dict[str, int] | None:
    """Xóa Project cùng chat, file, memory và checkpoint liên quan."""
    project_oid = _oid(project_id)
    user_oid = _oid(user_id)
    if not project_oid or not user_oid:
        return None
    project = get_collection("projects").find_one({"_id": project_oid, "owner_id": user_oid})
    if not project:
        return None
    conversations = list(get_collection("conversations").find({"project_id": project_oid}, {"thread_id": 1}))
    thread_ids = [str(row["thread_id"]) for row in conversations if row.get("thread_id")]
    checkpoints_deleted = 0
    try:
        from agent import delete_user_checkpoints
        checkpoints_deleted = delete_user_checkpoints(thread_ids)
    except Exception:
        pass
    for thread_id in thread_ids:
        get_collection("chat_messages").delete_many({"thread_id": thread_id})
        get_collection("conversations").delete_one({"thread_id": thread_id, "project_id": project_oid})
        jobs = get_collection("summary_jobs")
        job_ids = [row["_id"] for row in jobs.find({"context.thread_id": thread_id}, {"_id": 1})]
        if job_ids:
            get_collection("summary_job_checkpoints").delete_many({"job_id": {"$in": job_ids}})
        jobs.delete_many({"context.thread_id": thread_id})
        try:
            from services.rag import delete_thread_documents
            delete_thread_documents(thread_id)
        except Exception:
            pass
    files = list(get_collection("project_files").find({"project_id": project_oid}, {"document_id": 1, "path": 1}))
    try:
        from services.rag import delete_project_documents
        delete_project_documents(str(project_oid))
    except Exception:
        pass
    upload_root = Path("uploads").resolve()
    for file_doc in files:
        try:
            candidate = Path(file_doc.get("path", "")).resolve()
            if candidate.is_relative_to(upload_root) and candidate.is_file():
                candidate.unlink()
        except (OSError, ValueError):
            pass
    get_collection("project_files").delete_many({"project_id": project_oid})
    get_collection("long_term_memory").delete_many({"project_id": project_oid})
    get_collection("project_members").delete_many({"project_id": project_oid})
    get_collection("projects").delete_one({"_id": project_oid})
    return {"conversations_deleted": len(thread_ids), "files_deleted": len(files), "checkpoints_deleted": checkpoints_deleted}


def invite_project_member(project_id: str, inviter_id: str, identifier: str, role: str) -> dict[str, Any]:
    """Tạo lời mời tham gia Project cho tài khoản đã đăng ký."""
    project_oid, inviter_oid = _oid(project_id), _oid(inviter_id)
    if not project_oid or not inviter_oid:
        raise ValueError("Invalid project or user ID.")
    project = get_collection("projects").find_one({"_id": project_oid, "owner_id": inviter_oid})
    if not project:
        raise PermissionError("Only the project owner can invite members.")
    if role not in VALID_MEMBER_ROLES:
        raise ValueError("Role must be 'editor' or 'chat_only'.")
    lookup = (identifier or "").strip().lower()
    user = get_collection("users").find_one({"$or": [{"email": lookup}, {"username": lookup}]})
    if not user:
        raise ValueError("No registered account matches that email or username.")
    target_oid = user["_id"]
    if target_oid == inviter_oid:
        raise ValueError("You already own this project.")
    members = get_collection("project_members")
    existing = members.find_one({"project_id": project_oid, "user_id": target_oid})
    now = datetime.now(timezone.utc)
    if existing and existing.get("status") == "accepted":
        raise ValueError("This user is already a project member.")
    if existing:
        members.update_one({"_id": existing["_id"]}, {"$set": {"role": role, "status": "invited", "invited_by": inviter_oid, "created_at": now}})
        existing.update({"role": role, "status": "invited", "invited_by": inviter_oid, "created_at": now})
        return _public(existing) or {}
    doc = {"project_id": project_oid, "user_id": target_oid, "role": role, "status": "invited", "invited_by": inviter_oid, "created_at": now, "accepted_at": None}
    result = members.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _public(doc) or {}


def list_project_members(project_id: str) -> list[dict[str, Any]]:
    """Liệt kê owner, thành viên và lời mời của Project."""
    project_oid = _oid(project_id)
    if not project_oid:
        return []
    project = get_collection("projects").find_one({"_id": project_oid})
    if not project:
        return []
    owner_id = project.get("owner_id")
    owner = get_collection("users").find_one({"_id": owner_id}, {"username": 1, "full_name": 1, "email": 1, "avatar_url": 1}) or {}
    result = [{"user_id": str(owner_id), "role": "owner", "status": "accepted", "username": owner.get("username", ""), "full_name": owner.get("full_name", ""), "email": owner.get("email", ""), "avatar_url": owner.get("avatar_url", "")}]
    for row in get_collection("project_members").find({"project_id": project_oid}):
        user = get_collection("users").find_one({"_id": row.get("user_id")}, {"username": 1, "full_name": 1, "email": 1, "avatar_url": 1}) or {}
        item = _public(row) or {}
        item.update({key: user.get(key, "") for key in ("username", "full_name", "email", "avatar_url")})
        result.append(item)
    return result


def list_pending_invitations(user_id: str) -> list[dict[str, Any]]:
    """Lấy lời mời Project đang chờ user phản hồi."""
    user_oid = _oid(user_id)
    if not user_oid:
        return []
    rows = get_collection("project_members").find({"user_id": user_oid, "status": "invited"})
    result = []
    for row in rows:
        project = get_collection("projects").find_one({"_id": row["project_id"]})
        if project:
            item = _public(row) or {}
            item["project"] = _public(project)
            result.append(item)
    return result


def respond_to_invitation(invitation_id: str, user_id: str, accept: bool) -> bool:
    """Chấp nhận hoặc từ chối lời mời Project của user."""
    invitation_oid, user_oid = _oid(invitation_id), _oid(user_id)
    if not invitation_oid or not user_oid:
        return False
    result = get_collection("project_members").update_one(
        {"_id": invitation_oid, "user_id": user_oid, "status": "invited"},
        {"$set": {"status": "accepted" if accept else "declined", "accepted_at": datetime.now(timezone.utc) if accept else None}},
    )
    return result.modified_count == 1


def update_member_role(project_id: str, owner_id: str, member_id: str, role: str) -> bool:
    """Đổi vai trò thành viên trong Project do owner quản lý."""
    project_oid, owner_oid, member_oid = _oid(project_id), _oid(owner_id), _oid(member_id)
    if role not in VALID_MEMBER_ROLES or not all((project_oid, owner_oid, member_oid)):
        return False
    if not get_collection("projects").find_one({"_id": project_oid, "owner_id": owner_oid}):
        return False
    result = get_collection("project_members").update_one(
        {"project_id": project_oid, "user_id": member_oid, "status": "accepted"},
        {"$set": {"role": role}},
    )
    return result.matched_count == 1


def remove_project_member(project_id: str, owner_id: str, member_id: str) -> bool:
    """Thu hồi membership của user khỏi Project."""
    project_oid, owner_oid, member_oid = _oid(project_id), _oid(owner_id), _oid(member_id)
    if not all((project_oid, owner_oid, member_oid)):
        return False
    if not get_collection("projects").find_one({"_id": project_oid, "owner_id": owner_oid}):
        return False
    result = get_collection("project_members").delete_one({"project_id": project_oid, "user_id": member_oid})
    return result.deleted_count == 1


def list_project_conversations(project_id: str) -> list[dict[str, Any]]:
    """Liệt kê các cuộc trò chuyện thuộc Project."""
    project_oid = _oid(project_id)
    if not project_oid:
        return []
    return [_public(row) or {} for row in get_collection("conversations").find({"project_id": project_oid}).sort("updated_at", -1)]


def create_project_memory(project_id: str, user_id: str, memory: str, thread_id: str = "") -> str:
    """Lưu một fact dùng chung trong Project memory."""
    project_oid, user_oid = _oid(project_id), _oid(user_id)
    text = (memory or "").strip()[:2000]
    if not project_oid or not user_oid or not text:
        return "Project memory could not be saved."
    if not can_edit_project(project_id, user_id):
        return "Only project owners and editors can save shared project memory."
    from storage.memories import save_memory_entry
    entry = save_memory_entry(thread_id, text, user_id=user_id, project_id=project_id)
    if not entry:
        return "Project memory could not be saved."
    get_collection("projects").update_one({"_id": project_oid}, {"$set": {"updated_at": datetime.now(timezone.utc)}})
    return json.dumps({
        "status": "saved",
        "scope": "project",
        "memory_id": str(entry["_id"]),
        "version": entry["version"],
        "memory": entry["memory"],
    }, ensure_ascii=False)


def search_project_memory_entries(project_id: str, query: str = "", limit: int = 5) -> dict[str, Any]:
    """Trả memory Project kèm ID và version để agent có thể quản lý chính xác."""
    if not get_project(project_id):
        return {"status": "invalid_scope", "source": "none", "memories": []}
    from storage.memories import get_user_memories
    result = get_user_memories(
        project_id=project_id,
        query=query,
        allow_full_fallback=True,
        limit=limit,
    )
    result["memories"] = result.get("memories", [])[:max(1, limit)]
    result["scope"] = "project"
    return result


def search_project_memories(project_id: str, query: str, limit: int = 5) -> list[str]:
    """Tìm các fact Project memory liên quan đến câu hỏi."""
    result = search_project_memory_entries(project_id, query, limit)
    return [str(item.get("memory", "")) for item in result.get("memories", [])]


def list_project_files(project_id: str) -> list[dict[str, Any]]:
    """Liệt kê file đã đăng ký trong Project."""
    project_oid = _oid(project_id)
    if not project_oid:
        return []
    return [_public(row) or {} for row in get_collection("project_files").find({"project_id": project_oid}).sort("created_at", -1)]


def register_project_file(project_id: str, document_id: str, thread_id: str, filename: str, path: str, file_type: str, uploaded_by: str) -> dict[str, Any]:
    """Đăng ký file upload để chia sẻ trong Project."""
    project_oid, user_oid = _oid(project_id), _oid(uploaded_by)
    if not project_oid or not user_oid:
        raise ValueError("Invalid project or user ID.")
    doc = {"project_id": project_oid, "document_id": document_id, "thread_id": thread_id, "filename": filename, "path": path, "file_type": file_type, "uploaded_by": user_oid, "created_at": datetime.now(timezone.utc)}
    get_collection("project_files").update_one({"project_id": project_oid, "document_id": document_id}, {"$set": doc}, upsert=True)
    get_collection("projects").update_one({"_id": project_oid}, {"$set": {"updated_at": doc["created_at"]}})
    return doc


def get_project_file(project_id: str, document_id: str) -> dict[str, Any] | None:
    """Lấy metadata file theo Project ID và document ID."""
    project_oid = _oid(project_id)
    if not project_oid:
        return None
    return _public(get_collection("project_files").find_one({"project_id": project_oid, "document_id": document_id}))


def delete_project_file_record(project_id: str, document_id: str) -> bool:
    """Xóa metadata file khỏi collection của Project."""
    project_oid = _oid(project_id)
    if not project_oid:
        return False
    result = get_collection("project_files").delete_one({"project_id": project_oid, "document_id": document_id})
    return result.deleted_count == 1
