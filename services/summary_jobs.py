"""MongoDB persistence for resumable document summaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from storage.mongo_client import get_collection


# Increment when chunk grouping or prompt semantics change so stale checkpoints are ignored.
PIPELINE_VERSION = "2"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SummaryJobStore:
    """Persist job metadata and individual stage results without storing source text."""

    def __init__(self, job_id: str, context: dict[str, str]) -> None:
        self.job_id = job_id
        self.context = context
        self.jobs = get_collection("summary_jobs")
        self.checkpoints = get_collection("summary_job_checkpoints")

    @classmethod
    def for_document(
        cls,
        documents: list[Any],
        context: dict[str, str],
        *,
        target_language: str,
        input_token_budget: int,
        pipeline_config: dict[str, Any] | None = None,
    ) -> "SummaryJobStore":
        identity = {
            "version": PIPELINE_VERSION,
            "user_id": context.get("user_id", ""),
            "thread_id": context.get("thread_id", ""),
            "project_id": context.get("project_id", ""),
            "source": context.get("source", ""),
            "language": target_language,
            "input_token_budget": input_token_budget,
            "pipeline_config": pipeline_config or {},
            "documents": [
                {
                    "content": document.page_content,
                    "metadata": document.metadata,
                }
                for document in documents
            ],
        }
        serialized = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        job_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return cls(job_id, context)

    def start(self) -> None:
        now = _now()
        # Keep the explicit identity for databases with a unique job_id index.
        # Missing values otherwise all index as null, despite distinct _id values.
        fields = {
            "job_id": self.job_id,
            "context": self.context,
            "status": "running",
            "updated_at": now,
        }
        if not self.context.get("user_id"):
            fields["expires_at"] = now + timedelta(hours=24)
        self.jobs.update_one(
            {"_id": self.job_id},
            {
                "$set": fields,
                "$setOnInsert": {"created_at": now},
                "$unset": {"last_error": ""},
            },
            upsert=True,
        )

    def get_results(self, stage: str) -> dict[int, str]:
        rows = self.checkpoints.find({"job_id": self.job_id, "stage": stage})
        return {
            int(row["group_index"]): row["content"]
            for row in rows
            if isinstance(row.get("group_index"), int)
            and isinstance(row.get("content"), str) and row["content"].strip()
        }

    def save_result(self, stage: str, group_index: int, content: str) -> None:
        checkpoint_id = f"{self.job_id}:{stage}:{group_index}"
        now = _now()
        fields: dict[str, Any] = {
            "job_id": self.job_id,
            # Older deployments uniquely index (job_id, step_index). Include
            # the stage so map/reduce group 0 remain distinct checkpoints.
            "step_index": f"{stage}:{group_index}",
            "stage": stage,
            "group_index": group_index,
            "content": content,
            "updated_at": now,
        }
        if not self.context.get("user_id"):
            fields["expires_at"] = now + timedelta(hours=24)
        self.checkpoints.update_one(
            {"_id": checkpoint_id},
            {"$set": fields},
            upsert=True,
        )

    def get_final(self) -> str | None:
        job = self.jobs.find_one({"_id": self.job_id, "status": "completed"}, {"final_summary": 1})
        result = job.get("final_summary") if job else None
        return result if isinstance(result, str) and result.strip() else None

    def complete(self, summary: str) -> None:
        self.jobs.update_one(
            {"_id": self.job_id},
            {"$set": {"status": "completed", "final_summary": summary, "updated_at": _now()}},
        )

    def pause(self, reason: str = "") -> None:
        fields: dict[str, Any] = {"status": "paused", "updated_at": _now()}
        if reason:
            fields["last_error"] = reason[:500]
        self.jobs.update_one({"_id": self.job_id}, {"$set": fields})
