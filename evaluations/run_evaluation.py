"""
Run Evaluation - Main entry point cho toàn bộ hệ thống evaluation.

Usage:
    python run_evaluation.py                  # Chạy tất cả
    python run_evaluation.py --chatbot        # Chỉ chatbot eval
    python run_evaluation.py --rag            # Chỉ RAG eval
    python run_evaluation.py --tools          # Chỉ tool selection eval
    python run_evaluation.py --compare        # So sánh models
    python run_evaluation.py --create-datasets # Tạo datasets trước
"""

import argparse
import sys
from pathlib import Path

# Ensure evaluations directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_chatbot import (
    create_chatbot_dataset,
    create_tool_selection_dataset,
    run_chatbot_evaluation,
    run_tool_selection_evaluation,
    run_model_comparison,
)
from eval_rag import (
    setup_rag_test_data,
    create_rag_dataset,
    run_rag_evaluation,
    cleanup_rag_test_data,
)


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║           🤖 TIT CHATBOT EVALUATION SYSTEM 🤖               ║
║                                                              ║
║  Metrics:                                                    ║
║    Chatbot: correctness, concision, helpfulness,             ║
║             coherence, safety, latency                       ║
║    RAG:     correctness, relevance, groundedness,            ║
║             retrieval_relevance                              ║
║    Tools:   tool_selection                                   ║
║                                                              ║
║  Judge LLM: Groq (tương thích với Tit Chatbot stack)        ║
║  Tracking:  LangSmith                                        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")


def run_all(create_datasets: bool = True, cleanup: bool = False):
    """Chạy toàn bộ evaluation pipeline."""
    print_banner()

    # Step 1: Create datasets
    if create_datasets:
        print("\n📦 STEP 1: Creating datasets on LangSmith...")
        print("-" * 50)
        create_chatbot_dataset()
        create_tool_selection_dataset()
        setup_rag_test_data()
        create_rag_dataset()

    # Step 2: Run Chatbot evaluation
    print("\n📦 STEP 2: Chatbot Evaluation...")
    print("-" * 50)
    chatbot_results = run_chatbot_evaluation()

    # Step 3: Run Tool Selection evaluation
    print("\n📦 STEP 3: Tool Selection Evaluation...")
    print("-" * 50)
    tool_results = run_tool_selection_evaluation()

    # Step 4: Run RAG evaluation
    print("\n📦 STEP 4: RAG Evaluation...")
    print("-" * 50)
    rag_results = run_rag_evaluation()

    # Step 5: Cleanup (optional)
    if cleanup:
        print("\n📦 STEP 5: Cleanup...")
        print("-" * 50)
        cleanup_rag_test_data()

    # Summary
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                    📊 EVALUATION COMPLETE                    ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  ✅ Chatbot Evaluation  → Check LangSmith for results       ║
║  ✅ Tool Selection      → Check LangSmith for results       ║
║  ✅ RAG Evaluation      → Check LangSmith for results       ║
║                                                              ║
║  📈 View detailed results at:                                ║
║     https://smith.langchain.com                              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tit Chatbot Evaluation System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_evaluation.py                    # Run all evaluations
  python run_evaluation.py --chatbot          # Only chatbot eval
  python run_evaluation.py --rag              # Only RAG eval
  python run_evaluation.py --tools            # Only tool selection eval
  python run_evaluation.py --compare          # Compare all models
  python run_evaluation.py --create-datasets  # Just create datasets
  python run_evaluation.py --cleanup          # Cleanup test data
        """
    )

    parser.add_argument("--chatbot", action="store_true", help="Run chatbot evaluation only")
    parser.add_argument("--rag", action="store_true", help="Run RAG evaluation only")
    parser.add_argument("--tools", action="store_true", help="Run tool selection evaluation only")
    parser.add_argument("--compare", action="store_true", help="Compare models")
    parser.add_argument("--create-datasets", action="store_true", help="Create LangSmith datasets")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup RAG test data")
    parser.add_argument("--model", type=str, default=None, help="Specific model to evaluate")

    args = parser.parse_args()

    # If specific mode selected
    specific = any([args.chatbot, args.rag, args.tools, args.compare])

    if args.create_datasets and not specific:
        print_banner()
        create_chatbot_dataset()
        create_tool_selection_dataset()
        setup_rag_test_data()
        create_rag_dataset()
        print("✅ All datasets created!")

    elif args.chatbot:
        print_banner()
        run_chatbot_evaluation(model_name=args.model)

    elif args.rag:
        print_banner()
        if args.create_datasets:
            setup_rag_test_data()
            create_rag_dataset()
        run_rag_evaluation()
        if args.cleanup:
            cleanup_rag_test_data()

    elif args.tools:
        print_banner()
        run_tool_selection_evaluation()

    elif args.compare:
        print_banner()
        run_model_comparison()

    elif args.cleanup:
        cleanup_rag_test_data()

    else:
        # Default: run everything
        run_all(create_datasets=True, cleanup=args.cleanup)

