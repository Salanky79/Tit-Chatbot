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
from services.summarization import SummaryResult
from utils.token_utils import estimate_tokens


class MemoryStore:
    def __init__(self):
        self.job_id = "test-job"
        self.results = {}
        self.final_summary = None

    def get_results(self, stage):
        return dict(self.results.get(stage, {}))

    def save_result(self, stage, index, content):
        return self.results.setdefault(stage, {}).setdefault(index, content)

    def read_final(self):
        return self.final_summary

    def complete(self, text):
        self.final_summary = self.final_summary or text
        return self.final_summary


class SummaryPipelineTests(unittest.TestCase):
    def setUp(self):
        self.settings = summary.SummarySettings(input_tokens=800, stagger_seconds=0)
        self.documents = [Document(page_content="source " * 350, metadata={"source": "test.docx"})]

    def test_budget_keeps_every_character_and_counts_separators(self):
        items = ["a" * 1000, "b" * 13, "c" * 2000]
        groups = summary.groups_by_budget(items, 256)
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
        self.assertEqual({number for stage, number in calls if stage == "map"},
                         set(range(1, len(store.results["map"]) + 1)) - {i + 1 for i in completed})
        self.assertTrue(any(stage == "final" for stage, _ in calls))

    def test_reduce_quota_keeps_completed_level_and_resumes(self):
        store = MemoryStore()

        def invoke(llm, prompt, stage, number, **kwargs):
            if stage == "final":
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
        settings = summary.SummarySettings(input_tokens=800, reduce_tokens=1350, stagger_seconds=0)
        document = Document(page_content="x" * 30000, metadata={"source": "large.txt"})
        with patch.object(summary, "_invoke", return_value="x" * 300) as invoke:
            result = summary._execute_map_reduce([document], object(), settings, "English", None)
        self.assertEqual(result.status, "completed")
        stages = [call.args[2] for call in invoke.call_args_list]
        self.assertIn("reduce:1", stages)
        self.assertIn("reduce:2", stages)

    def test_single_quota_leaves_job_without_final_or_checkpoint(self):
        store = MemoryStore()
        with (
            patch.object(SummaryJobStore, "open", return_value=store),
            patch.object(summary, "_invoke", side_effect=RuntimeError("daily token limit reached")),
        ):
            result = summary.summarize_documents(
                [Document(page_content="short")], object(), job_context={"user_id": "u"},
            )
        self.assertEqual(result.status, "partial")
        self.assertIsNone(store.final_summary)
        self.assertFalse(store.results)

    def test_partial_words_in_completed_text_do_not_change_status(self):
        store = MemoryStore()
        text = "Hoàn thành: 100%. Partial draft: is a quoted label in the source."
        with (
            patch.object(SummaryJobStore, "open", return_value=store),
            patch.object(summary, "_invoke", return_value=text),
        ):
            result = summary.summarize_documents([Document(page_content="short")], object(), job_context={"user_id": "u"})
        self.assertEqual(result.status, "completed")
        self.assertEqual(store.final_summary, text)

    def test_cache_hit_does_not_build_or_invoke_model(self):
        store = MemoryStore()
        store.final_summary = "cached"
        with (
            patch.object(SummaryJobStore, "open", return_value=store),
            patch.object(summary, "_build_groq_llm") as build,
        ):
            result = summary.summarize_documents(Mock(side_effect=AssertionError("loaded chunks on cache hit")),
                                                 job_context={"user_id": "u"})
        self.assertEqual(result, SummaryResult("cached"))
        build.assert_not_called()

    def test_storage_failure_keeps_original_exception(self):
        with (
            patch.object(SummaryJobStore, "open", side_effect=ValueError("original failure")),
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

    def test_interrupted_intermediate_reduce_resumes_only_missing_groups(self):
        store = MemoryStore()
        settings = summary.SummarySettings(input_tokens=800, reduce_tokens=1350, workers=1, stagger_seconds=0)
        docs = [Document(page_content="x" * 30000, metadata={"source": "large.txt"})]
        calls = []
        fail = True

        def invoke(llm, prompt, stage, number, **kwargs):
            calls.append((stage, number))
            if fail and (stage, number) == ("reduce:1", 2):
                raise RuntimeError("daily token limit reached")
            return "x" * 300

        with patch.object(summary, "_invoke", side_effect=invoke):
            self.assertEqual(summary._execute_map_reduce(docs, object(), settings, "English", store).status, "partial")
            self.assertEqual(set(store.results["reduce:1"]), {0})
            fail = False
            calls.clear()
            self.assertEqual(summary._execute_map_reduce(docs, object(), settings, "English", store).status, "completed")
        self.assertEqual(calls[0], ("reduce:1", 2))
        self.assertNotIn(("reduce:1", 1), calls)
        self.assertFalse(any(stage == "map" for stage, _ in calls))

    def test_verified_overlap_is_removed_only_at_matching_offsets(self):
        def chunk(text, start, page=1):
            return Document(page_content=text, metadata={"page": page, "start_index": start})
        chunks = [chunk("abc123", 0), chunk("123xyz", 3), chunk("xyz!", 6),
                  chunk("xyz!", 30), chunk("xyz!", 0, 2)]
        self.assertEqual(summary.page_texts(chunks),
                         ["[Page 1]\nabc123xyz!", "[Page 1]\nxyz!", "[Page 2]\nxyz!"])

    def test_four_groups_resume_uses_only_missing_group_and_committed_content(self):
        store = MemoryStore()
        store.results["map"] = {0: "first", 1: "second", 3: "fourth"}
        with patch.object(summary, "_invoke", return_value="third") as invoke:
            texts = summary._invoke_groups_parallel(["a", "b", "c", "d"], "map", object(), "auto",
                                                     settings=self.settings, job_store=store)
        self.assertEqual(texts, ["first", "second", "third", "fourth"])
        self.assertEqual(invoke.call_args.args[1], "c")

    def test_configuration_rejects_invalid_values(self):
        for kwargs in ({"input_tokens": 0}, {"reduce_tokens": 0}, {"workers": 0}, {"stagger_seconds": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                summary.SummarySettings(**kwargs)


if __name__ == "__main__":
    unittest.main()
