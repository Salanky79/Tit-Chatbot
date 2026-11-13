"""
RAG Evaluation Runner - Chạy đánh giá RAG pipeline cho Tit Chatbot.

Tự động:
  1. Upload test document vào RAG pipeline
  2. Tạo dataset trên LangSmith
  3. Chạy RAG queries và đánh giá với 4 metrics gốc:
     - Correctness, Relevance, Groundedness, Retrieval Relevance
"""

from pathlib import Path

from langsmith import Client, traceable

from eval_config import RAG_THREAD_ID, PROJECT_ROOT
from eval_datasets import (
    RAG_DATASET_NAME, RAG_EXAMPLES, RAG_TEST_DOCUMENT,
)
from eval_metrics import RAG_EVALUATORS

# Import Tit Chatbot RAG pipeline
from services.rag import (
    add_document_to_rag,
    get_vectorstore,
)


# ===========================================================================
# 1. Setup
# ===========================================================================

client = Client()


def setup_rag_test_data():
    """
    Upload test document vào RAG pipeline của Tit Chatbot.
    Tạo file tạm, gọi add_document_to_rag() giống hệt flow thật.
    """
    print("📄 Uploading test document to RAG pipeline...")

    # Tạo file tạm chứa test document
    test_file = Path(PROJECT_ROOT) / "uploads" / "eval_test_document.txt"
    test_file.parent.mkdir(exist_ok=True)
    test_file.write_text(RAG_TEST_DOCUMENT, encoding="utf-8")

    try:
        result = add_document_to_rag(
            file_path=str(test_file),
            thread_id=RAG_THREAD_ID,
        )
        print(f"✅ Uploaded: {result['filename']} → {result['chunks']} chunks")
        return result
    except Exception as e:
        print(f"⚠️  Upload error (may already exist): {e}")
        return None


def create_rag_dataset():
    """Tạo RAG evaluation dataset trên LangSmith."""
    try:
        dataset = client.create_dataset(RAG_DATASET_NAME)
        client.create_examples(
            dataset_id=dataset.id,
            examples=RAG_EXAMPLES,
        )
        print(f"✅ Created dataset: {RAG_DATASET_NAME} ({len(RAG_EXAMPLES)} examples)")
        return dataset
    except Exception as e:
        print(f"⚠️  Dataset '{RAG_DATASET_NAME}' may already exist: {e}")
        return None


# ===========================================================================
# 2. RAG Target Function
# ===========================================================================

@traceable()
def rag_target(inputs: dict) -> dict:
    """
    Gọi RAG pipeline của Tit Chatbot.
    Trả về cả answer và documents để đánh giá groundedness & retrieval relevance.

    Flow:
      1. Retrieve documents từ ChromaDB (giống search_uploaded_documents tool)
      2. Dùng Groq LLM để generate answer từ documents
      3. Trả về {answer, documents} cho LangSmith evaluators
    """
    question = inputs["question"]

    # Step 1: Retrieve - dùng trực tiếp vectorstore của Tit Chatbot
    docs = get_vectorstore().similarity_search(
        question,
        k=4,
        filter={"thread_id": RAG_THREAD_ID},
    )

    if not docs:
        return {
            "answer": "No relevant documents found.",
            "documents": [],
        }

    # Step 2: Generate - dùng Groq LLM
    from langchain_groq import ChatGroq
    from eval_config import JUDGE_MODEL

    docs_string = "\n\n".join(doc.page_content for doc in docs)

    instructions = f"""You are a helpful assistant who is good at analyzing source information and answering questions. \
Use the following source documents to answer the user's questions. \
If you don't know the answer, just say that you don't know. \
Use three sentences maximum and keep the answer concise.

Documents:
{docs_string}"""

    llm = ChatGroq(model=JUDGE_MODEL, temperature=0)
    ai_msg = llm.invoke([
        {"role": "system", "content": instructions},
        {"role": "user", "content": question},
    ])

    return {
        "answer": ai_msg.content,
        "documents": docs,
    }


# ===========================================================================
# 3. Run RAG Evaluation
# ===========================================================================

def run_rag_evaluation():
    """
    Chạy RAG evaluation đầy đủ với 4 metrics gốc:
      1. Correctness
      2. Relevance
      3. Groundedness
      4. Retrieval Relevance
    """
    print(f"\n{'='*60}")
    print(f"📚 Running RAG Evaluation")
    print(f"   Metrics: correctness, relevance, groundedness, retrieval_relevance")
    print(f"{'='*60}")

    results = client.evaluate(
        rag_target,
        data=RAG_DATASET_NAME,
        evaluators=RAG_EVALUATORS,
        experiment_prefix="tit-rag",
        metadata={
            "type": "rag",
            "rag_thread_id": RAG_THREAD_ID,
            "version": "Tit Chatbot RAG - HuggingFace Embeddings + ChromaDB",
        },
    )

    print(f"✅ RAG evaluation complete!")
    return results


# ===========================================================================
# 4. Cleanup
# ===========================================================================

def cleanup_rag_test_data():
    """Xóa test data khỏi ChromaDB sau khi evaluate xong."""
    print("🧹 Cleaning up RAG test data...")
    try:
        # Xóa documents với thread_id eval
        collection = get_vectorstore()._collection
        results = collection.get(where={"thread_id": RAG_THREAD_ID})
        if results and results["ids"]:
            collection.delete(ids=results["ids"])
            print(f"✅ Deleted {len(results['ids'])} test chunks from ChromaDB")

        # Xóa test file
        test_file = Path(PROJECT_ROOT) / "uploads" / "eval_test_document.txt"
        if test_file.exists():
            test_file.unlink()
            print(f"✅ Deleted test file: {test_file.name}")
    except Exception as e:
        print(f"⚠️  Cleanup error: {e}")


# ===========================================================================
# 5. Main
# ===========================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tit RAG Evaluation")
    parser.add_argument("--setup", action="store_true",
                        help="Setup: upload test doc + create LangSmith dataset")
    parser.add_argument("--run", action="store_true",
                        help="Run RAG evaluation")
    parser.add_argument("--cleanup", action="store_true",
                        help="Cleanup test data from ChromaDB")
    parser.add_argument("--all", action="store_true",
                        help="Setup + Run + Cleanup")

    args = parser.parse_args()

    if args.all or args.setup:
        setup_rag_test_data()
        create_rag_dataset()

    if args.all or args.run:
        run_rag_evaluation()

    if args.all or args.cleanup:
        cleanup_rag_test_data()

    if not any([args.setup, args.run, args.cleanup, args.all]):
        # Default: setup + run
        setup_rag_test_data()
        create_rag_dataset()
        run_rag_evaluation()

