import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TESTING"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"

import unittest
from unittest.mock import MagicMock, patch
from langchain_core.documents import Document

import rag
import tools


class TestRagUserIsolation(unittest.TestCase):
    def setUp(self):
        # Create an in-memory mock for Chroma vectorstore
        self.mock_docs = []

        self.mock_vectorstore = MagicMock()
        def mock_add_documents(docs):
            self.mock_docs.extend(docs)
        self.mock_vectorstore.add_documents.side_effect = mock_add_documents

        def mock_similarity_search(query, k=4, filter=None):
            filter = filter or {}
            results = []
            for doc in self.mock_docs:
                meta = doc.metadata
                match = True
                if "$and" in filter:
                    for cond in filter["$and"]:
                        for k_cond, v_cond in cond.items():
                            if meta.get(k_cond) != v_cond:
                                match = False
                                break
                else:
                    for k_cond, v_cond in filter.items():
                        if meta.get(k_cond) != v_cond:
                            match = False
                            break
                if match:
                    results.append(doc)
            return results[:k]
        self.mock_vectorstore.similarity_search.side_effect = mock_similarity_search

        mock_collection = MagicMock()
        def mock_coll_get(where=None, include=None):
            where = where or {}
            matched_docs = []
            matched_metas = []
            for doc in self.mock_docs:
                meta = doc.metadata
                match = True
                if "$and" in where:
                    for cond in where["$and"]:
                        for k_cond, v_cond in cond.items():
                            if meta.get(k_cond) != v_cond:
                                match = False
                                break
                else:
                    for k_cond, v_cond in where.items():
                        if meta.get(k_cond) != v_cond:
                            match = False
                            break
                if match:
                    matched_docs.append(doc.page_content)
                    matched_metas.append(dict(meta))
            return {"documents": matched_docs, "metadatas": matched_metas}
        mock_collection.get.side_effect = mock_coll_get
        self.mock_vectorstore._collection = mock_collection

        self.patcher = patch("rag.get_vectorstore", return_value=self.mock_vectorstore)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        tools.set_current_user_id("")
        tools.set_current_thread_id("default")

    def test_document_and_image_isolation_between_users(self):
        # Simulate Alice uploading a document and an image in thread_1
        doc_alice = Document(
            page_content="Alice's confidential financial report for 2026.",
            metadata={
                "thread_id": "thread_1",
                "user_id": "alice_uid",
                "source": "financial_report.pdf",
                "page": 1,
            }
        )
        img_alice = Document(
            page_content="Uploaded image: alice_id_card.png",
            metadata={
                "thread_id": "thread_1",
                "user_id": "alice_uid",
                "source": "alice_id_card.png",
                "file_type": "image",
                "image_path": "uploads/alice_id_card.png",
            }
        )
        self.mock_vectorstore.add_documents([doc_alice, img_alice])

        # Simulate Bob uploading his own document in thread_1
        doc_bob = Document(
            page_content="Bob's public marketing presentation.",
            metadata={
                "thread_id": "thread_1",
                "user_id": "bob_uid",
                "source": "marketing.pdf",
                "page": 1,
            }
        )
        self.mock_vectorstore.add_documents([doc_bob])

        # 1. Alice retrieves from RAG -> receives Alice's document, not Bob's
        alice_rag = rag.retrieve_from_rag("confidential", thread_id="thread_1", user_id="alice_uid")
        self.assertIn("Alice's confidential financial report", alice_rag)
        self.assertNotIn("Bob's public marketing", alice_rag)

        # 2. Bob retrieves from RAG in the same thread_1 -> CANNOT see Alice's document!
        bob_rag = rag.retrieve_from_rag("confidential", thread_id="thread_1", user_id="bob_uid")
        self.assertNotIn("Alice's confidential", bob_rag)
        self.assertIn("Bob's public marketing", bob_rag)

        # 3. Bob queries a thread where he has no documents -> receives no relevant content
        bob_none = rag.retrieve_from_rag("secret", thread_id="thread_2", user_id="bob_uid")
        self.assertEqual(bob_none, "No relevant uploaded document content found.")

        # 4. List document sources
        alice_sources = rag.list_document_sources(thread_id="thread_1", user_id="alice_uid")
        self.assertEqual(alice_sources, ["alice_id_card.png", "financial_report.pdf"])

        bob_sources = rag.list_document_sources(thread_id="thread_1", user_id="bob_uid")
        self.assertEqual(bob_sources, ["marketing.pdf"])

        # 4. List images
        alice_images = rag.list_uploaded_images(thread_id="thread_1", user_id="alice_uid")
        self.assertEqual(len(alice_images), 1)
        self.assertEqual(alice_images[0]["source"], "alice_id_card.png")

        bob_images = rag.list_uploaded_images(thread_id="thread_1", user_id="bob_uid")
        self.assertEqual(len(bob_images), 0)

        # 6. Tools integration: test search_uploaded_documents via CURRENT_USER_ID
        tools.set_current_thread_id("thread_1")
        tools.set_current_user_id("alice_uid")
        alice_tool_result = tools.search_uploaded_documents.invoke({"query": "financial"})
        self.assertIn("Alice's confidential", alice_tool_result)

        tools.set_current_user_id("bob_uid")
        bob_tool_result = tools.search_uploaded_documents.invoke({"query": "financial"})
        self.assertNotIn("Alice's confidential", bob_tool_result)
        self.assertIn("Bob's public marketing", bob_tool_result)


if __name__ == "__main__":
    unittest.main()
