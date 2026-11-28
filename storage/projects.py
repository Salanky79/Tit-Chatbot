"""MongoDB persistence for projects, memberships, and project-scoped memory."""

from __future__ import annotations

import config as cfg
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.summary_jobs import delete_summary_jobs
from storage.mongo_client import get_collection


VALID_MEMBER_ROLES = cfg.VALID_MEMBER_ROLES
INVITATION_DURATIONS = cfg.INVITATION_DURATIONS
DEFAULT_INVITATION_DAYS = cfg.DEFAULT_INVITATION_DAYS



# =============================================================================
# 01. Thời gian, hạn lời mời và biểu diễn dữ liệu
# =============================================================================

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _invitation_expiry(row: dict[str, Any]) -> datetime:
    """Legacy invitations expire seven days after creation as well."""
    if isinstance(row.get("expires_at"), datetime):
        return _aware(row["expires_at"])
    created = row.get("created_at")
    return _aware(created) + timedelta(days=DEFAULT_INVITATION_DAYS) if isinstance(created, datetime) else datetime.min.replace(tzinfo=timezone.utc)


def _valid_invitation(now: datetime) -> dict[str, Any]:
    return {"$or": [
        {"expires_at": {"$gt": now}},
        {"expires_at": {"$exists": False}, "created_at": {"$gt": now - timedelta(days=DEFAULT_INVITATION_DAYS)}},
    ]}


def _token_hash(token: str) -> str | None:
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _member_public(row: dict[str, Any]) -> dict[str, Any]:
    result = _public(row) or {}
    if row.get("status") == "invited":
        expiry = _invitation_expiry(row)
        result["expires_at"] = expiry.isoformat()
        if expiry <= _now():
            result["status"] = "expired"
    return result



# =============================================================================
# 02. Chuẩn hóa ObjectId và dữ liệu công khai
# =============================================================================

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
    result.pop("invitation_token_hash", None)
    for key in ("_id", "owner_id", "project_id", "user_id", "invited_by", "created_by", "uploaded_by", "last_actor_id", "sender_id"):
        if isinstance(result.get(key), ObjectId):
            result[key] = str(result[key])
    for key in ("created_at", "updated_at", "accepted_at", "expires_at"):
        value = result.get(key)
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result



# =============================================================================
# 03. Tạo và truy vấn Project
# =============================================================================

