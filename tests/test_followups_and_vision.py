import unittest
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage

from followups import (
    _clean_content_for_followup,
    fallback_followup_questions,
    generate_followup_questions,
    MAX_FOLLOWUP_USER_TOKENS,
    MAX_FOLLOWUP_ANSWER_TOKENS,
)
from image_analysis import analyze_image, MAX_IMAGE_QUESTION_TOKENS
from context_manager import estimate_tokens


class TestFollowupsAndVision(unittest.TestCase):

    def test_clean_content_for_followup_removes_code_blocks(self):
        long_code = "```python\n" + ("print('hello world')\n" * 20) + "```"
        raw_text = f"Here is the solution:\n{long_code}\nHope this helps!"
        cleaned = _clean_content_for_followup(raw_text, MAX_FOLLOWUP_ANSWER_TOKENS)

        self.assertNotIn("print('hello world')", cleaned)
        self.assertIn("[Code snippet omitted]", cleaned)
        self.assertIn("Here is the solution:", cleaned)

    def test_clean_content_for_followup_removes_attachments(self):
        raw_text = "What is shown in [Attached image: test.png]? Explain."
        cleaned = _clean_content_for_followup(raw_text, MAX_FOLLOWUP_USER_TOKENS)

        self.assertNotIn("[Attached image: test.png]", cleaned)
        self.assertIn("What is shown in", cleaned)

    def test_clean_content_for_followup_enforces_token_limit(self):
        huge_text = "Important discussion point. " * 300
        limit = 50
        cleaned = _clean_content_for_followup(huge_text, limit)

        # Estimated tokens should be around the limit
        self.assertLessEqual(estimate_tokens(cleaned), limit + 5)
        self.assertTrue(cleaned.endswith("..."))

    def test_fallback_followup_questions_vietnamese(self):
        questions = fallback_followup_questions("Bạn có thể giải thích về Python không?")
        self.assertEqual(len(questions), 2)
        self.assertTrue(any("ví dụ" in q.lower() or "cụ thể" in q.lower() for q in questions))

    def test_fallback_followup_questions_english(self):
        questions = fallback_followup_questions("What is FastAPI framework?")
        self.assertEqual(len(questions), 2)
        self.assertTrue(all(q.endswith("?") for q in questions))

    @patch("image_analysis.Path")
    def test_analyze_image_truncates_oversized_question(self, mock_path_cls):
        mock_path_instance = MagicMock()
        mock_path_cls.return_value = mock_path_instance
        mock_path_instance.resolve.return_value = mock_path_instance
        mock_path_instance.is_relative_to.return_value = True
        mock_path_instance.is_file.return_value = True
        mock_path_instance.suffix = ".jpg"
        mock_path_instance.read_bytes.return_value = b"\xff\xd8\xff\xe0" + b"\x00" * 100

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="The image shows a chart.")

        oversized_question = "Explain this chart in detail. " * 100
        answer = analyze_image("uploads/chart.jpg", oversized_question, llm=mock_llm)

        self.assertEqual(answer, "The image shows a chart.")
        # Check the prompt passed to LLM
        called_args = mock_llm.invoke.call_args[0][0]
        human_msg = called_args[0]
        text_content = human_msg.content[0]["text"]
        self.assertIn("[câu hỏi được rút gọn để đảm bảo ngân sách token]", text_content)


if __name__ == "__main__":
    unittest.main()
