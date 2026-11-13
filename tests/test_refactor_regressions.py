"""Regression checks for the behavior-preserving cleanup."""

import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from agent import route_after_tools
from app import _can_access_conversation, _chat_event_generator
from services.context_manager import prepare_context_for_llm, prune_old_tool_results
from storage.database import (
    _conv_to_obj,
    _public_user,
    list_admin_conversations,
    get_admin_conversation_messages,
)
from services.token_limit_handler import is_daily_token_limit, is_rate_limit, parse_wait_time
from tools import (
    SUMMARY_RESULT_PREFIX_VI,
    _select_uploaded_name,
    summarize_uploaded_document,
)


class UploadedNameSelectionTests(unittest.TestCase):
    def test_selects_normalized_name(self) -> None:
        names = ["b9a44130-0d58-4d2f-9485-4d5f07f32fa3_Research_Notes.pdf", "other.pdf"]

        selected, matches = _select_uploaded_name(names, "Research Notes.pdf")

        self.assertEqual(selected, names[0])
        self.assertEqual(matches, [])

    def test_returns_ambiguous_partial_matches(self) -> None:
        names = ["report_2025.pdf", "report_2026.pdf"]

        selected, matches = _select_uploaded_name(names, "report")

        self.assertIsNone(selected)
        self.assertEqual(matches, names)

    def test_empty_request_keeps_existing_first_item_behavior(self) -> None:
        selected, matches = _select_uploaded_name(["first.pdf", "second.pdf"], "")

        self.assertEqual(selected, "first.pdf")
        self.assertEqual(matches, [])

    def test_summary_tool_uses_defined_vietnamese_prefix(self) -> None:
        with (
            patch("tools.list_document_sources", return_value=["brief.pdf"]),
            patch("tools.get_document_chunks", return_value=[Document(page_content="Text")]),
            patch("tools.summarize_documents", return_value="Summary body"),
        ):
            result = summarize_uploaded_document.func(
                filename="brief.pdf", target_language="Vietnamese"
            )

        self.assertTrue(SUMMARY_RESULT_PREFIX_VI.startswith("Bản tóm tắt"))
        self.assertTrue(result.startswith(f'{SUMMARY_RESULT_PREFIX_VI}"brief.pdf"'))


class ContextAndRoutingTests(unittest.TestCase):
    def test_prunes_completed_tool_protocol_but_keeps_active_turn(self) -> None:
        old_tool_call = AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {}, "id": "call-1", "type": "tool_call"}],
        )
        messages = [
            HumanMessage(content="old question"),
            old_tool_call,
            ToolMessage(content="large old result", tool_call_id="call-1", name="search"),
            AIMessage(content="old answer"),
            HumanMessage(content="current question"),
            AIMessage(content="current answer"),
        ]

        self.assertEqual(
            prune_old_tool_results(messages),
            [messages[0], messages[3], messages[4], messages[5]],
        )

    def test_context_keeps_requested_number_of_recent_turns(self) -> None:
        messages = [
            HumanMessage(content="first"), AIMessage(content="one"),
            HumanMessage(content="second"), AIMessage(content="two"),
        ]

        prepared, summary = prepare_context_for_llm(
            messages, "system", max_history_turns=1
        )

        self.assertEqual([message.content for message in prepared[1:]], ["second", "two"])
        self.assertEqual(summary, "")

    def test_summary_tool_result_routes_directly_to_emit_node(self) -> None:
        result = ToolMessage(
            content='Summary of "brief.pdf"\n\nSummary body',
            tool_call_id="call-1",
            name="summarize_uploaded_document",
        )
        with patch("agent.SUMMARY_DIRECT_RETURN", True):
            self.assertEqual(route_after_tools({"messages": [result]}), "emit_summary")

    def test_extracted_chat_generator_preserves_sse_payloads(self) -> None:
        class FakeAgent:
            call = None

            def stream(self, inputs, *, config, stream_mode):
                self.call = (inputs, config, stream_mode)
                yield AIMessageChunk(content="Hello"), {"langgraph_node": "chatbot"}

        agent = FakeAgent()
        with patch("app.save_chat_message") as save_message:
            events = list(
                _chat_event_generator(
                    agent=agent,
                    thread_id="thread-1",
                    user_id="user-1",
                    user_message="Hi",
                    selected_model="model-x",
                    config={"configurable": {"thread_id": "thread-1"}},
                )
            )

        self.assertEqual(events, ['data: {"token": "Hello"}\n\n', 'data: {"done": true}\n\n'])
        self.assertEqual(
            agent.call,
            (
                {"messages": [HumanMessage(content="Hi")]},
                {"configurable": {"thread_id": "thread-1"}},
                "messages",
            ),
        )
        save_message.assert_called_once_with(
            "thread-1", "assistant", "Hello", user_id="user-1"
        )


