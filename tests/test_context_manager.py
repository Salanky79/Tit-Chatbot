import unittest
from unittest.mock import MagicMock, patch
from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from langchain_core.documents import Document

from context_manager import (
    estimate_tokens,
    estimate_message_tokens,
    estimate_tool_schemas_tokens,
    calculate_token_breakdown,
    group_messages_into_turns,
    summarize_old_messages,
    trim_conversation_history,
    prune_old_tool_results,
    validate_and_isolate_summarization_context,
    is_isolated_document_task,
    get_model_token_budget,
    _deduplicate_summary_lines,
    enforce_token_budget,
    prepare_context_for_llm,
)


class TestContextManager(unittest.TestCase):

    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens(None), 0)
        # "Xin chào Việt Nam!" is 18 chars -> ceil(18/3) = 6
        self.assertEqual(estimate_tokens("Xin chào Việt Nam!"), 6)

    def test_estimate_message_tokens(self):
        msg = HumanMessage(content="Hello world")
        tokens = estimate_message_tokens(msg)
        self.assertGreater(tokens, 0)
        self.assertGreaterEqual(tokens, 4 + estimate_tokens("Hello world"))

        ai_msg = AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "web_search", "args": {"query": "test"}}]
        )
        tokens_ai = estimate_message_tokens(ai_msg)
        self.assertGreater(tokens_ai, 4)

    def test_estimate_tool_schemas_tokens(self):
        class DummyTool:
            name = "test_tool"
            description = "A dummy tool for testing token schema calculation"
            args = {"query": {"type": "string"}}

        tokens = estimate_tool_schemas_tokens([DummyTool()])
        self.assertGreater(tokens, 20)

    def test_calculate_token_breakdown(self):
        messages = [
            HumanMessage(content="Question 1"),
            AIMessage(content="Answer 1"),
            ToolMessage(content="Some tool result", tool_call_id="call_0"),
        ]
        breakdown = calculate_token_breakdown(
            messages=messages,
            system_prompt="You are an assistant.",
            tools=None,
            expected_output_tokens=500,
        )
        self.assertGreater(breakdown["system_tokens"], 0)
        self.assertGreater(breakdown["history_tokens"], 0)
        self.assertGreater(breakdown["tool_results_tokens"], 0)
        self.assertEqual(breakdown["expected_output_tokens"], 500)
        expected_total = (
            breakdown["system_tokens"]
            + breakdown["tool_schemas_tokens"]
            + breakdown["history_tokens"]
            + breakdown["tool_results_tokens"]
            + 500
        )
        self.assertEqual(breakdown["total_tokens"], expected_total)

    def test_group_messages_into_turns(self):
        messages = [
            HumanMessage(content="Turn 1 Q"),
            AIMessage(content="Turn 1 A"),
            HumanMessage(content="Turn 2 Q"),
            AIMessage(content="", tool_calls=[{"id": "1", "name": "search", "args": {}}]),
            ToolMessage(content="search results", tool_call_id="1"),
            AIMessage(content="Turn 2 A"),
            HumanMessage(content="Turn 3 Q"),
        ]
        turns = group_messages_into_turns(messages)
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[0][0].content, "Turn 1 Q")
        self.assertEqual(turns[1][0].content, "Turn 2 Q")
        self.assertEqual(len(turns[1]), 4)
        self.assertEqual(turns[2][0].content, "Turn 3 Q")

    def test_trim_conversation_history_within_limit(self):
        messages = [
            HumanMessage(content="Turn 1"),
            AIMessage(content="Answer 1"),
            HumanMessage(content="Turn 2"),
            AIMessage(content="Answer 2"),
        ]
        recent, summary = trim_conversation_history(messages, max_turns=3)
        self.assertEqual(len(recent), 4)
        self.assertEqual(summary, "")

    @patch("context_manager.ChatGroq")
    def test_trim_conversation_history_exceeding_limit(self, mock_chat_groq):
        mock_instance = MagicMock()
        mock_chat_groq.return_value = mock_instance
        mock_instance.invoke.return_value = AIMessage(content="- User is Alice who likes Python.")

        messages = [
            HumanMessage(content="Turn 1: My name is Alice"),
            AIMessage(content="Answer 1: Nice to meet you Alice"),
            HumanMessage(content="Turn 2: I like Python"),
            AIMessage(content="Answer 2: Python is great"),
            HumanMessage(content="Turn 3: What is fastAPI?"),
            AIMessage(content="Answer 3: FastAPI is a modern web framework"),
        ]
        # Keep only last 1 turn
        recent, summary = trim_conversation_history(messages, max_turns=1)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0].content, "Turn 3: What is fastAPI?")
        self.assertEqual(recent[1].content, "Answer 3: FastAPI is a modern web framework")
        self.assertTrue("Alice" in summary or "Python" in summary)

    def test_deduplicate_summary_lines(self):
        text = (
            "- Người dùng là Sơn.\n"
            "- Người dùng là Sơn.\n"
            "- Người dùng là Sơn\n"
            "- Thích câu trả lời ngắn gọn."
        )
        deduped = _deduplicate_summary_lines(text)
        self.assertEqual(deduped.count("Người dùng là Sơn"), 1)
        self.assertIn("Thích câu trả lời ngắn gọn.", deduped)

    def test_get_model_token_budget(self):
        budget_120b = get_model_token_budget("openai/gpt-oss-120b")
        self.assertEqual(budget_120b["max_context_tokens"], 2800)
        self.assertEqual(budget_120b["max_tool_result_tokens"], 1000)
        self.assertEqual(budget_120b["expected_output_tokens"], 800)

        budget_20b = get_model_token_budget("openai/gpt-oss-20b")
        self.assertEqual(budget_20b["max_context_tokens"], 6000)

        budget_default = get_model_token_budget(None)
        self.assertEqual(budget_default["max_context_tokens"], 6000)

    def test_is_isolated_document_task(self):
        msg_file = [HumanMessage(content="tóm tắt file này đi 📎 Tiểu luận CNXHKH.pdf")]
        self.assertTrue(is_isolated_document_task(msg_file))

        msg_attached = [HumanMessage(content="Giải thích nội dung trong [Attached file: report.docx]")]
        self.assertTrue(is_isolated_document_task(msg_attached))

        msg_chat = [HumanMessage(content="Xin chào, bạn có thể giúp tôi viết code Python không?")]
        self.assertFalse(is_isolated_document_task(msg_chat))

        # Yêu cầu dịch / tóm tắt lại sang tiếng Việt khi đã có bản tóm tắt trước đó không bị cô lập
        msg_followup = [
            HumanMessage(content="Tóm tắt tài liệu đính kèm"),
            AIMessage(content='Summary of "NguyenThaiSon_CV.pdf" (all 4 stored chunks):\n\nNguyen Thai Son - Summary...'),
            HumanMessage(content="Hãy tóm tắt bằng tiếng Việt"),
        ]
        self.assertFalse(is_isolated_document_task(msg_followup))

    def test_prepare_context_for_llm_isolates_document_task(self):
        messages = [
            HumanMessage(content="Turn 1: Phân tích ảnh hoa trắng"),
            AIMessage(content="Ảnh này có bông hoa trắng trên nền đen."),
            HumanMessage(content="Turn 2: tóm tắt file này đi 📎 Tiểu luận CNXHKH.pdf"),
        ]
        existing_summary = "- Người dùng là Sơn thích ngắm hoa trắng."

        final_messages, updated_summary = prepare_context_for_llm(
            messages=messages,
            system_prompt="You are a helpful assistant.",
            existing_summary=existing_summary,
            model_name="openai/gpt-oss-120b",
        )

        sys_msg = final_messages[0]
        # Bắt buộc: KHÔNG ĐƯỢC chứa background context hoa trắng hay tên Sơn vào prompt tài liệu
        self.assertNotIn("Người dùng là Sơn thích ngắm hoa trắng", sys_msg.content)
        self.assertNotIn("[CONTEXT FROM EARLIER CONVERSATION", sys_msg.content)
        self.assertIn("CRITICAL TASK ISOLATION INSTRUCTIONS", sys_msg.content)

        # Bắt buộc: Danh sách tin nhắn chỉ giữ lượt active về file
        human_msgs = [m for m in final_messages if isinstance(m, HumanMessage)]
        self.assertEqual(len(human_msgs), 1)
        self.assertIn("tóm tắt file", human_msgs[0].content)
        self.assertFalse(any("Phân tích ảnh hoa trắng" in getattr(m, "content", "") for m in final_messages))

    def test_prune_old_tool_results(self):
        messages = [
            HumanMessage(content="Turn 1: Search for weather in Hanoi"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_1", "name": "web_search", "args": {"query": "weather Hanoi"}}]
            ),
            ToolMessage(
                content="Hanoi weather: 25C sunny, 10-day forecast: day 1 sunny, day 2 rainy...",
                tool_call_id="call_1"
            ),
            AIMessage(content="The weather in Hanoi is 25C and sunny."),
            HumanMessage(content="Turn 2: What about Da Nang?"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_2", "name": "web_search", "args": {"query": "weather Da Nang"}}]
            ),
            ToolMessage(
                content="Da Nang weather: 28C clear sky.",
                tool_call_id="call_2"
            ),
        ]

        pruned = prune_old_tool_results(messages)

        # In turn 1, the old ToolMessage and intermediate tool-calling AIMessage MUST BE GONE
        turn_1_tool_msgs = [m for m in pruned if isinstance(m, ToolMessage) and m.tool_call_id == "call_1"]
        self.assertEqual(len(turn_1_tool_msgs), 0)

        # In turn 1, the HumanMessage and final AIMessage MUST REMAIN
        self.assertTrue(any(isinstance(m, HumanMessage) and "Turn 1" in m.content for m in pruned))
        self.assertTrue(any(isinstance(m, AIMessage) and "weather in Hanoi is 25C" in m.content for m in pruned))

        # In active turn (Turn 2), the ToolMessage MUST BE PRESERVED with its full content!
        active_tool_msgs = [m for m in pruned if isinstance(m, ToolMessage) and m.tool_call_id == "call_2"]
        self.assertEqual(len(active_tool_msgs), 1)
        self.assertIn("Da Nang weather: 28C", active_tool_msgs[0].content)

    def test_validate_and_isolate_summarization_context(self):
        docs = [
            Document(page_content="Chapter 1 content", metadata={"page": 1, "source": "book.pdf", "extra": "leak"}),
            Document(page_content="Chapter 2 content", metadata={"page": 2, "source": "book.pdf"}),
        ]
        isolated = validate_and_isolate_summarization_context(docs)
        self.assertEqual(len(isolated), 2)
        self.assertEqual(isolated[0].page_content, "Chapter 1 content")
        self.assertEqual(isolated[0].metadata["page"], 1)
        self.assertEqual(isolated[0].metadata["source"], "book.pdf")
        self.assertNotIn("extra", isolated[0].metadata)

        with self.assertRaises(ValueError):
            validate_and_isolate_summarization_context([])

        with self.assertRaises(ValueError):
            validate_and_isolate_summarization_context([Document(page_content="   ")])

    def test_enforce_token_budget_truncates_huge_tool_result(self):
        huge_result = "Result chunk: " + ("x" * 15000)
        messages = [
            HumanMessage(content="User query"),
            ToolMessage(content=huge_result, tool_call_id="call_x"),
        ]
        budgeted = enforce_token_budget(
            messages=messages,
            system_prompt="System prompt",
            max_context_tokens=3000,
            max_tool_result_tokens=500,
        )
        tool_msg = next(m for m in budgeted if isinstance(m, ToolMessage))
        self.assertLess(len(tool_msg.content), len(huge_result))
        self.assertIn("rút gọn", tool_msg.content)

    def test_enforce_token_budget_trims_older_history_when_over_budget(self):
        messages = [
            HumanMessage(content="Old query 1 " * 50),
            AIMessage(content="Old answer 1 " * 50),
            HumanMessage(content="Recent query"),
        ]
        budgeted = enforce_token_budget(
            messages=messages,
            system_prompt="System prompt",
            max_context_tokens=300,
        )
        self.assertTrue(any("Recent query" in getattr(m, "content", "") for m in budgeted))
        self.assertLess(len(budgeted), len(messages))

    @patch("context_manager.ChatGroq")
    def test_prepare_context_for_llm_pipeline(self, mock_chat_groq):
        mock_instance = MagicMock()
        mock_chat_groq.return_value = mock_instance
        mock_instance.invoke.return_value = AIMessage(content="- User name is Bob.")

        messages = [
            HumanMessage(content="Turn 1: My name is Bob"),
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "remember", "args": {}}]),
            ToolMessage(content="Memory saved", tool_call_id="c1"),
            AIMessage(content="I remembered your name is Bob."),
            HumanMessage(content="Turn 2: What is 2 + 2?"),
            AIMessage(content="2 + 2 = 4."),
            HumanMessage(content="Turn 3: Search news"),
            AIMessage(content="", tool_calls=[{"id": "c2", "name": "search", "args": {}}]),
            ToolMessage(content="Breaking news content", tool_call_id="c2"),
        ]

        final_messages, summary = prepare_context_for_llm(
            messages=messages,
            system_prompt="You are a helpful assistant.",
            max_history_turns=2,
            max_context_tokens=5000,
        )

        self.assertIsInstance(final_messages[0], SystemMessage)
        self.assertIn("You are a helpful assistant.", final_messages[0].content)
        self.assertNotEqual(summary, "")
        self.assertTrue(any("Bob" in getattr(m, "content", "") for m in final_messages if isinstance(m, SystemMessage)))
        self.assertTrue(any(isinstance(m, ToolMessage) and m.tool_call_id == "c2" for m in final_messages))
        self.assertFalse(any(isinstance(m, ToolMessage) and m.tool_call_id == "c1" for m in final_messages))


if __name__ == "__main__":
    unittest.main()
