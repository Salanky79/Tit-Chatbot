"""Pipeline tests with a deterministic model and in-memory checkpoints."""

import os
import unittest
from contextvars import ContextVar
from unittest.mock import Mock, patch

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain_core.documents import Document
from services import summarization as summary
from services.summary_jobs import SummaryJobStore
from services.summary_result import SummaryResult
from utils.token_utils import estimate_tokens


class MemoryStore:
    def __init__(self):
        self.job_id = "test-job"
        self.results = {}
        self.final = None
        self.status = None

    def get_results(self, stage):
        return dict(self.results.get(stage, {}))

    def save_result(self, stage, index, content):
        self.results.setdefault(stage, {})[index] = content

    def get_final(self):
        return self.final

    def start(self):
        self.status = "running"

    def complete(self, text):
        self.status, self.final = "completed", text

    def pause(self, reason):
        self.status = "paused"


class SummaryPipelineTests(unittest.TestCase):
    def setUp(self):
        self.settings = summary.SummarySettings(input_tokens=800, stagger_seconds=0)
        self.documents = [Document(page_content="source " * 350, metadata={"source": "test.docx"})]

    def test_budget_keeps_every_character_and_counts_separators(self):
        items = ["a" * 1000, "b" * 13, "c" * 2000]
        groups = summary._groups_by_budget(items, 256)
        self.assertEqual("".join(part for group in groups for part in group), "".join(items))
        self.assertTrue(all(estimate_tokens("\n\n".join(group)) <= 256 for group in groups))

    def test_resume_skips_saved_map_results_then_completes_reduce(self):
        store = MemoryStore()
        fail = True
        calls = []

        def invoke(llm, prompt, stage, number, **kwargs):
            calls.append((stage, number))
            if stage == "map" and number == 2 and fail:
                raise RuntimeError("daily token limit reached")
            return f"summary {stage} {number}"

        with patch.object(summary, "_invoke", side_effect=invoke):
            first = summary._execute_map_reduce(self.documents, object(), self.settings, "English", store)
            self.assertEqual(first.status, "partial")
            completed = set(store.get_results("map"))
            self.assertTrue(completed)
            fail = False
            calls.clear()
            second = summary._execute_map_reduce(self.documents, object(), self.settings, "English", store)
        self.assertEqual(second.status, "completed")
        self.assertEqual([number for stage, number in calls if stage == "map"], [2])
        self.assertTrue(any(stage.startswith("reduce:") for stage, _ in calls))

    def test_reduce_quota_keeps_completed_level_and_resumes(self):
        store = MemoryStore()

        def invoke(llm, prompt, stage, number, **kwargs):
            if stage.startswith("reduce:"):
                raise RuntimeError("daily token limit reached")
            return f"map {number}"

        with patch.object(summary, "_invoke", side_effect=invoke):
            result = summary._execute_map_reduce(self.documents, object(), self.settings, "English", store)
        self.assertEqual(result.status, "partial")
        for content in store.results["map"].values():
            self.assertIn(content, result.text)
        with patch.object(summary, "_invoke", return_value="final") as invoke:
            result = summary._execute_map_reduce(self.documents, object(), self.settings, "English", store)
        self.assertEqual(result, SummaryResult("final"))
        self.assertEqual(invoke.call_count, 1)

    def test_reduce_can_finish_multiple_levels(self):
        settings = summary.SummarySettings(input_tokens=800, reduce_tokens=256, stagger_seconds=0)
        with patch.object(summary, "_invoke", return_value="x" * 300) as invoke:
            result = summary._execute_map_reduce(self.documents, object(), settings, "English", None)
        self.assertEqual(result.status, "completed")
        stages = [call.args[2] for call in invoke.call_args_list]
        self.assertIn("reduce:1", stages)
        self.assertIn("reduce:2", stages)

    def test_partial_result_pauses_job_without_caching_final(self):
        store = MemoryStore()
        with (
            patch.object(SummaryJobStore, "for_document", return_value=store),
            patch.object(summary, "_invoke", side_effect=RuntimeError("daily token limit reached")),
        ):
            result = summary.summarize_documents(
                [Document(page_content="short")], object(), job_context={"user_id": "u"},
            )
        self.assertEqual(result.status, "partial")
        self.assertEqual(store.status, "paused")
        self.assertIsNone(store.final)

    def test_partial_words_in_completed_text_do_not_pause_job(self):
        store = MemoryStore()
        text = "Hoàn thành: 100%. Partial draft: is a quoted label in the source."
        with (
            patch.object(SummaryJobStore, "for_document", return_value=store),
            patch.object(summary, "_invoke", return_value=text),
        ):
            result = summary.summarize_documents([Document(page_content="short")], object(), job_context={"user_id": "u"})
        self.assertEqual(result.status, "completed")
        self.assertEqual(store.status, "completed")
        self.assertEqual(store.final, text)

    def test_cache_hit_does_not_build_or_invoke_model(self):
        store = MemoryStore()
        store.final = "cached"
        with (
            patch.object(SummaryJobStore, "for_document", return_value=store),
            patch.object(summary, "_build_groq_llm") as build,
        ):
            result = summary.summarize_documents(self.documents, job_context={"user_id": "u"})
        self.assertEqual(result, SummaryResult("cached"))
        build.assert_not_called()

    def test_storage_failure_keeps_original_exception(self):
        store = MemoryStore()
        store.start = Mock(side_effect=ValueError("original failure"))
        store.pause = Mock(side_effect=RuntimeError("pause failure"))
        with (
            patch.object(SummaryJobStore, "for_document", return_value=store),
            self.assertLogs(summary.logger, level="ERROR"),
            self.assertRaisesRegex(ValueError, "original failure"),
        ):
            summary.summarize_documents(self.documents, object(), job_context={"user_id": "u"})

    def test_worker_context_and_result_order(self):
        request = ContextVar("summary-test-request", default="missing")
        token = request.set("current-user")
        try:
            with patch.object(summary, "_invoke", side_effect=lambda llm, prompt, *args, **kwargs: request.get() + prompt):
                results = summary._invoke_groups_parallel(["a", "b"], "map", object(), "auto", settings=self.settings)
            self.assertEqual(results, ["current-usera", "current-userb"])
        finally:
            request.reset(token)

    def test_reduce_does_not_force_oversized_requests(self):
        settings = summary.SummarySettings(input_tokens=800, reduce_tokens=256, stagger_seconds=0)
        with patch.object(summary, "_invoke", return_value="x" * 640):
            with self.assertRaisesRegex(ValueError, "Reduce budget"):
                summary._execute_map_reduce(self.documents, object(), settings, "auto", None)

    def test_cache_identity_includes_project_and_reduce_configuration(self):
        with patch("services.summary_jobs.get_collection"):
            def identity(project, reduce):
                return SummaryJobStore.for_document(
                    self.documents, {"user_id": "u", "thread_id": "t", "project_id": project},
                    target_language="auto", input_token_budget=300, pipeline_config={"reduce_tokens": reduce},
                ).job_id
            self.assertNotEqual(identity("a", 500), identity("b", 500))
            self.assertNotEqual(identity("a", 500), identity("a", 1000))
            self.assertEqual(identity("a", 500), identity("a", 500))

    def test_configuration_rejects_invalid_values(self):
        for kwargs in ({"input_tokens": 0}, {"reduce_tokens": 0}, {"workers": 0}, {"stagger_seconds": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                summary.SummarySettings(**kwargs)


if __name__ == "__main__":
    unittest.main()
