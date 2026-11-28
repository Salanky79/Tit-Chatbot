"""Shared judge client, rubric helpers, and LangSmith result formatting."""

from __future__ import annotations

import config as cfg
import json
import os
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator

from ..retry import retry_with_backoff



# =============================================================================
# Schema kết quả chấm điểm
# =============================================================================

class ScalarGrade(BaseModel):
    score: int = Field(ge=0, le=cfg.EVAL_MAX_SCORE)
    reason: str = Field(default="")
    evidence: list[str] = Field(default_factory=list)

    @field_validator("evidence", mode="before")
    @classmethod
    def _sanitize_evidence(cls, v: Any) -> list[str]:
        if not v:
            return []
        if isinstance(v, (str, int, float)):
            return [str(v)]
        if isinstance(v, (list, tuple)):
            return [str(item) for item in v if item is not None and str(item).strip()]
        return []


class ContextGrade(BaseModel):
    context_id: str
    score: int = Field(ge=0, le=cfg.EVAL_MAX_SCORE)
    reason: str = Field(default="")
    evidence: list[str] = Field(default_factory=list)

    @field_validator("evidence", mode="before")
    @classmethod
    def _sanitize_evidence(cls, v: Any) -> list[str]:
        if not v:
            return []
        if isinstance(v, (str, int, float)):
            return [str(v)]
        if isinstance(v, (list, tuple)):
            return [str(item) for item in v if item is not None and str(item).strip()]
        return []


class RetrievalGrade(BaseModel):
    contexts: list[ContextGrade] = Field(default_factory=list)
    reason: str = Field(default="")


class ClaimGrade(BaseModel):
    claim: str
    label: str
    context_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @field_validator("context_ids", mode="before")
    @classmethod
    def _sanitize_context_ids(cls, v: Any) -> list[str]:
        if not v:
            return []
        if isinstance(v, (str, int)):
            return [str(v)]
        if isinstance(v, (list, tuple)):
            return [str(item) for item in v if item is not None and str(item).strip()]
        return []

    @field_validator("evidence", mode="before")
    @classmethod
    def _sanitize_evidence(cls, v: Any) -> list[str]:
        if not v:
            return []
        if isinstance(v, (str, int, float)):
            return [str(v)]
        if isinstance(v, (list, tuple)):
            return [str(item) for item in v if item is not None and str(item).strip()]
        return []


class GroundednessGrade(BaseModel):
    claims: list[ClaimGrade] = Field(default_factory=list)
    reason: str = Field(default="")


_judge_factory: Callable[[], Any] | None = None



# =============================================================================
# Khởi tạo và thay thế judge trong kiểm thử
# =============================================================================

def set_judge_factory_for_tests(factory: Callable[[], Any] | None) -> None:
    global _judge_factory
    _judge_factory = factory


def make_judge() -> Any:
    if _judge_factory is not None:
        return _judge_factory()
    from langchain_groq import ChatGroq

    model = os.getenv("RAG_EVAL_JUDGE_MODEL") or os.getenv("GROQ_MODEL", cfg.CHAT_MODEL)
    return ChatGroq(
        model=model,
        temperature=cfg.EVAL_TEMPERATURE,
        max_retries=0,
        max_tokens=int(os.getenv("RAG_EVAL_JUDGE_MAX_TOKENS", str(cfg.EVAL_JUDGE_TOKENS))),
    )



# =============================================================================
# Gọi judge có cấu trúc và chuẩn hóa kết quả
# =============================================================================

def invoke_structured(
    schema: type[BaseModel],
    system_prompt: str,
    payload: dict[str, Any],
    *,
    judge: Any | None = None,
    max_attempts: int = cfg.EVAL_ATTEMPTS,
) -> BaseModel:
    client = judge or make_judge()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    def _parse(result: Any) -> BaseModel:
        if isinstance(result, schema):
            return result
        if isinstance(result, dict):
            return schema.model_validate(result)
        if hasattr(result, "model_dump"):
            return schema.model_validate(result.model_dump())
        content = getattr(result, "content", result)
        if isinstance(content, str):
            text = content.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                text = "\n".join(lines).strip()
            return schema.model_validate_json(text)
        raise TypeError(f"Judge returned unsupported output type: {type(result).__name__}")

    def invoke_and_validate() -> BaseModel:
        if hasattr(client, "with_structured_output"):
            try:
                bound = client.with_structured_output(schema, method="json_schema", strict=False)
                return _parse(bound.invoke(messages))
            except Exception as exc:
                err_msg = str(exc).lower()
                if "json_validate_failed" in err_msg or "badrequesterror" in err_msg or "400" in err_msg:
                    try:
                        bound_fallback = client.with_structured_output(schema, method="json_mode")
                        return _parse(bound_fallback.invoke(messages))
                    except Exception:
                        pass
                raise
        return _parse(client.invoke(messages))

    return retry_with_backoff(invoke_and_validate, max_attempts=max_attempts)


def langsmith_result(
    key: str,
    score: float | None,
    comment: str,
    *,
    status: str = "scored",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"status": status}
    if details:
        meta.update(details)
    return {
        "key": key,
        "score": score,
        "comment": comment,
        "metadata": meta,
    }


def read_run_example(run: Any, example: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    outputs = getattr(run, "outputs", None) or (run.get("outputs", {}) if isinstance(run, dict) else {})
    inputs = getattr(example, "inputs", None) or (example.get("inputs", {}) if isinstance(example, dict) else {})
    reference = getattr(example, "outputs", None) or (example.get("outputs", {}) if isinstance(example, dict) else {})
    return inputs, outputs, reference
