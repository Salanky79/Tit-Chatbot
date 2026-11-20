"""Async behavior checks for follow-up generation and its API route."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import FollowupRequest, chat_followups
from services.followups import FollowupSuggestions, generate_followup_questions


class FollowupServiceAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_async_structured_model_and_returns_valid_prompts(self) -> None:
        prompts = ["Bạn muốn tìm hiểu thêm về tuyển sinh?", "Bạn cần xem học phí không?"]
        structured_model = MagicMock()
        structured_model.ainvoke = AsyncMock(
            return_value={
                "raw": None,
                "parsed": FollowupSuggestions(prompts=prompts),
                "parsing_error": None,
            }
        )
        model = MagicMock()
        model.with_structured_output.return_value = structured_model

        with (
            patch("services.followups.FOLLOWUPS_ENABLED", True),
            patch("services.followups.ChatGroq", return_value=model),
        ):
            result = await generate_followup_questions(
                user_message="Tuyển sinh TDTU thế nào?",
                assistant_answer="TDTU có nhiều phương thức tuyển sinh.",
                model_name="test-model",
            )

        self.assertEqual(result, prompts)
        structured_model.ainvoke.assert_awaited_once()
        model.with_structured_output.assert_called_once_with(
            FollowupSuggestions,
            method="json_schema",
            include_raw=True,
        )

    async def test_provider_exception_fails_safe_to_empty_list(self) -> None:
        structured_model = MagicMock()
        structured_model.ainvoke = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        model = MagicMock()
        model.with_structured_output.return_value = structured_model

        with (
            patch("services.followups.FOLLOWUPS_ENABLED", True),
            patch("services.followups.ChatGroq", return_value=model),
        ):
            result = await generate_followup_questions(
                user_message="Question",
                assistant_answer="Answer",
            )

        self.assertEqual(result, [])
        structured_model.ainvoke.assert_awaited_once()

    async def test_disabled_setting_skips_model_call(self) -> None:
        with (
            patch("services.followups.FOLLOWUPS_ENABLED", False),
            patch("services.followups.ChatGroq") as chat_groq,
        ):
            result = await generate_followup_questions("Question", "Answer")

        self.assertEqual(result, [])
        chat_groq.assert_not_called()


class FollowupRouteAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_awaits_service_and_returns_two_suggestions(self) -> None:
        suggestions = ["Câu hỏi gợi ý 1?", "Câu hỏi gợi ý 2?"]
        with (
            patch("app.FOLLOWUPS_ENABLED", True),
            patch("app.generate_followup_questions", new_callable=AsyncMock) as service,
        ):
            service.return_value = suggestions
            response = await chat_followups(
                FollowupRequest(
                    user_message="Câu hỏi ban đầu",
                    assistant_answer="Câu trả lời",
                    model="test-model",
                )
            )

        self.assertEqual(response, {"success": True, "suggestions": suggestions})
        service.assert_awaited_once_with(
            user_message="Câu hỏi ban đầu",
            assistant_answer="Câu trả lời",
            model_name="test-model",
        )

    async def test_route_skips_service_when_input_is_empty(self) -> None:
        with (
            patch("app.FOLLOWUPS_ENABLED", True),
            patch("app.generate_followup_questions", new_callable=AsyncMock) as service,
        ):
            response = await chat_followups(
                FollowupRequest(user_message="Question", assistant_answer=" ")
            )

        self.assertEqual(response, {"success": True, "suggestions": []})
        service.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
