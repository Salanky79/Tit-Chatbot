"""Unit tests for centralized language handling, BCP 47 extraction, and ContextVar propagation."""

from __future__ import annotations

import unittest
from langchain_core.messages import ToolMessage

from services.language import (
    extract_primary_language,
    get_current_language,
    is_vietnamese,
    resolve_request_language,
    set_current_language,
)
from services.token_limit_handler import (
    format_tool_limit_notice,
    handle_partial_stream,
)
from tools import SUMMARY_RESULT_PREFIX, SUMMARY_RESULT_PREFIX_VI
from agent import route_after_tools


class LanguageResolutionTests(unittest.TestCase):
    def test_extract_primary_language_preserves_two_and_three_letter_codes(self) -> None:
        # BCP 47 primary language subtag extraction
        self.assertEqual(extract_primary_language("fil-PH"), "fil")
        self.assertEqual(extract_primary_language("vi-VN"), "vi")
        self.assertEqual(extract_primary_language("en-US"), "en")
        self.assertEqual(extract_primary_language("es-ES"), "es")
        self.assertEqual(extract_primary_language("zh-Hans-CN"), "zh")
        self.assertEqual(extract_primary_language("en_GB"), "en")
        self.assertEqual(extract_primary_language(""), "vi")
        self.assertEqual(extract_primary_language(None), "vi")

    def test_resolve_request_language_prefers_x_language(self) -> None:
        headers = {
            "Accept-Language": "en-US,en;q=0.9",
            "X-Language": "vi-VN",
        }
        self.assertEqual(resolve_request_language(headers), "vi")

    def test_resolve_request_language_falls_back_to_accept_language(self) -> None:
        headers = {"Accept-Language": "fil-PH,fil;q=0.9,en;q=0.8"}
        self.assertEqual(resolve_request_language(headers), "fil")

    def test_resolve_request_language_defaults_to_vi_on_empty(self) -> None:
        self.assertEqual(resolve_request_language({}), "vi")
        self.assertEqual(resolve_request_language(None), "vi")

    def test_context_var_set_and_get(self) -> None:
        set_current_language("en-US")
        self.assertEqual(get_current_language(), "en")
        self.assertFalse(is_vietnamese())

        set_current_language("vi-VN")
        self.assertEqual(get_current_language(), "vi")
        self.assertTrue(is_vietnamese())

    def test_handle_partial_stream_uses_context_language(self) -> None:
        # Vietnamese context
        set_current_language("vi")
        res_vi = handle_partial_stream("Nội dung dở dang", "test-model")
        self.assertIn("Thông báo gián đoạn", res_vi)
        self.assertIn("Nội dung dở dang", res_vi)

        # English / International context (e.g. fil-PH or en-US)
        set_current_language("en")
        res_en = handle_partial_stream("Partial content", "test-model")
        self.assertIn("Notice:", res_en)
        self.assertIn("Partial content", res_en)

    def test_handle_partial_stream_empty_text_uses_context_language(self) -> None:
        set_current_language("vi")
        res_vi = handle_partial_stream("", "test-model")
        self.assertIn("Thông báo", res_vi)

        set_current_language("en")
        res_en = handle_partial_stream("", "test-model")
        self.assertIn("Notice:", res_en)

    def test_format_tool_limit_notice_uses_context_language(self) -> None:
        set_current_language("vi")
        notice_vi = format_tool_limit_notice("analyze_uploaded_image")
        self.assertIn("Analyze Uploaded Image", notice_vi)
        self.assertIn("tạm thời không thể hoàn thành", notice_vi)

        set_current_language("en")
        notice_en = format_tool_limit_notice("analyze_uploaded_image")
        self.assertIn("Analyze Uploaded Image", notice_en)
        self.assertIn("could not complete", notice_en)

    def test_format_tool_limit_notice_dynamic_for_any_tool_without_hardcoded_dictionary(self) -> None:
        # Tự động định dạng bất kỳ tool nào trong số 100+ tools mà không cần từ điển
        notice = format_tool_limit_notice("extract_table_from_pdf")
        self.assertIn("Extract Table From Pdf", notice)


class AgentSummaryRoutingTests(unittest.TestCase):
    def test_route_after_tools_recognizes_english_and_vietnamese_prefixes(self) -> None:
        # English summary tool result
        state_en = {
            "messages": [
                ToolMessage(
                    content=f'{SUMMARY_RESULT_PREFIX}"doc.pdf" (all 5 stored chunks):\n\nSummary text',
                    name="summarize_uploaded_document",
                    tool_call_id="call-1",
                )
            ]
        }
        self.assertEqual(route_after_tools(state_en), "emit_summary")

        # Vietnamese summary tool result
        state_vi = {
            "messages": [
                ToolMessage(
                    content=f'{SUMMARY_RESULT_PREFIX_VI}"doc.pdf" (toàn bộ 5 phần nội dung):\n\nBản tóm tắt',
                    name="summarize_uploaded_document",
                    tool_call_id="call-2",
                )
            ]
        }
        self.assertEqual(route_after_tools(state_vi), "emit_summary")


if __name__ == "__main__":
    unittest.main()