def create_project(owner_id: str, name: str, description: str = "") -> dict[str, Any]:
    """Tạo Project mới thuộc sở hữu của user."""
    owner_oid = _oid(owner_id)
    clean_name = (name or "").strip()[:cfg.PROJECT_NAME_CHARS]
    if not owner_oid:
        raise ValueError("Valid user is required to create a project.")
    if not clean_name:
        raise ValueError("Project name is required.")
    now = datetime.now(timezone.utc)
    doc = {
        "owner_id": owner_oid,
        "name": clean_name,
        "description": (description or "").strip()[:cfg.PROJECT_DESCRIPTION_CHARS],
        "instructions": "",
        "memory_mode": cfg.DEFAULT_MEMORY_MODE,
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



# =============================================================================
# 04. Membership và quyền chỉnh sửa
# =============================================================================

def get_membership(project_id: str, user_id: str) -> dict[str, Any] | None:
    """Lấy quyền của user trong Project nếu membership đã được chấp nhận."""
    project_oid, user_oid = _oid(project_id), _oid(user_id)
    if not project_oid or not user_oid:
        return None
    project = get_collection("projects").find_one({"_id": project_oid}, {"owner_id": 1})
    if project and str(project.get("owner_id")) == str(user_oid):
        return {"project_id": str(project_oid), "user_id": str(user_oid), "role": "owner", "status": "accepted"}
    if not project:
        return None
    row = get_collection("project_members").find_one(
        {"project_id": project_oid, "user_id": user_oid, "status": "accepted"}
    )
    return _public(row)


def can_edit_project(project_id: str, user_id: str) -> bool:
    """Kiểm tra user có quyền owner hoặc editor hay không."""
    membership = get_membership(project_id, user_id)
    return bool(membership and membership.get("role") in {"owner", "editor"})



# =============================================================================
# 05. Cập nhật và xóa Project cùng tài nguyên
# =============================================================================

def update_project(project_id: str, user_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Cập nhật thông tin Project nếu user có quyền chỉnh sửa."""
    project_oid = _oid(project_id)
    if not project_oid or not can_edit_project(project_id, user_id):
        return None
    allowed: dict[str, Any] = {}
    if "name" in fields:
        name = str(fields["name"] or "").strip()[:cfg.PROJECT_NAME_CHARS]
        if not name:
            raise ValueError("Project name is required.")
        allowed["name"] = name
    if "description" in fields:
        allowed["description"] = str(fields["description"] or "").strip()[:cfg.PROJECT_DESCRIPTION_CHARS]
    if "instructions" in fields:
        allowed["instructions"] = str(fields["instructions"] or "").strip()[:cfg.PROJECT_INSTRUCTION_CHARS]
    if "memory_mode" in fields:
        mode = fields["memory_mode"]
        if mode not in cfg.PROJECT_MEMORY_MODES:
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
        try:
            from services.rag import delete_thread_documents
            delete_thread_documents(thread_id)
        except Exception:
            pass
    delete_summary_jobs({"context.project_id": str(project_oid)})
    files = list(get_collection("project_files").find({"project_id": project_oid}, {"document_id": 1, "path": 1}))
    try:
        from services.rag import delete_project_documents
        delete_project_documents(str(project_oid))
    except Exception:
        pass
    upload_root = Path(cfg.UPLOAD_DIR).resolve()
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



# =============================================================================
# 06. Tạo, cấp lại và liệt kê lời mời
# =============================================================================

def invite_project_member(project_id: str, inviter_id: str, identifier: str, role: str, expiry_days: int = DEFAULT_INVITATION_DAYS) -> dict[str, Any]:
    """Tạo lời mời tham gia Project cho tài khoản đã đăng ký."""
    project_oid, inviter_oid = _oid(project_id), _oid(inviter_id)
    if not project_oid or not inviter_oid:
        raise ValueError("Invalid project or user ID.")
    project = get_collection("projects").find_one({"_id": project_oid, "owner_id": inviter_oid})
    if not project:
        raise PermissionError("Only the project owner can invite members.")
    if not isinstance(role, str) or role not in VALID_MEMBER_ROLES:
        raise ValueError("Role must be 'editor' or 'chat_only'.")
    if type(expiry_days) is not int or expiry_days not in INVITATION_DURATIONS:
        raise ValueError("Invitation duration must be 1, 3, 7, 14, or 30 days.")
    if not isinstance(identifier, str):
        raise ValueError("Email or username is required.")
    lookup = (identifier or "").strip().lower()
    user = get_collection("users").find_one({"$or": [{"email": lookup}, {"username": lookup}]})
    if not user:
        raise ValueError("No registered account matches that email or username.")
    if not user.get("is_active", True):
        raise ValueError("This account is inactive.")
    target_oid = user["_id"]
    if target_oid == inviter_oid:
        raise ValueError("You already own this project.")
    members = get_collection("project_members")
    existing = members.find_one({"project_id": project_oid, "user_id": target_oid})
    now = _now()
    if existing and existing.get("status") == "accepted":
        raise ValueError("This user is already a project member.")
    token = secrets.token_urlsafe(cfg.SECRET_TOKEN_BYTES)
    try:
        doc = members.find_one_and_update(
            {"project_id": project_oid, "user_id": target_oid, "status": {"$ne": "accepted"}},
            {"$set": {"role": role, "status": "invited", "invited_by": inviter_oid,
                      "created_at": now, "expires_at": now + timedelta(days=expiry_days),
                      "accepted_at": None, "invitation_token_hash": _token_hash(token)},
             "$setOnInsert": {"project_id": project_oid, "user_id": target_oid}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        # An invitation may have been accepted between the read and the write.
        raise ValueError("This user is already a project member.") from exc
    result = _member_public(doc)
    # This raw token is returned once to the invitation route, never persisted.
    result.update(invitation_token=token, email=user.get("email", ""), project_name=project.get("name", "Project"))
    return result


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
        item = _member_public(row)
        item.update({key: user.get(key, "") for key in ("username", "full_name", "email", "avatar_url")})
        result.append(item)
    return result


def list_pending_invitations(user_id: str) -> list[dict[str, Any]]:
    """Lấy lời mời Project đang chờ user phản hồi."""
    user_oid = _oid(user_id)
    if not user_oid:
        return []
    rows = get_collection("project_members").find({"user_id": user_oid, "status": "invited", **_valid_invitation(_now())})
    result = []
    for row in rows:
        project = get_collection("projects").find_one({"_id": row["project_id"]})
        if project:
            item = _member_public(row)
            item["project"] = {"_id": str(project["_id"]), "name": project.get("name", "Project")}
            result.append(item)
    return result



# =============================================================================
# 07. Xem trước và phản hồi lời mời
# =============================================================================

def respond_to_invitation(invitation_id: str, user_id: str, accept: bool) -> bool:
    """Chấp nhận hoặc từ chối lời mời Project của user."""
    invitation_oid, user_oid = _oid(invitation_id), _oid(user_id)
    if not invitation_oid or not user_oid:
        return False
    row = get_collection("project_members").find_one({"_id": invitation_oid, "user_id": user_oid, "status": "invited"})
    if not row or not get_project(str(row["project_id"])):
        return False
    now = _now()
    result = get_collection("project_members").update_one(
        {"_id": invitation_oid, "user_id": user_oid, "status": "invited", **_valid_invitation(now)},
        {"$set": {"status": "accepted" if accept else "declined", "accepted_at": now if accept else None},
         "$unset": {"invitation_token_hash": ""}},
    )
    return result.modified_count == 1 # số lượng document bị thay đổi, 1 nếu thành công, 0 nếu không có gì thay đổi

# Khi user mở link mời dạng /#project-invite={token} (từ email hoặc link copy), gọi trước lúc user bấm nút Chấp nhận/Từ chối để hiển thị popup xem trước lời mời (#projectInvitationModal).
def preview_project_invitation(token: str, user_id: str) -> dict[str, Any] | None:
    """Reveal a minimal preview only to the signed-in intended recipient."""
    hashed, user_oid = _token_hash(token), _oid(user_id)
    if not hashed or not user_oid:
        return None
    row = get_collection("project_members").find_one({"invitation_token_hash": hashed, "user_id": user_oid, "status": "invited"})
    if not row:
        return None
    if _invitation_expiry(row) <= _now():
        raise ValueError("Invitation has expired. Ask the owner to send a new invitation.")
    project = get_project(str(row["project_id"]))
    if not project:
        return None
    inviter = get_collection("users").find_one({"_id": row.get("invited_by")}, {"full_name": 1, "username": 1}) or {}
    return {"_id": str(row["_id"]), "project_id": str(row["project_id"]), "project_name": project.get("name", "Project"),
            "role": row["role"], "expires_at": _invitation_expiry(row).isoformat(),
            "inviter_name": inviter.get("full_name") or inviter.get("username") or "Chủ sở hữu"}


def respond_to_invitation_token(token: str, user_id: str, accept: bool) -> str | None:
    invitation = preview_project_invitation(token, user_id)
    if not invitation:
        return None
    now = _now()
    result = get_collection("project_members").update_one(
        {"_id": _oid(invitation["_id"]), "user_id": _oid(user_id), "status": "invited",
         "invitation_token_hash": _token_hash(token), **_valid_invitation(now)},
        {"$set": {"status": "accepted" if accept else "declined", "accepted_at": now if accept else None},
         "$unset": {"invitation_token_hash": ""}},
    )
    return invitation["project_id"] if result.modified_count == 1 else None



# =============================================================================
# 08. Revision cho thông báo SSE
# =============================================================================

def project_update_state(user_id: str) -> dict[str, str]:
    """Hash a user's visible state; works across workers without a local event bus."""
    user_oid = _oid(user_id)
    if not user_oid:
        return {"revision": "", "members_revision": ""}
    # Lấy các Project user tham gia hoặc được mời để gom phạm vi cần theo dõi
    own_rows = list(get_collection("project_members").find({"user_id": user_oid}, {"project_id": 1, "status": 1}))
    project_ids = [r["project_id"] for r in own_rows if r.get("project_id")]
    projects = list(get_collection("projects").find(
        {"$or": [{"owner_id": user_oid}, {"_id": {"$in": project_ids}}]},
        {"name": 1, "owner_id": 1, "updated_at": 1},
    ).sort("_id", 1))
    # Chỉ thành viên đã accepted hoặc owner mới thấy biến động thành viên của Project đó
    accepted_ids = {r["project_id"] for r in own_rows if r.get("status") == "accepted"}
    visible_ids = [p["_id"] for p in projects if p.get("owner_id") == user_oid or p["_id"] in accepted_ids]
    members = [_member_public(r) for r in get_collection("project_members").find(
        {"$or": [{"user_id": user_oid}, {"project_id": {"$in": visible_ids}}]},
        {"project_id": 1, "user_id": 1, "role": 1, "status": 1, "created_at": 1, "expires_at": 1},
    ).sort("_id", 1)]
    def digest(value: Any) -> str:
        # sort_keys=True đảm bảo cùng dữ liệu luôn cho ra cùng 1 mã hash SHA-256
        # chuyển dữ liệu python (list/dict lấy từ MongoDB) thành 1 chuỗi JSON chuẩn
        # sắp xếp theo thứ tự A -> Z
        # default=str để chuyển các giá trị không phải kiểu JSON (ví dụ datetime) thành chuỗi
        # .encode() để chuyển chuỗi JSON thành bytes trước khi băm
        # Băm mảng byte đó bằng SHA-256 và dùng .hexdigest() đổi ra chuỗi 64 ký tự hex gọn nhẹ (ví dụ "a3f8c2...").
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
    return {"revision": digest([projects, members]), "members_revision": digest(members)}



# =============================================================================
# 09. Đổi quyền và thu hồi thành viên
# =============================================================================

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



# =============================================================================
# 10. Danh sách cuộc trò chuyện trong Project
# =============================================================================

def list_project_conversations(project_id: str) -> list[dict[str, Any]]:
    """Liệt kê các cuộc trò chuyện thuộc Project."""
    project_oid = _oid(project_id)
    if not project_oid:
        return []
    return [_public(row) or {} for row in get_collection("conversations").find({"project_id": project_oid}).sort("updated_at", -1)]



# =============================================================================
# 11. Ghi và truy xuất bộ nhớ chung
# =============================================================================

def create_project_memory(project_id: str, user_id: str, memory: str, thread_id: str = "") -> str:
    """Lưu một fact dùng chung trong Project memory."""
    project_oid, user_oid = _oid(project_id), _oid(user_id)
    text = (memory or "").strip()[:cfg.PROJECT_MEMORY_CHARS]
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


def search_project_memory_entries(project_id: str, query: str = "", limit: int = cfg.MEMORY_TOP_K) -> dict[str, Any]:
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


def search_project_memories(project_id: str, query: str, limit: int = cfg.MEMORY_TOP_K) -> list[str]:
    """Tìm các fact Project memory liên quan đến câu hỏi."""
    result = search_project_memory_entries(project_id, query, limit)
    return [str(item.get("memory", "")) for item in result.get("memories", [])]



# =============================================================================
# 12. Đăng ký và quản lý metadata tài liệu
# =============================================================================

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
