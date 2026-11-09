import unittest
from types import SimpleNamespace
from langchain_core.documents import Document

from summarization import summarize_documents


class FakeLLM:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def invoke(self, messages):
        self.calls.append(messages)
        if self.fail_at == len(self.calls):
            raise RuntimeError("simulated provider failure")
        return SimpleNamespace(content=f"summary-{len(self.calls)}")


class TestSummarizationIntegration(unittest.TestCase):

    def test_map_reduce_processes_large_unstructured_document_at_multiple_levels(self):
        docs = [Document(page_content=(f"Text block {i}. " + "fact " * 180)) for i in range(18)]
        llm = FakeLLM()

        result = summarize_documents(docs, llm, input_token_budget=500)

        self.assertTrue(result.startswith("summary-"))
        self.assertGreater(len(llm.calls), 18)
        map_prompts = [
            m[1].content for m in llm.calls
            if m[1].content.startswith("Summarize this part")
        ]
        joined = "\n".join(map_prompts)
        for i in range(18):
            self.assertIn(f"Text block {i}.", joined)

    def test_small_document_uses_one_map_call_and_returns_its_summary(self):
        llm = FakeLLM()
        result = summarize_documents([Document(page_content="Short content.")], llm)
        self.assertEqual(result, "summary-1")
        self.assertEqual(len(llm.calls), 1)

    def test_summarization_propagates_llm_failure_instead_of_returning_partial_result(self):
        docs = [Document(page_content=("content " * 400)) for _ in range(4)]
        with self.assertRaises(RuntimeError):
            summarize_documents(docs, FakeLLM(fail_at=2), input_token_budget=500)

    def test_summarization_strictly_isolates_context_without_chat_history(self):
        docs = [
            Document(page_content="Section 1: Data structures.", metadata={"page": 1, "source": "guide.pdf"}),
            Document(page_content="Section 2: Algorithms.", metadata={"page": 2, "source": "guide.pdf"}),
        ]
        llm = FakeLLM()
        result = summarize_documents(docs, llm)
        self.assertEqual(result, "summary-1")

        # Verify that every call to LLM contains ONLY [SystemMessage, HumanMessage]
        # and NEVER contains chat history or conversation messages
        for call in llm.calls:
            self.assertEqual(len(call), 2)
            sys_msg, human_msg = call
            self.assertEqual(sys_msg.__class__.__name__, "SystemMessage")
            self.assertEqual(human_msg.__class__.__name__, "HumanMessage")
            self.assertNotIn("User:", human_msg.content)
            self.assertNotIn("Assistant:", human_msg.content)


    def test_parallel_map_preserves_document_order_and_handles_multiple_workers(self):
        docs = [
            Document(page_content=f"Part {i}: Important facts about section {i} " + "detail " * 100)
            for i in range(1, 6)
        ]
        llm = FakeLLM()
        result = summarize_documents(docs, llm, input_token_budget=600)
        self.assertTrue(result.startswith("summary-"))
        self.assertGreaterEqual(len(llm.calls), 3)

    def test_clean_source_name_normalizes_uuid_and_spaces(self):
        from tools import _clean_source_name
        raw_source = "099a6ec2-3eed-4dbb-8de4-23bdb6a591bf_Nguyễn_Thái_Sơn_-_25021970_-_Tiểu_luận_CNXHKH_-_Đề_1_.pdf"
        user_filename = "Nguyễn Thái Sơn - 25021970 - Tiểu luận CNXHKH - Đề 1 .pdf"
        self.assertEqual(_clean_source_name(raw_source), _clean_source_name(user_filename))

    def test_parse_wait_time_handles_minutes_and_seconds(self):
        from summarization import _parse_wait_time
        self.assertAlmostEqual(_parse_wait_time("Please try again in 14m20.112s."), 860.112, places=2)
        self.assertAlmostEqual(_parse_wait_time("Please try again in 5.5s."), 5.5, places=2)
        self.assertIsNone(_parse_wait_time("No wait time specified."))

    def test_invoke_fails_fast_on_tpd_daily_limit(self):
        from summarization import _invoke

        class FailingDailyLimitLLM:
            def __init__(self):
                self.calls = 0

            def invoke(self, messages):
                self.calls += 1
                raise RuntimeError(
                    "Error code: 429 - Rate limit reached on tokens per day (TPD): Limit 200000, Used 198154. "
                    "Please try again in 14m20.112s."
                )

        import time
        start = time.time()
        failing_llm = FailingDailyLimitLLM()
        with self.assertRaises(RuntimeError) as ctx:
            _invoke(failing_llm, "test prompt", "map", 1, max_retries=4)
        elapsed = time.time() - start

        self.assertIn("rate limit exceeded", str(ctx.exception).lower())
        # Zero retries: must call exactly once and fail immediately (< 0.1s)
        self.assertEqual(failing_llm.calls, 1)
        self.assertLess(elapsed, 0.5)


if __name__ == "__main__":
    unittest.main()
