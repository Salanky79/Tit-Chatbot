"""Explicitly gated RAG evaluation runner.

No dataset sync or Chroma writes occur unless the corresponding CLI flags are
provided. Unit/integration tests should use fake stores and clients.
"""

from __future__ import annotations

import config as cfg
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import uuid
from typing import Any, Callable

from dotenv import load_dotenv

from .dataset import MAX_CASES_PER_RUN, DATASET_NAME, RAG_EXAMPLES, RAG_TEST_DOCUMENTS, sync_dataset, validate_examples
from .evaluators import RAG_EVALUATORS
from .target import build_target, ensure_eval_run_dir

load_dotenv()

logger = logging.getLogger(__name__)



# =============================================================================
# Cô lập tracing trong lần chạy local
# =============================================================================

@contextmanager
def disable_langsmith_tracing():
    """Prevent implicit trace uploads during local evaluation, restoring env after."""
    keys = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")
    previous = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ[key] = "false"
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value



# =============================================================================
# Chuẩn hóa kết quả và tên metric
# =============================================================================

def _serializable_result(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _serializable_result(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable_result(v) for v in value]
    if hasattr(value, "model_dump"):
        return _serializable_result(value.model_dump())
    return str(value)


EVALUATOR_METRIC_MAP = {
    "evaluate_retrieval_relevance": "retrieval_relevance",
    "evaluate_answer_relevance": "answer_relevance",
    "evaluate_groundedness": "groundedness",
    "evaluate_correctness": "correctness",
}


def _evaluator_metric_key(evaluator: Callable[..., Any]) -> str:
    return (
        getattr(evaluator, "metric_key", None)
        or getattr(evaluator, "metric_name", None)
        or EVALUATOR_METRIC_MAP.get(getattr(evaluator, "__name__", ""), None)
        or getattr(evaluator, "__name__", "evaluator").removeprefix("evaluate_")
    )



# =============================================================================
# Chạy các evaluator trên từng bản ghi
# =============================================================================

def evaluate_records(
    examples: list[dict[str, Any]],
    target: Callable[[dict[str, Any]], dict[str, Any]],
    evaluators: list[Callable[[Any, Any], dict[str, Any]]],
    results_path: Path,
) -> list[dict[str, Any]]:
    """Run records locally, append one durable JSONL row at a time."""
    results_path = Path(results_path).resolve()
    package_dir = Path(__file__).resolve().parent
    if not results_path.is_relative_to((package_dir / "results").resolve()):
        raise ValueError("Results path must be under evaluation/rag_eval/results.")
    rows = []
    with results_path.open("x", encoding="utf-8") as output_file:
        for example in examples:
            errors = []
            try:
                outputs = target(example["inputs"])
            except Exception as exc:
                outputs = {}
                errors.append({"stage": "target", "error_type": type(exc).__name__, "message": str(exc)})
                logger.error("Target failed for case %s: %s: %s", example.get("case_id"), type(exc).__name__, exc)
            scores = []
            for evaluator in evaluators:
                metric_key = _evaluator_metric_key(evaluator)
                try:
                    res = evaluator({"outputs": outputs}, example)
                    scores.append(res)
                except Exception as exc:
                    errors.append({"stage": metric_key, "error_type": type(exc).__name__, "message": str(exc)})
                    logger.error("Evaluator %s failed for case %s: %s: %s", metric_key, example.get("case_id"), type(exc).__name__, exc)
                    scores.append({
                        "key": metric_key,
                        "score": None,
                        "comment": f"Evaluator failed: {type(exc).__name__}: {exc}",
                        "metadata": {"status": "error"},
                    })
            row = {
                "case_id": example["case_id"],
                "inputs": example["inputs"],
                "reference_outputs": example["outputs"],
                "outputs": outputs,
                "evaluation_results": scores,
                "errors": errors,
            }
            output_file.write(json.dumps(_serializable_result(row), ensure_ascii=False) + "\n")
            output_file.flush()
            rows.append(row)
    return rows



# =============================================================================
# Chạy local và xuất kết quả
# =============================================================================

def run_local(
    *,
    run_id: str | None = None,
    examples: list[dict[str, Any]] = RAG_EXAMPLES,
    documents: list[dict[str, str]] = RAG_TEST_DOCUMENTS,
    eval_store: Any | None = None,
    generator: Any | None = None,
    evaluators: list[Callable[[Any, Any], dict[str, Any]]] = RAG_EVALUATORS,
    run_dir: Path | None = None,
    limit: int | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    validate_examples(documents, examples)
    if len(examples) > MAX_CASES_PER_RUN:
        raise ValueError(f"Refusing to run more than {MAX_CASES_PER_RUN} cases before benchmark review.")
    if limit is not None and (limit < 1 or limit > MAX_CASES_PER_RUN):
        raise ValueError(f"limit must be between 1 and {MAX_CASES_PER_RUN}.")
    selected_examples = examples[:limit] if limit is not None else examples
    resolved_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    if run_dir is None:
        directory = ensure_eval_run_dir(resolved_run_id)
    else:
        directory = Path(run_dir).resolve()
        results_root = (Path(__file__).resolve().parent / "results").resolve()
        if not directory.is_relative_to(results_root) or not directory.is_dir():
            raise ValueError("run_dir must be an existing directory under evaluation/rag_eval/results.")
    result_path = directory / "results.jsonl"
    metadata_path = directory / "run_metadata.json"
    metadata = {
        "run_id": resolved_run_id,
        "dataset_name": DATASET_NAME,
        "dataset_version": cfg.DATASET_VERSION,
        "case_count": 0,
        "status": "initializing",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result_file": result_path.name,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        with disable_langsmith_tracing():
            target = build_target(resolved_run_id, directory, documents, eval_store=eval_store, generator=generator)
            metadata["status"] = "running"
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            rows = evaluate_records(selected_examples, target, evaluators, result_path)
        metadata["status"] = "completed"
        metadata["case_count"] = len(rows)
        metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return directory, rows
    except Exception as exc:
        metadata["status"] = "failed"
        metadata["error_type"] = type(exc).__name__
        metadata["error_message"] = str(exc)
        metadata["failed_at"] = datetime.now(timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        raise



# =============================================================================
# Đồng bộ và chạy LangSmith theo cờ CLI
# =============================================================================

def sync_langsmith_dataset() -> Any:
    """Explicit sync entry point; never called implicitly by a local run."""
    return sync_dataset()


def run_langsmith(*, sync_first: bool = False, run_id: str | None = None) -> Any:
    """Run LangSmith evaluate against the versioned dataset when explicitly requested."""
    from langsmith import Client

    from .dataset import DATASET_NAME

    if sync_first:
        sync_dataset()
    client = Client()
    dataset = client.read_dataset(dataset_name=DATASET_NAME)
    remote_examples = list(client.list_examples(dataset_id=dataset.id))
    expected_ids = {row["case_id"] for row in RAG_EXAMPLES}
    remote_ids = {
        (getattr(row, "metadata", None) or {}).get("case_id")
        for row in remote_examples
        if (getattr(row, "metadata", None) or {}).get("case_id") is not None
    }
    if len(remote_examples) > MAX_CASES_PER_RUN or remote_ids != expected_ids:
        missing = expected_ids - remote_ids
        extra = remote_ids - expected_ids
        raise ValueError(
            f"LangSmith dataset case mismatch. Missing: {sorted(missing)}, Extra: {sorted(extra)}. "
            "Review/sync the 10-case benchmark before running it."
        )
    resolved_run_id = run_id or uuid.uuid4().hex[:10]
    run_dir = ensure_eval_run_dir(f"langsmith-{resolved_run_id}")
    target = build_target(
        resolved_run_id,
        run_dir,
        RAG_TEST_DOCUMENTS,
    )
    return client.evaluate(
        target,
        data=DATASET_NAME,
        evaluators=RAG_EVALUATORS,
        max_concurrency=1,
        experiment_prefix=f"tit-rag-{resolved_run_id}",
        metadata={"dataset_version": cfg.DATASET_VERSION, "run_id": resolved_run_id},
    )



# =============================================================================
# Điểm vào CLI và kiểm tra tùy chọn
# =============================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tit Chatbot RAG evaluation")
    parser.add_argument("--validate-only", action="store_true", help="Validate fixture data without API or database calls.")
    parser.add_argument("--run-eval", action="store_true", help="Run target, writing only to a new evaluation Chroma store.")
    parser.add_argument("--allow-eval-chroma-write", action="store_true", help="Explicitly permit creation/writes under evaluation/rag_eval/results.")
    parser.add_argument("--sync-langsmith", action="store_true", help="Explicitly create/sync the LangSmith dataset.")
    parser.add_argument("--langsmith-run", action="store_true", help="Run LangSmith evaluate() (requires explicit Chroma-write permission).")
    parser.add_argument("--limit", type=int, default=None, help=f"Run at most this many local cases (hard cap {MAX_CASES_PER_RUN}).")
    args = parser.parse_args(argv)

    if args.validate_only:
        validate_examples()
        print(f"Validated {len(RAG_EXAMPLES)} cases and {len(RAG_TEST_DOCUMENTS)} documents.")
        return 0
    if args.sync_langsmith:
        sync_langsmith_dataset()
        print(f"Synced dataset {DATASET_NAME}.")
        if not args.run_eval and not args.langsmith_run:
            return 0
    if args.run_eval or args.langsmith_run:
        if not args.allow_eval_chroma_write:
            parser.error("--run-eval/--langsmith-run requires --allow-eval-chroma-write.")
        if args.langsmith_run:
            run_langsmith(sync_first=False)
            return 0
        run_dir, rows = run_local(limit=args.limit)
        print(f"Completed {len(rows)} cases. Results: {run_dir / 'results.jsonl'}")
        return 0
    parser.error("Choose --validate-only, --sync-langsmith, or --run-eval.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
