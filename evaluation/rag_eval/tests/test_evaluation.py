from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from evaluation.rag_eval.dataset import RAG_EXAMPLES, RAG_TEST_DOCUMENTS, validate_examples
from evaluation.rag_eval.evaluators.groundedness import compute_groundedness
from evaluation.rag_eval.evaluators.retrieval import score_retrieval
from evaluation.rag_eval.runner import disable_langsmith_tracing, evaluate_records
from evaluation.rag_eval.retry import retry_with_backoff
from evaluation.rag_eval.target import RAGEvaluationTarget, create_chroma_store, use_eval_vectorstore


class FakeDocument:
    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


class FakeStore:
    def __init__(self):
        self.add_calls = []
        self.search_calls = []
        self.documents = []

    def add_documents(self, documents, **kwargs):
        self.add_calls.append((documents, kwargs))
        self.documents.extend(documents)

    def similarity_search(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        return self.documents[: kwargs.get("k", 4)] or [
            FakeDocument("indexed evidence", {"document_id": "doc", "page": 1, "chunk_index": 0})
        ]


class DatasetTests(unittest.TestCase):
    def test_small_dataset_has_10_valid_cases_and_two_documents(self):
        validate_examples()
        self.assertEqual(len(RAG_EXAMPLES), 10)
        self.assertEqual(len(RAG_TEST_DOCUMENTS), 2)
        self.assertEqual(len({case["case_id"] for case in RAG_EXAMPLES}), 10)


class GroundednessFormulaTests(unittest.TestCase):
    def test_formula_rewards_only_supported_claims(self):
        result = compute_groundedness([
            {"label": "supported"},
            {"label": "contradicted"},
            {"label": "not_found"},
            {"label": "supported"},
        ])
        self.assertEqual(result["score"], 2.0)  # 4 * 2 / 4
        self.assertEqual(result["normalized_score"], 0.5)
        self.assertEqual(result["contradicted"], 1)
        self.assertEqual(result["not_found"], 1)

    def test_empty_claim_set_is_not_scored(self):
        self.assertEqual(compute_groundedness([])["status"], "no_claims")

    def test_unknown_label_is_rejected(self):
        with self.assertRaises(ValueError):
            compute_groundedness([{"label": "maybe"}])


class RetryTests(unittest.TestCase):
    def test_provider_wait_hint_is_honored(self):
        waits = []
        attempts = 0
        def flaky():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("429 rate limit; try again in 2.5s")
            return "ok"
        result = retry_with_backoff(flaky, sleep=waits.append)
        self.assertEqual(result, "ok")
        self.assertEqual(waits, [3.0])

    def test_retry_attempts_are_bounded(self):
        waits = []
        with self.assertRaisesRegex(RuntimeError, "still failing"):
            retry_with_backoff(
                lambda: (_ for _ in ()).throw(RuntimeError("still failing")),
                max_attempts=3,
                sleep=waits.append,
            )
        self.assertEqual(waits, [1.0, 2.0])


class ProductionStoreIsolationTests(unittest.TestCase):
    def test_chroma_path_guard_rejects_production_location_before_initialization(self):
        production_path = Path(__file__).resolve().parents[3] / "chroma_db_hf"
        with self.assertRaisesRegex(ValueError, "under evaluation/rag_eval/results"):
            create_chroma_store(production_path)

    def test_original_getter_is_restored_after_success_and_exception(self):
        production_store = FakeStore()
        eval_store = FakeStore()
        rag_module = Mock()
        rag_module._vectorstore = production_store
        original_getter = lambda: production_store
        rag_module.get_vectorstore = original_getter

        with use_eval_vectorstore(rag_module, eval_store) as recording:
            self.assertIs(rag_module.get_vectorstore(), recording)
            rag_module.get_vectorstore().add_documents(["fixture"])
        self.assertIs(rag_module.get_vectorstore, original_getter)
        self.assertEqual(len(eval_store.add_calls), 1)
        self.assertEqual(production_store.add_calls, [])

        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with use_eval_vectorstore(rag_module, eval_store):
                raise RuntimeError("fixture failure")
        self.assertIs(rag_module.get_vectorstore, original_getter)

    def test_real_rag_retrieval_uses_eval_store_and_never_production_store(self):
        from services import rag

        production_store = FakeStore()
        eval_store = FakeStore()
        original_getter = rag.get_vectorstore
        with patch.object(rag, "_vectorstore", production_store):
            with use_eval_vectorstore(rag, eval_store):
                result = rag.retrieve_from_rag(query="q", thread_id="eval-thread", user_id=None)

        self.assertEqual(len(eval_store.search_calls), 1)
        self.assertEqual(production_store.search_calls, [])
        self.assertEqual(production_store.add_calls, [])
        self.assertIs(rag.get_vectorstore, original_getter)
        self.assertIn("indexed evidence", result)

    def test_real_ingestion_writes_fixture_only_to_eval_store(self):
        from services import rag

        production_store = FakeStore()
        eval_store = FakeStore()
        original_getter = rag.get_vectorstore
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "results") as tmp:
            text_file = Path(tmp) / "fixture.txt"
            text_file.write_text("A small passage for ingestion through the real service.", encoding="utf-8")
            with patch.object(rag, "_vectorstore", production_store):
                with use_eval_vectorstore(rag, eval_store):
                    rag.add_document_to_rag(str(text_file), "eval-thread", document_id="fixture")
        self.assertEqual(len(eval_store.add_calls), 1)
        self.assertEqual(production_store.add_calls, [])
        self.assertIs(rag.get_vectorstore, original_getter)

    def test_target_restores_vectorstore_after_generation_error(self):
        from services import rag

        production_store = FakeStore()
        eval_store = FakeStore()
        generator = Mock()
        generator.invoke.side_effect = RuntimeError("generation failed")
        original_getter = rag.get_vectorstore
        target = RAGEvaluationTarget("test-run", Path(__file__).resolve().parents[1] / "results", eval_store, generator, [], rag)
        eval_store.similarity_search = Mock(return_value=[FakeDocument("evidence", {"source": "fixture", "page": 1})])
        with patch.object(rag, "_vectorstore", production_store):
            with self.assertRaisesRegex(RuntimeError, "generation failed"):
                target({"question": "q"})
        self.assertIs(rag.get_vectorstore, original_getter)
        self.assertEqual(production_store.search_calls, [])
        self.assertEqual(production_store.add_calls, [])

    def test_mocked_end_to_end_uses_real_ingestion_and_retrieval_and_returns_case_output(self):
        from services import rag

        production_store = FakeStore()
        eval_store = FakeStore()
        generator = Mock()
        generator.invoke.return_value = Mock(content="Yêu cầu ưu tiên được phản hồi trong tám giờ làm việc.")
        original_getter = rag.get_vectorstore
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "results") as tmp:
            run_dir = Path(tmp)
            target = RAGEvaluationTarget(
                "e2e-test",
                run_dir,
                eval_store,
                generator,
                [{
                    "document_id": "doc_e2e",
                    "filename": "guide.txt",
                    "raw_text": "Yêu cầu ưu tiên được phản hồi trong vòng tám giờ làm việc.",
                }],
                rag,
            )
            with patch.object(rag, "_vectorstore", production_store):
                target.index_documents()
                outputs = target({"question": "Yêu cầu ưu tiên được phản hồi trong bao lâu?"})

        self.assertEqual(outputs["answer"], "Yêu cầu ưu tiên được phản hồi trong tám giờ làm việc.")
        self.assertEqual(len(outputs["contexts"]), 1)
        self.assertIn("tám giờ làm việc", outputs["contexts"][0]["text"])
        self.assertEqual(len(eval_store.add_calls), 1)
        self.assertEqual(len(eval_store.search_calls), 1)
        self.assertEqual(production_store.add_calls, [])
        self.assertEqual(production_store.search_calls, [])
        self.assertIs(rag.get_vectorstore, original_getter)


