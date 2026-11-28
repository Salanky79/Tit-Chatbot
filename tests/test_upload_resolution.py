"""Current uploads are unambiguous while account fallback remains available."""

import os
import unittest
from unittest.mock import Mock, patch

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain_core.documents import Document
from agent_tools import documents
from services import rag
from agent_tools.context import get_current_attachment_source


class UploadResolutionTests(unittest.TestCase):
    old = "5b0c2b2c-03d4-4188-94aa-ec9c1932928b_ocr_test_scanned.pdf"
    new = "e1c57f29-7f6d-425f-805b-b646f840c617_ocr_test_scanned.pdf"

    def test_attached_upload_resolves_duplicate_names_in_same_thread(self):
        for requested in ("", "ocr_test_scanned.pdf", self.new):
            with self.subTest(requested=requested):
                self.assertEqual(documents._select_uploaded_name(
                    [self.old, self.new], requested, attached_source=self.new,
                ), (self.new, []))

    def test_explicit_request_for_older_upload_takes_precedence(self):
        self.assertEqual(documents._select_uploaded_name(
            [self.old, self.new], self.old, attached_source=self.new,
        ), (self.old, []))

    def test_attachment_preference_cannot_add_an_inaccessible_source(self):
        self.assertEqual(documents._select_uploaded_name(
            [self.old], self.new, attached_source=self.new,
        ), (None, []))
        self.assertEqual(documents._select_uploaded_name(
            ["other.pdf"], "ocr_test_scanned.pdf", attached_source=self.new,
        ), (None, []))

    def test_summary_reads_exact_current_upload(self):
        with (
            patch.object(documents, "list_text_uploads", return_value={"old": self.old, "new": self.new}),
            patch.object(documents, "get_attachment_document_id", return_value="new"),
            patch.object(documents, "get_document_chunks", return_value=[Document(page_content="current")]) as chunks,
            patch.object(documents, "summarize_documents", side_effect=lambda load, **kw: documents.SummaryResult(load()[0].page_content)),
        ):
            result = documents._summarize_upload("ocr_test_scanned.pdf", "auto")
        self.assertEqual(result.status, "completed")
        self.assertEqual(chunks.call_args.args[0], self.new)
        self.assertEqual(chunks.call_args.kwargs["document_id"], "new")

    def test_missing_attachment_id_does_not_select_another_available_file(self):
        with (
            patch.object(documents, "list_text_uploads", return_value={"old": self.old}),
            patch.object(documents, "get_attachment_document_id", return_value="missing"),
            patch.object(documents, "summarize_documents") as summarize,
        ):
            self.assertEqual(documents._summarize_upload("", "auto").status, "clarification")
            summarize.assert_not_called()

    def test_sources_found_in_thread_do_not_query_account(self):
        store = Mock()
        store._collection.get.return_value = {"metadatas": [{"source": self.new, "document_id": "new"}]}
        with patch.object(rag, "get_vectorstore", return_value=store):
            self.assertEqual(rag.list_document_sources("current", "user"), [self.new])
        self.assertEqual(store._collection.get.call_count, 1)
        self.assertEqual(store._collection.get.call_args.kwargs["where"], {
            "$and": [{"thread_id": "current"}, {"scope": "personal"}, {"user_id": "user"}, {"file_type": {"$ne": "image"}}],
        })

    def test_empty_thread_still_falls_back_to_user_sources(self):
        store = Mock()
        store._collection.get.side_effect = [
            {"metadatas": []}, {"metadatas": [{"source": self.old, "document_id": "old"}]},
        ]
        with patch.object(rag, "get_vectorstore", return_value=store):
            self.assertEqual(rag.list_document_sources("empty-thread", "user"), [self.old])
        self.assertEqual(store._collection.get.call_count, 2)
        self.assertIn({"user_id": "user"}, store._collection.get.call_args.kwargs["where"]["$and"])

    def test_search_keeps_current_attachment_filter_during_user_fallback(self):
        store = Mock()
        store.similarity_search.side_effect = [[], [Document(page_content="found", metadata={"source": self.new})]]
        with patch.object(rag, "get_vectorstore", return_value=store):
            result = rag.retrieve_from_rag("question", "thread", "user", source=self.new)
        self.assertIn("found", result)
        self.assertEqual(store.similarity_search.call_count, 2)
        first, second = [call.kwargs["filter"] for call in store.similarity_search.call_args_list]
        self.assertIn({"source": self.new}, first["$and"])
        self.assertIn({"source": self.new}, second["$and"])
        self.assertIn({"user_id": "user"}, second["$and"])

    def test_search_without_attachment_keeps_existing_thread_filter(self):
        store = Mock()
        store.similarity_search.return_value = [Document(page_content="found")]
        with patch.object(rag, "get_vectorstore", return_value=store):
            rag.retrieve_from_rag("question", "thread", "user")
        self.assertEqual(store.similarity_search.call_args.kwargs["filter"], {
            "$and": [{"thread_id": "thread"}, {"user_id": "user"}],
        })

    def test_attachment_context_survives_stream_yields_and_resets_next_request(self):
        from api import chat
        from langchain_core.messages import AIMessageChunk

        observed = []

        class FakeAgent:
            def stream(self, *args, **kwargs):
                for text in ("first", "second"):
                    observed.append(get_current_attachment_source())
                    yield AIMessageChunk(content=text), {"langgraph_node": "chatbot"}

        with patch.object(chat, "save_chat_message"):
            first = chat._chat_event_generator(
                FakeAgent(), "thread", "user", "question", "model", {},
                attachment_source=self.new,
            )
            # Each next call resumes after an SSE yield, like the HTTP streamer.
            list(first)
            list(chat._chat_event_generator(FakeAgent(), "thread", "user", "follow-up", "model", {}))
        self.assertEqual(observed, [self.new, self.new, "", ""])

    def test_mismatched_image_extension_uses_detected_format_and_rejects_invalid_bytes(self):
        import asyncio
        import io
        import tempfile
        from pathlib import Path
        from starlette.datastructures import UploadFile
        from api import documents as api_documents
        from services import image_analysis

        webp_bytes = b"RIFF\x24\x00\x00\x00WEBPVP8 \x18\x00\x00\x00\x00\x00\x00\x00"
        self.assertTrue(api_documents._looks_like_image(webp_bytes, ".jpg"))
        self.assertFalse(api_documents._looks_like_image(b"not an image", ".jpg"))

        with tempfile.TemporaryDirectory() as tmpdir:
            upload_dir = Path(tmpdir)
            with (
                patch.object(api_documents.cfg, "UPLOAD_DIR", str(upload_dir)),
                patch.object(image_analysis.cfg, "UPLOAD_DIR", str(upload_dir)),
                patch.object(api_documents, "get_current_user", return_value={"id": "u1"}),
                patch.object(api_documents, "get_conversation", return_value=None),
                patch.object(api_documents, "create_or_update_conversation"),
                patch.object(api_documents, "add_image_to_rag", return_value={"filename": "photo.jpg", "images": 1}) as add_img,
            ):
                bad_upload = UploadFile(filename="fake.jpg", file=io.BytesIO(b"plain text"))
                bad_resp = asyncio.run(api_documents.upload_document(Mock(), file=bad_upload, thread_id="t1", is_temporary="false", project_id=""))
                self.assertEqual(bad_resp.status_code, 400)

                good_upload = UploadFile(filename="photo.jpg", file=io.BytesIO(webp_bytes))
                good_resp = asyncio.run(api_documents.upload_document(Mock(), file=good_upload, thread_id="t1", is_temporary="false", project_id=""))
                self.assertEqual(good_resp.status_code, 200)
                saved_path = Path(add_img.call_args.args[0])
                self.assertEqual(saved_path.suffix, ".webp")
                self.assertTrue(saved_path.is_file())

                fake_llm = Mock()
                fake_llm.invoke.return_value = Mock(content="ok")
                with patch.object(image_analysis, "log_token_usage"):
                    answer = image_analysis.analyze_image(str(saved_path), "describe", llm=fake_llm)
                self.assertEqual(answer, "ok")
                sent_url = fake_llm.invoke.call_args.args[0][0].content[1]["image_url"]["url"]
                self.assertTrue(sent_url.startswith("data:image/webp;base64,"))


if __name__ == "__main__":
    unittest.main()
