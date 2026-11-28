"""MongoDB storage for one immutable result per summary group."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from storage.mongo_client import get_collection

JOBS = "summary_jobs"
CHECKPOINTS = "summary_job_checkpoints"
SCOPE_FIELDS = ("thread_id", "user_id", "project_id")
JOB_FIELDS = tuple(f"context.{name}" for name in SCOPE_FIELDS) + ("document_id", "target_language")


@lru_cache(maxsize=1) # last recently used job is likely to be reused
def ensure_summary_indexes() -> None:
    """A writer must have the unique indexes before accepting concurrent work."""
    jobs = get_collection(JOBS)
    checkpoints = get_collection(CHECKPOINTS)
    jobs.create_index([(field, 1) for field in JOB_FIELDS], unique=True)
    jobs.create_index("document_id") # for cleanup of all jobs for a document
    checkpoints.create_index([("job_id", 1), ("stage", 1), ("group_index", 1)], unique=True) # mỗi bộ chỉ có một bản ghi
    for collection in (jobs, checkpoints):
        collection.create_index("expires_at", expireAfterSeconds=0)


def _get_or_insert(collection, key, fields):
    try:
        return collection.find_one_and_update(
            key, {"$setOnInsert": fields}, upsert=True, return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        row = collection.find_one(key)
        if row is None:
            raise
        return row


class SummaryJobStore:
    def __init__(self, job: dict):
        self.job_id: ObjectId = job["_id"]
        self.final_summary: str | None = job["final_summary"]
        self.expiry = {"expires_at": job["expires_at"]} if job.get("expires_at") else {}

    @classmethod
    def open(cls, context: dict, language: str) -> SummaryJobStore:
        # tìm job theo context và language, tạo mới nếu chưa có. Mỗi job chỉ có một kết quả cuối cùng.
        ensure_summary_indexes()
        key = {f"context.{name}": context.get(name) or None for name in SCOPE_FIELDS}
        key.update(document_id=context["document_id"], target_language=language)
        jobs = get_collection(JOBS)
        conversation = get_collection("conversations").find_one(
            {"thread_id": context["thread_id"]}, {"expires_at": 1, "project_id": 1},
        )
        if not conversation or str(conversation.get("project_id") or "") != str(context.get("project_id") or ""):
            raise LookupError("Conversation is no longer in the requested scope.")
        fields = {"final_summary": None}
        if isinstance(conversation.get("expires_at"), datetime):
            fields["expires_at"] = conversation["expires_at"]
        return cls(_get_or_insert(jobs, key, fields))

    def get_results(self, stage: str) -> dict[int, str]:
        # đọc tất cả các kết quả checkpoint cho một stage, trả về dict {group_index: content}
        rows = get_collection(CHECKPOINTS).find(
            {"job_id": self.job_id, "stage": stage}, {"group_index": 1, "content": 1},
        )
        return {row["group_index"]: row["content"] for row in rows}

    def save_result(self, stage: str, index: int, content: str) -> str:
        # lưu kết quả checkpoint cho một stage và group_index, trả về content đã lưu
        key = {"job_id": self.job_id, "stage": stage, "group_index": index}
        checkpoints = get_collection(CHECKPOINTS)
        row = _get_or_insert(checkpoints, key, {"content": content, **self.expiry})
        # Cleanup removes parents first. Cover a worker returning after that deletion.
        if not get_collection(JOBS).find_one({"_id": self.job_id}, {"_id": 1}):
            checkpoints.delete_one({"_id": row["_id"]})
            raise LookupError("Summary job was deleted.")
        return row["content"]

    def read_final(self) -> str | None:
        row = get_collection(JOBS).find_one({"_id": self.job_id}, {"final_summary": 1})
        if row is None:
            raise LookupError("Summary job was deleted.")
        return row["final_summary"]

    def complete(self, text: str) -> str:
        # lưu kết quả cuối cùng cho job, trả về text đã lưu. Nếu job đã có kết quả, trả về kết quả hiện có.
        written = get_collection(JOBS).update_one(
            {"_id": self.job_id, "final_summary": None},
            {"$set": {"final_summary": text}},
        )
        if written.matched_count:
            return text
        final = self.read_final()
        if final is None:
            raise RuntimeError("Summary job has no committed result.")
        return final


def delete_summary_jobs(query: dict) -> None:
    jobs = get_collection(JOBS)
    ids = [row["_id"] for row in jobs.find(query, {"_id": 1})]
    if ids:
        jobs.delete_many({"_id": {"$in": ids}})
        get_collection(CHECKPOINTS).delete_many({"job_id": {"$in": ids}})
