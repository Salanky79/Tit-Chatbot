import unittest
from unittest.mock import MagicMock
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from agent import AgentState, SYSTEM_PROMPT, normalize_model_name
from context_manager import prepare_context_for_llm, calculate_token_breakdown


class TestAgentWorkflow(unittest.TestCase):

    def test_normalize_model_name(self):
        self.assertEqual(normalize_model_name(None), "openai/gpt-oss-20b")
        self.assertEqual(normalize_model_name("   "), "openai/gpt-oss-20b")
        self.assertEqual(normalize_model_name("unknown-model"), "openai/gpt-oss-20b")
        self.assertEqual(normalize_model_name("openai/gpt-oss-120b"), "openai/gpt-oss-120b")

    def test_agent_chatbot_node_workflow_integration(self):
        mock_llm_response = AIMessage(content="Hello! How can I help you today?")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_llm_response

        state: AgentState = {
            "messages": [HumanMessage(content="Hello Tit")],
            "conversation_summary": "",
        }

        final_messages, updated_summary = prepare_context_for_llm(
            messages=state["messages"],
            system_prompt=SYSTEM_PROMPT,
            tools=None,
            existing_summary=state.get("conversation_summary", ""),
            model_name="openai/gpt-oss-20b",
        )

        response = mock_llm.invoke(final_messages)

        self.assertEqual(response.content, "Hello! How can I help you today?")
        self.assertIsInstance(final_messages[0], SystemMessage)
        self.assertIn(SYSTEM_PROMPT.strip(), final_messages[0].content)
        self.assertTrue(any(isinstance(m, HumanMessage) and m.content == "Hello Tit" for m in final_messages))

    def test_agent_prunes_old_tool_messages_across_turns(self):
        # Simulate a conversation after 2 turns where turn 1 had a web search
        state_messages = [
            # Turn 1
            HumanMessage(content="What is Python?"),
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "web_search", "args": {"query": "python"}}]),
            ToolMessage(content="Long web search result about Python 3.12...", tool_call_id="c1"),
            AIMessage(content="Python is a high-level programming language."),
            # Turn 2
            HumanMessage(content="What about FastAPI?"),
        ]

        final_messages, summary = prepare_context_for_llm(
            messages=state_messages,
            system_prompt=SYSTEM_PROMPT,
            tools=None,
            existing_summary="",
        )

        # The old ToolMessage 'c1' and its empty intermediate AIMessage MUST NOT be in final_messages
        tool_c1_msgs = [m for m in final_messages if isinstance(m, ToolMessage) and m.tool_call_id == "c1"]
        self.assertEqual(len(tool_c1_msgs), 0)

        # The clean dialogue must be preserved
        self.assertTrue(any(isinstance(m, HumanMessage) and "What is Python?" in m.content for m in final_messages))
        self.assertTrue(any(isinstance(m, AIMessage) and "Python is a high-level" in m.content for m in final_messages))
        self.assertTrue(any(isinstance(m, HumanMessage) and "What about FastAPI?" in m.content for m in final_messages))

    def test_agent_preserves_active_tool_message_in_current_turn(self):
        # In active turn, tool was just executed and LLM needs to read it
        state_messages = [
            HumanMessage(content="Calculate 125 * 8"),
            AIMessage(content="", tool_calls=[{"id": "calc_1", "name": "calculator", "args": {"expression": "125 * 8"}}]),
            ToolMessage(content="1000", tool_call_id="calc_1"),
        ]

        final_messages, summary = prepare_context_for_llm(
            messages=state_messages,
            system_prompt=SYSTEM_PROMPT,
            tools=None,
            existing_summary="",
        )

        # Active ToolMessage MUST be present!
        active_tools = [m for m in final_messages if isinstance(m, ToolMessage) and m.tool_call_id == "calc_1"]
        self.assertEqual(len(active_tools), 1)
        self.assertEqual(active_tools[0].content, "1000")

    def test_token_budget_calculation_breakdown(self):
        messages = [
            HumanMessage(content="What is AI?"),
            AIMessage(content="AI is Artificial Intelligence."),
        ]
        breakdown = calculate_token_breakdown(
            messages=messages,
            system_prompt=SYSTEM_PROMPT,
            tools=None,
            expected_output_tokens=1000,
        )

        self.assertGreater(breakdown["system_tokens"], 0)
        self.assertGreater(breakdown["history_tokens"], 0)
        self.assertEqual(breakdown["expected_output_tokens"], 1000)
        self.assertGreater(breakdown["total_tokens"], breakdown["system_tokens"])


if __name__ == "__main__":
    unittest.main()