class RunnerOutputTests(unittest.TestCase):
    def test_local_tracing_suppression_restores_environment(self):
        import os

        names = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")
        saved = {name: os.environ.get(name) for name in names}
        try:
            os.environ["LANGSMITH_TRACING"] = "true"
            os.environ.pop("LANGCHAIN_TRACING_V2", None)
            with disable_langsmith_tracing():
                self.assertTrue(all(os.environ.get(name) == "false" for name in names))
            self.assertEqual(os.environ.get("LANGSMITH_TRACING"), "true")
            self.assertIsNone(os.environ.get("LANGCHAIN_TRACING_V2"))
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_local_results_are_jsonl_and_evaluators_receive_case_reference(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "results") as tmp:
            output_path = Path(tmp) / "out.jsonl"
            cases = [RAG_EXAMPLES[0]]
            evaluator = Mock(return_value={"key": "dummy", "score": 1})
            rows = evaluate_records(
                cases,
                lambda inputs: {"answer": "answer", "contexts": []},
                [evaluator],
                output_path,
            )
            parsed = json.loads(output_path.read_text(encoding="utf-8").strip())
            self.assertEqual(len(rows), 1)
            self.assertEqual(parsed["case_id"], "case_001")
            self.assertEqual(parsed["reference_outputs"]["reference_answer"], cases[0]["outputs"]["reference_answer"])
            evaluator.assert_called_once()

    def test_batch_output_covers_all_10_cases_without_external_services(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "results") as tmp:
            output_path = Path(tmp) / "batch.jsonl"
            evaluators = [
                Mock(return_value={"key": name, "score": 4, "metadata": {"status": "scored"}})
                for name in ("retrieval_relevance", "answer_relevance", "groundedness", "correctness")
            ]
            rows = evaluate_records(
                RAG_EXAMPLES,
                lambda inputs: {"answer": f"answer for {inputs['question']}", "contexts": []},
                evaluators,
                output_path,
            )
            written = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 10)
            self.assertEqual(len(written), 10)
            self.assertEqual(len(written[0]["evaluation_results"]), 4)
            self.assertTrue(all(row["outputs"]["answer"] for row in written))

    def test_target_and_evaluator_errors_are_recorded_per_case(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "results") as tmp:
            output_path = Path(tmp) / "errors.jsonl"
            cases = [RAG_EXAMPLES[0]]
            def broken_target(_inputs):
                raise RuntimeError("expected target failure")
            def broken_evaluator(_run, _example):
                raise ValueError("expected judge failure")
            with self.assertLogs("evaluation.rag_eval.runner", level="ERROR"):
                rows = evaluate_records(cases, broken_target, [broken_evaluator], output_path)
            self.assertEqual(len(rows[0]["errors"]), 2)
            parsed = json.loads(output_path.read_text(encoding="utf-8").strip())
            self.assertEqual(parsed["evaluation_results"][0]["metadata"]["status"], "error")


