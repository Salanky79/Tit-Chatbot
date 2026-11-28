"""Run against a disposable local server by setting SUMMARY_TEST_MONGOD to the executable."""

import json
import os
import socket
import subprocess
import tempfile
import unittest
from contextlib import chdir
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from bson import ObjectId
from langchain_core.documents import Document
from pymongo import MongoClient

from services import summarization as summary
from services import summary_jobs as repository
from utils.token_utils import CHARS_PER_TOKEN


@unittest.skipUnless(os.environ.get("SUMMARY_TEST_MONGOD"), "set SUMMARY_TEST_MONGOD for local MongoDB tests")
class SummaryMongoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = subprocess.Popen(
            [os.environ["SUMMARY_TEST_MONGOD"], "--dbpath", directory.name, "--bind_ip", "127.0.0.1",
             "--port", str(port), "--logpath", str(Path(directory.name) / "mongo.log")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        def stop_server():
            server.terminate()
            server.wait(timeout=20)
        cls.addClassCleanup(stop_server)
        cls.client = MongoClient("127.0.0.1", port, serverSelectionTimeoutMS=15000)
        cls.addClassCleanup(cls.client.close)
        cls.client.admin.command("ping")

    def setUp(self):
        self.db = self.client[f"summary_test_{ObjectId()}"]
        patcher = patch.object(repository, "get_collection", side_effect=self.db.get_collection)
        patcher.start()
        self.addCleanup(patcher.stop)
        repository.ensure_summary_indexes.cache_clear()
        self.addCleanup(repository.ensure_summary_indexes.cache_clear)
        self.expiry = datetime.now(timezone.utc) + timedelta(days=1)
        self.db.conversations.insert_one({"thread_id": "t", "expires_at": self.expiry})
        self.context = {"thread_id": "t", "user_id": "u", "document_id": "upload-1"}

    def test_concurrent_writers_and_deletion(self):
        barrier = Barrier(2)

        def write(text):
            barrier.wait()
            store = repository.SummaryJobStore.open(self.context, "auto")
            checkpoint = store.save_result("map", 0, text)
            return store.job_id, checkpoint, store.complete(text)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(write, ["alpha", "beta"])
        self.assertEqual(first, second)
        job = self.db.summary_jobs.find_one()
        step = self.db.summary_job_checkpoints.find_one()
        self.assertIsInstance(job["_id"], ObjectId)
        self.assertIsInstance(step["_id"], ObjectId)
        self.assertEqual(step["job_id"], job["_id"])
        self.assertEqual(job["expires_at"], step["expires_at"])
        self.assertEqual(self.db.summary_jobs.count_documents({}), 1)
        self.assertEqual(self.db.summary_job_checkpoints.count_documents({}), 1)
        self.assertFalse({"job_key", "job_id", "status"} & job.keys())
        self.assertNotIn("step_index", step)

        old_worker = repository.SummaryJobStore(job)
        repository.delete_summary_jobs({"document_id": "upload-1"})
        with self.assertRaises(LookupError):
            old_worker.save_result("map", 1, "late")
        with self.assertRaises(LookupError):
            old_worker.complete("late")
        self.assertEqual(self.db.summary_job_checkpoints.count_documents({}), 0)

    def test_resume_four_groups_then_final_then_cached(self):
        settings = summary.SummarySettings(input_tokens=800, workers=1, stagger_seconds=0)
        size = summary.text_budget(800, summary.MAP_GROUP_PROMPT, "English", summary.MAP_SUMMARY_OUTPUT_TOKENS)
        docs = [Document(page_content="a" * int(size * CHARS_PER_TOKEN) * 4, metadata={"source": "report.txt"})]
        calls = []
        failure = ("map", 4)

        def invoke(llm, prompt, stage, number, **kwargs):
            calls.append((stage, number))
            if (stage, number) == failure:
                raise RuntimeError("daily token limit reached")
            return f"{stage}-{number}"

        def run(documents=docs):
            return summary.summarize_documents(documents, object(), job_context=self.context, target_language="English")

        with patch.object(summary.SummarySettings, "from_env", return_value=settings), patch.object(summary, "_invoke", side_effect=invoke):
            self.assertEqual(run().status, "partial")
            self.assertEqual(self.db.summary_job_checkpoints.count_documents({}), 3)
            calls.clear()
            failure = ("final", 1)
            self.context["model"] = "another-model"
            self.assertIn("Read 4/4 groups", run().text)
            self.assertEqual(calls, [("map", 4), ("final", 1)])
            calls.clear()
            failure = None
            self.assertEqual(run().text, "final-1")
            self.assertEqual(calls, [("final", 1)])
            calls.clear()
            self.assertEqual(run(lambda: self.fail("cache hit loaded chunks")).text, "final-1")
            self.assertEqual(calls, [])
        self.assertEqual(self.db.summary_job_checkpoints.count_documents({}), 4)

    def test_single_is_scoped_by_file_language_and_project(self):
        docs = [Document(page_content="short")]
        with patch.object(summary, "_invoke", return_value="done") as invoke:
            for language in ("vi", "Vietnamese", "en"):
                summary.summarize_documents(docs, object(), job_context=self.context, target_language=language)
            self.assertEqual(invoke.call_count, 2)
        self.assertEqual(self.db.summary_job_checkpoints.count_documents({}), 0)
        with self.assertRaises(LookupError):
            repository.SummaryJobStore.open({**self.context, "project_id": "other"}, "English")


    def test_upload_to_chat_stream(self):
        from chromadb import EphemeralClient
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from langchain_chroma import Chroma
        from langchain_core.embeddings import DeterministicFakeEmbedding
        from langchain_core.messages import AIMessage
        from langgraph.graph import END, START, StateGraph
        from langgraph.prebuilt import ToolNode
        from agent import AgentState, emit_summary_node
        from agent_tools.documents import summarize_uploaded_document
        from api import chat, documents
        from services import rag
        from storage import conversations

        vectors = Chroma(client=EphemeralClient(), collection_name=f"test-{ObjectId()}",
                         embedding_function=DeterministicFakeEmbedding(size=4))
        graph = StateGraph(AgentState)
        graph.add_node("choose", lambda state: {"messages": [AIMessage(content="", tool_calls=[
            {"name": "summarize_uploaded_document", "args": {}, "id": "call-1"}])]})
        graph.add_node("tools", ToolNode([summarize_uploaded_document]))
        graph.add_node("emit_summary", emit_summary_node)
        graph.add_edge(START, "choose")
        graph.add_edge("choose", "tools")
        graph.add_edge("tools", "emit_summary")
        graph.add_edge("emit_summary", END)
        app = FastAPI()
        app.include_router(documents.router)
        app.include_router(chat.router)
        with (
            tempfile.TemporaryDirectory() as tmp, chdir(tmp), TestClient(app) as client,
            patch.object(conversations, "get_collection", side_effect=self.db.get_collection),
            patch.object(rag, "get_vectorstore", return_value=vectors),
            patch.object(documents, "get_current_user", return_value=None),
            patch.object(chat, "get_current_user", return_value=None),
            patch.object(chat, "get_agent", return_value=graph.compile()),
            patch.object(summary, "_build_groq_llm", return_value=object()),
            patch.object(summary, "_invoke", side_effect=lambda llm, p, stage, n, **kw: f"{stage}-{n}") as invoke,
        ):
            Path("uploads").mkdir()
            upload = client.post("/upload", data={"thread_id": "web", "is_temporary": "true"},
                                 files={"file": ("report.txt", "Some facts in a report. " * 650)})
            self.assertEqual(upload.status_code, 200, upload.text)
            file = upload.json()
            payload = {"thread_id": "web", "message": "Summarize the report", "is_temporary": True,
                       "attachment_document_id": file["document_id"], "attachment_source": file["source"]}
            for attempt in range(2):
                invoke.reset_mock()
                stream = client.post("/chat/stream", json=payload)
                self.assertEqual(stream.status_code, 200, stream.text)
                events = [json.loads(line[5:]) for line in stream.text.splitlines() if line.startswith("data:")]
                answer = "".join(event.get("token", "") for event in events)
                self.assertIn("final-1", answer, stream.text)
                self.assertNotIn("map-1", answer)
                self.assertEqual(events[-1], {"done": True})
                self.assertEqual(invoke.called, attempt == 0)
            self.assertEqual(self.db.chat_messages.count_documents({"thread_id": "web", "role": "assistant"}), 2)
            job = self.db.summary_jobs.find_one({"document_id": file["document_id"]})
            self.assertEqual(job["expires_at"], self.db.summary_job_checkpoints.find_one({"job_id": job["_id"]})["expires_at"])
