from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys
import types

import pytest
from langchain_core.documents import Document

# Avoid loading/downloading the local embedding model while testing collection
# selection. The production Chroma wrapper is exercised through its get API.
fake_chroma = types.ModuleType("langchain_chroma")
fake_chroma.Chroma = lambda **kwargs: SimpleNamespace(_collection=Mock())
fake_embeddings = types.ModuleType("langchain_huggingface")
fake_embeddings.HuggingFaceEmbeddings = lambda **kwargs: object()
sys.modules.setdefault("langchain_chroma", fake_chroma)
sys.modules.setdefault("langchain_huggingface", fake_embeddings)
import rag
from summarization import summarize_documents


class FakeLLM:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def invoke(self, messages):
        self.calls.append(messages)
        if self.fail_at == len(self.calls):
            raise RuntimeError("simulated provider failure")
        return SimpleNamespace(content=f"summary-{len(self.calls)}")


def test_get_document_chunks_filters_thread_and_source_and_sorts_all_chunks():
    rag.vectorstore._collection = Mock()
    rag.vectorstore._collection.get.return_value = {
        "documents": ["third", "first", "second"],
        "metadatas": [
            {"thread_id": "t1", "source": "a.txt", "page": 2, "chunk_index": 0},
            {"thread_id": "t1", "source": "a.txt", "page": 1, "chunk_index": 0},
            {"thread_id": "t1", "source": "a.txt", "page": 2, "chunk_index": 1},
        ],
    }

    docs = rag.get_document_chunks("a.txt", "t1")

    assert [doc.page_content for doc in docs] == ["first", "third", "second"]
    rag.vectorstore._collection.get.assert_called_once_with(
        where={"$and": [{"thread_id": "t1"}, {"source": "a.txt"}]},
        include=["documents", "metadatas"],
    )


def test_get_document_chunks_errors_when_file_is_missing():
    rag.vectorstore._collection = Mock()
    rag.vectorstore._collection.get.return_value = {"documents": [], "metadatas": []}
    with pytest.raises(ValueError, match="No content found"):
        rag.get_document_chunks("missing.txt", "t1")


def test_map_reduce_processes_large_unstructured_document_at_multiple_levels():
    # No headings are present; coverage is chunk based, not chapter based.
    docs = [Document(page_content=(f"Text block {i}. " + "fact " * 180)) for i in range(18)]
    llm = FakeLLM()

    result = summarize_documents(docs, llm, input_token_budget=300)

    assert result.startswith("summary-")
    assert len(llm.calls) > 18  # map plus multiple reduction calls
    map_prompts = [
        m[1].content for m in llm.calls
        if m[1].content.startswith("Summarize this part")
    ]
    # Each source block is assigned to a map group; all 18 occur in map input.
    joined = "\n".join(map_prompts)
    for i in range(18):
        assert f"Text block {i}." in joined


def test_small_document_uses_one_map_call_and_returns_its_summary():
    llm = FakeLLM()
    result = summarize_documents([Document(page_content="Short content.")], llm)
    assert result == "summary-1"
    assert len(llm.calls) == 1


def test_summarization_propagates_llm_failure_instead_of_returning_partial_result():
    docs = [Document(page_content=("content " * 400)) for _ in range(4)]
    with pytest.raises(RuntimeError, match="map, group"):
        summarize_documents(docs, FakeLLM(fail_at=2), input_token_budget=300)


def test_tool_asks_when_multiple_files_are_available():
    import tools

    with patch.object(tools, "list_document_sources", return_value=["a.pdf", "b.txt"]), \
         patch.object(tools, "get_document_chunks") as get_chunks, \
         patch.object(tools, "summarize_documents") as summarize:
        answer = tools.summarize_uploaded_document.invoke({"filename": ""})
    assert "Which file" in answer
    get_chunks.assert_not_called()
    summarize.assert_not_called()


def test_tool_summarizes_only_the_named_file():
    import tools

    chunks = [Document(page_content="entire selected file")]
    with patch.object(tools, "list_document_sources", return_value=["a.pdf", "b.txt"]), \
         patch.object(tools, "get_document_chunks", return_value=chunks) as get_chunks, \
         patch.object(tools, "summarize_documents", return_value="complete summary") as summarize:
        answer = tools.summarize_uploaded_document.invoke({"filename": "B.TXT"})
    assert '"b.txt"' in answer
    assert "complete summary" in answer
    get_chunks.assert_called_once_with("b.txt", tools.CURRENT_THREAD_ID)
    summarize.assert_called_once_with(chunks)


def test_image_tool_asks_when_multiple_images_are_available():
    import tools

    images = [
        {"source": "one.png", "image_path": "uploads/one.png"},
        {"source": "two.jpg", "image_path": "uploads/two.jpg"},
    ]
    with patch.object(tools, "list_uploaded_images", return_value=images), \
         patch.object(tools, "analyze_image") as analyze:
        answer = tools.analyze_uploaded_image.invoke({"question": "What is shown?", "filename": ""})
    assert "Which image" in answer
    analyze.assert_not_called()


def test_image_tool_uses_only_the_named_image():
    import tools

    images = [
        {"source": "one.png", "image_path": "uploads/one.png"},
        {"source": "two.jpg", "image_path": "uploads/two.jpg"},
    ]
    with patch.object(tools, "list_uploaded_images", return_value=images), \
         patch.object(tools, "analyze_image", return_value="A red car.") as analyze:
        answer = tools.analyze_uploaded_image.invoke({
            "question": "What color is the car?", "filename": "two.jpg"
        })
    assert "A red car." in answer
    analyze.assert_called_once_with("uploads/two.jpg", "What color is the car?")