class RetrievalMissingDataTests(unittest.TestCase):
    def test_missing_contexts_is_not_scored_as_empty_retrieval(self):
        missing = score_retrieval({"question": "q"}, {})
        empty = score_retrieval({"question": "q"}, {"contexts": []})
        self.assertEqual(missing["status"], "missing_input")
        self.assertEqual(empty["score"], 0.0)


class ResilientEvaluationTests(unittest.TestCase):
    def test_case_id_present_in_metadata_for_all_examples(self):
        for example in RAG_EXAMPLES:
            self.assertIn("case_id", example)
            self.assertIn("case_id", example.get("metadata", {}))
            self.assertEqual(example["metadata"]["case_id"], example["case_id"])

    def test_groundedness_label_synonyms_are_normalized(self):
        result = compute_groundedness([
            {"label": "supported"},
            {"label": "unsupported"},
            {"label": "not_supported"},
            {"label": "contradictory"},
        ])
        self.assertEqual(result["supported"], 1)
        self.assertEqual(result["not_found"], 2)
        self.assertEqual(result["contradicted"], 1)
        self.assertEqual(result["score"], 1.0)

    def test_retrieval_scoring_handles_none_context_id(self):
        mock_judge = Mock()
        mock_bound = Mock()
        mock_judge.with_structured_output.return_value = mock_bound
        from evaluation.rag_eval.evaluators.common import ContextGrade, RetrievalGrade
        mock_bound.invoke.return_value = RetrievalGrade(
            contexts=[ContextGrade(context_id="ctx_1", score=3, reason="useful", evidence=[])],
            reason="good",
        )
        res = score_retrieval(
            {"question": "test?"},
            {"contexts": [{"context_id": None, "text": "evidence text"}]},
            judge=mock_judge,
        )
        self.assertEqual(res["status"], "scored")
        self.assertEqual(res["score"], 3.0)

    def test_invoke_structured_falls_back_to_json_mode_on_400(self):
        from evaluation.rag_eval.evaluators.common import ScalarGrade, invoke_structured
        mock_judge = Mock()

        def fake_with_structured_output(schema, method="json_schema", **kwargs):
            bound = Mock()
            if method == "json_schema":
                bound.invoke.side_effect = RuntimeError("400 BadRequestError: json_validate_failed")
            else:
                bound.invoke.return_value = ScalarGrade(score=4, reason="fallback success", evidence=[])
            return bound

        mock_judge.with_structured_output.side_effect = fake_with_structured_output
        result = invoke_structured(ScalarGrade, "system", {"key": "val"}, judge=mock_judge)
        self.assertEqual(result.score, 4)
        self.assertEqual(result.reason, "fallback success")


