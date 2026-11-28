"""Regression coverage for summary persistence and terminal tool failures."""

import os
import unittest
from unittest.mock import patch

# These tests exercise local control flow, without exporting traces.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain_core.messages import AIMessage, ToolMessage
from pymongo.errors import DuplicateKeyError

import agent
from agent_tools import documents
from agent_tools.context import SUMMARY_ERROR_PREFIX, SUMMARY_RESULT_PREFIX
from services.summarization import SummaryResult
from langgraph.prebuilt import ToolNode
from langgraph.graph import StateGraph, START, END


class SummaryFlowTests(unittest.TestCase):
    def test_toolnode_preserves_status_for_graph_routing(self):
        workflow = StateGraph(agent.AgentState)
        workflow.add_node("tools", ToolNode([documents.summarize_uploaded_document]))
        workflow.add_node("emit_summary", agent.emit_summary_node)
        workflow.add_edge(START, "tools")
        workflow.add_conditional_edges("tools", agent.route_after_tools, {
            "emit_summary": "emit_summary", "chatbot": END,
        })
        workflow.add_edge("emit_summary", END)
        with (
            patch.object(documents, "_summarize_upload", return_value=SummaryResult("Localized result without a prefix")),
            patch.object(agent, "SUMMARY_DIRECT_RETURN", True),
        ):
            state = workflow.compile().invoke({"messages": [AIMessage(
                content="", tool_calls=[{"name": agent.SUMMARY_TOOL_NAME, "args": {}, "id": "summary-1", "type": "tool_call"}],
            )]})
        self.assertEqual(state["messages"][1].artifact, {"kind": "document_summary", "status": "completed"})
        self.assertIsInstance(state["messages"][-1], AIMessage)
        self.assertEqual(state["messages"][-1].content, "Localized result without a prefix")

    def test_clarification_is_terminal_without_running_summary(self):
        with (
            patch.object(documents, "list_text_uploads", return_value={"a": "a.pdf", "b": "b.pdf"}),
            patch.object(documents, "summarize_documents") as summarize,
        ):
            message = documents.summarize_uploaded_document.invoke({
                "name": agent.SUMMARY_TOOL_NAME, "args": {}, "id": "summary-1", "type": "tool_call",
            })
        summarize.assert_not_called()
        self.assertEqual(message.artifact["status"], "clarification")
        with patch.object(agent, "SUMMARY_DIRECT_RETURN", False):
            self.assertEqual(agent.route_after_tools({"messages": [message]}), "emit_summary")

    def test_source_lookup_failure_also_uses_terminal_error_contract(self):
        with (
            patch.object(documents, "list_text_uploads", side_effect=RuntimeError("storage unavailable")),
            self.assertLogs(documents.logger, level="ERROR"),
        ):
            result = documents._summarize_upload("file.pdf", "auto")
        self.assertEqual(result.status, "error")
        self.assertNotIn("storage unavailable", result.text)

    def test_failure_stops_graph_even_when_direct_return_disabled(self):
        error = SUMMARY_ERROR_PREFIX + '"CV.pdf" do lỗi xử lý nội bộ.'
        state = {"messages": [
            AIMessage(content=""),
            ToolMessage(content=error, name=agent.SUMMARY_TOOL_NAME, tool_call_id="1"),
            ToolMessage(content="Other tool result", name="other", tool_call_id="2"),
        ]}
        with patch.object(agent, "SUMMARY_DIRECT_RETURN", False):
            self.assertEqual(agent.route_after_tools(state), "emit_summary")
        self.assertEqual(agent.emit_summary_node(state)["messages"][0].content, error)

    def test_success_still_returns_summary(self):
        content = SUMMARY_RESULT_PREFIX + '"CV.pdf": summary'
        state = {"messages": [ToolMessage(
            content=content, name=agent.SUMMARY_TOOL_NAME, tool_call_id="1") ]}
        with patch.object(agent, "SUMMARY_DIRECT_RETURN", True):
            self.assertEqual(agent.route_after_tools(state), "emit_summary")
        self.assertEqual(agent.emit_summary_node(state)["messages"][0].content, content)

    def test_tool_database_failure_is_safe_and_terminal(self):
        with (
            patch.object(documents, "list_text_uploads", return_value={"id": "NguyenThaiSon_CV.pdf"}),
            patch.object(documents, "get_document_chunks", return_value=[object()]),
            patch.object(documents, "summarize_documents", side_effect=DuplicateKeyError("job_id: null")) as summarize,
            self.assertLogs(documents.logger, level="ERROR"),
        ):
            result = documents.summarize_uploaded_document.invoke({"filename": "NguyenThaiSon_CV.pdf"})
        self.assertTrue(result.startswith(SUMMARY_ERROR_PREFIX))
        self.assertNotIn("job_id", result)
        self.assertEqual(summarize.call_count, 1)
        self.assertEqual(summarize.call_args.kwargs["target_language"], "auto")

    def test_file_selection_does_not_guess(self):
        self.assertEqual(documents._select_uploaded_name(["a.pdf", "b.pdf"], ""),
                         (None, ["a.pdf", "b.pdf"]))
        self.assertEqual(documents._select_uploaded_name(["a.pdf"], "missing.pdf"), (None, []))
        self.assertEqual(documents._select_uploaded_name(["NguyenThaiSon_CV.pdf"], "NguyenThaiSon_CV.pdf"),
                         ("NguyenThaiSon_CV.pdf", []))


if __name__ == "__main__":
    unittest.main()