class SharedHelperTests(unittest.TestCase):
    def test_rate_limit_helpers_share_wait_time_parsing(self) -> None:
        self.assertEqual(parse_wait_time("try again in 1m30.5s"), 90.5)
        self.assertTrue(is_rate_limit("429 rate limit exceeded"))
        self.assertTrue(is_daily_token_limit("try again in 4m0s"))

    def test_public_user_projection_excludes_credentials(self) -> None:
        result = _public_user(
            {
                "username": "tit",
                "email": "tit@example.com",
                "hashed_password": "secret-hash",
                "full_name": "Tit",
            },
            "user-id",
        )

        self.assertEqual(result["id"], "user-id")
        self.assertNotIn("hashed_password", result)

    def test_object_projection_preserves_attribute_access(self) -> None:
        result = _conv_to_obj({"thread_id": "thread-1"})

        self.assertEqual(result.thread_id, "thread-1")

    def test_conversation_access_requires_matching_owner(self) -> None:
        self.assertTrue(_can_access_conversation({"user_id": None}, None))
        self.assertTrue(
            _can_access_conversation(
                {"user_id": "user-1"}, {"id": "user-1"}
            )
        )
        self.assertFalse(
            _can_access_conversation(
                {"user_id": "user-1"}, {"id": "user-2"}
            )
        )


class AdminConversationTests(unittest.TestCase):
    def test_invalid_user_id_does_not_query_conversations(self) -> None:
        with patch("storage.database.get_collection") as get_collection:
            result = list_admin_conversations("not-an-object-id", page=2)

        self.assertEqual(result, {"items": [], "page": 2, "page_size": 20, "total": 0})
        get_collection.assert_not_called()

    def test_invalid_user_id_cannot_load_messages(self) -> None:
        with patch("storage.database.get_collection") as get_collection:
            result = get_admin_conversation_messages("bad-id", "thread-1")

        self.assertIsNone(result)
        get_collection.assert_not_called()

    def test_message_lookup_verifies_owner_before_reading_messages(self) -> None:
        user_id = ObjectId()
        conversations = MagicMock()
        conversations.find_one.return_value = None

        with patch("storage.database.get_collection", return_value=conversations) as get_collection:
            result = get_admin_conversation_messages(str(user_id), "thread-owned-by-someone-else")

        self.assertIsNone(result)
        conversations.find_one.assert_called_once()
        query = conversations.find_one.call_args.args[0]
        self.assertEqual(query["user_id"], user_id)
        self.assertEqual(query["thread_id"], "thread-owned-by-someone-else")
        get_collection.assert_called_once_with("conversations")

    def test_conversation_list_filters_user_and_applies_pagination(self) -> None:
        user_id = ObjectId()
        conversations = MagicMock()
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = cursor
        conversations.find.return_value = cursor
        conversations.count_documents.return_value = 41

        with patch("storage.database.get_collection", return_value=conversations):
            result = list_admin_conversations(
                str(user_id), page=2, page_size=20, search="help.*"
            )

        self.assertEqual(result["total"], 41)
        self.assertEqual(result["page"], 2)
        query = conversations.find.call_args.args[0]
        self.assertEqual(query["user_id"], user_id)
        self.assertEqual(query["title"], {"$regex": "help\\.\\*", "$options": "i"})
        cursor.skip.assert_called_once_with(20)
        cursor.limit.assert_called_once_with(20)

    def test_message_page_is_filtered_by_owner_and_thread(self) -> None:
        user_id = ObjectId()
        conversations = MagicMock()
        conversations.find_one.return_value = {"thread_id": "thread-1", "title": "Help"}
        messages = MagicMock()
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = cursor
        messages.find.return_value = cursor
        messages.count_documents.return_value = 130

        with patch(
            "storage.database.get_collection", side_effect=[conversations, messages]
        ):
            result = get_admin_conversation_messages(
                str(user_id), "thread-1", page=2, page_size=50
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["total"], 130)
        query = messages.find.call_args.args[0]
        self.assertEqual(query, {"user_id": user_id, "thread_id": "thread-1"})
        cursor.sort.assert_called_once_with("created_at", 1)
        cursor.skip.assert_called_once_with(50)
        cursor.limit.assert_called_once_with(50)


if __name__ == "__main__":
    unittest.main()