class DatasetSyncTests(unittest.TestCase):
    def test_sync_dataset_creates_when_not_found(self):
        from langsmith.utils import LangSmithNotFoundError
        from evaluation.rag_eval.dataset import sync_dataset
        mock_client = Mock()
        mock_client.read_dataset.side_effect = LangSmithNotFoundError("not found")
        fake_ds = Mock()
        fake_ds.id = "ds_123"
        mock_client.create_dataset.return_value = fake_ds
        mock_client.list_examples.return_value = []

        ds = sync_dataset(client=mock_client)
        self.assertEqual(ds.id, "ds_123")
        mock_client.create_dataset.assert_called_once()
        mock_client.create_examples.assert_called_once()

    def test_sync_dataset_reraises_auth_error_without_creating(self):
        from evaluation.rag_eval.dataset import sync_dataset
        mock_client = Mock()
        mock_client.read_dataset.side_effect = PermissionError("401 Unauthorized")

        with self.assertRaises(PermissionError):
            sync_dataset(client=mock_client)
        mock_client.create_dataset.assert_not_called()

    def test_sync_dataset_rejects_remote_examples_missing_case_id(self):
        from evaluation.rag_eval.dataset import sync_dataset
        mock_client = Mock()
        fake_ds = Mock()
        fake_ds.id = "ds_123"
        mock_client.read_dataset.return_value = fake_ds
        mock_example = Mock()
        mock_example.metadata = None
        mock_client.list_examples.return_value = [mock_example]

        with self.assertRaisesRegex(ValueError, "missing 'metadata.case_id'"):
            sync_dataset(client=mock_client)


class RetrievalJudgeOmissionTests(unittest.TestCase):
    def test_judge_omits_context_marks_partial_without_artificial_penalty(self):
        mock_judge = Mock()
        mock_bound = Mock()
        mock_judge.with_structured_output.return_value = mock_bound
        from evaluation.rag_eval.evaluators.common import ContextGrade, RetrievalGrade
        mock_bound.invoke.return_value = RetrievalGrade(
            contexts=[ContextGrade(context_id="ctx_1", score=4, reason="excellent evidence", evidence=["evidence"])],
            reason="graded ctx_1",
        )
        res = score_retrieval(
            {"question": "test?"},
            {"contexts": [{"context_id": "ctx_1", "text": "t1"}, {"context_id": "ctx_2", "text": "t2"}]},
            judge=mock_judge,
        )
        self.assertEqual(res["status"], "partial")
        self.assertEqual(res["score"], 4.0)
        self.assertEqual(res["unscored_context_ids"], ["ctx_2"])


class MetricConsistencyAndDetailTests(unittest.TestCase):
    def test_evaluator_details_are_preserved_in_metadata(self):
        from evaluation.rag_eval.evaluators.groundedness import evaluate_groundedness
        from evaluation.rag_eval.evaluators.common import GroundednessGrade, ClaimGrade
        mock_grade = GroundednessGrade(
            claims=[ClaimGrade(claim="c1", label="supported", context_ids=["ctx_1"], evidence=["ev1"])],
            reason="well grounded",
        )
        with patch("evaluation.rag_eval.evaluators.groundedness.invoke_structured", return_value=mock_grade):
            res = evaluate_groundedness(
                {"outputs": {"answer": "c1", "contexts": [{"context_id": "ctx_1", "text": "ev1"}]}},
                {"inputs": {"question": "q"}},
            )
        self.assertEqual(res["key"], "groundedness")
        self.assertEqual(res["score"], 4.0)
        self.assertIn("claims", res["metadata"])
        self.assertEqual(len(res["metadata"]["claims"]), 1)
        self.assertEqual(res["metadata"]["supported"], 1)

    def test_runner_metric_key_consistent_on_failure(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "results") as tmp:
            output_path = Path(tmp) / "fail.jsonl"
            def broken_evaluator(_run, _example):
                raise RuntimeError("judge down")
            broken_evaluator.__name__ = "evaluate_groundedness"
            broken_evaluator.metric_key = "groundedness"

            rows = evaluate_records([RAG_EXAMPLES[0]], lambda i: {"answer": "a", "contexts": []}, [broken_evaluator], output_path)
            self.assertEqual(rows[0]["evaluation_results"][0]["key"], "groundedness")

    def test_groundedness_grade_allows_missing_reason(self):
        from evaluation.rag_eval.evaluators.common import GroundednessGrade
        data = {
            "claims": [
                {"claim": "User must provide title", "label": "supported", "context_ids": ["ctx_1"]}
            ]
        }
        grade = GroundednessGrade.model_validate(data)
        self.assertEqual(len(grade.claims), 1)
        self.assertEqual(grade.reason, "")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
