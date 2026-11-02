"""
Chatbot Evaluation Runner - Chạy đánh giá cho Tit Chatbot.

Đánh giá chatbot trên các khả năng:
  - General Q&A (kiến thức tổng quát)
  - Tool Selection (chọn đúng tool)
  - So sánh giữa các models
"""

import time
from langsmith import Client
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from eval_config import CHATBOT_MODELS, JUDGE_MODEL
from eval_datasets import (
    CHATBOT_DATASET_NAME, CHATBOT_EXAMPLES,
    TOOL_SELECTION_DATASET_NAME, TOOL_SELECTION_EXAMPLES,
)
from eval_metrics import (
    CHATBOT_EVALUATORS, TOOL_EVALUATORS,
    correctness, concision, helpfulness, coherence, safety, latency_check,
)

# Import Tit Chatbot agent
from agent import get_agent


# ===========================================================================
# 1. Setup LangSmith Client & Datasets
# ===========================================================================

client = Client()


def create_chatbot_dataset():
    """Tạo dataset cho Chatbot evaluation trên LangSmith."""
    try:
        dataset = client.create_dataset(CHATBOT_DATASET_NAME)
        client.create_examples(
            dataset_id=dataset.id,
            examples=CHATBOT_EXAMPLES,
        )
        print(f"✅ Created dataset: {CHATBOT_DATASET_NAME} ({len(CHATBOT_EXAMPLES)} examples)")
        return dataset
    except Exception as e:
        print(f"⚠️  Dataset '{CHATBOT_DATASET_NAME}' may already exist: {e}")
        return None


def create_tool_selection_dataset():
    """Tạo dataset cho Tool Selection evaluation trên LangSmith."""
    try:
        dataset = client.create_dataset(TOOL_SELECTION_DATASET_NAME)
        client.create_examples(
            dataset_id=dataset.id,
            examples=TOOL_SELECTION_EXAMPLES,
        )
        print(f"✅ Created dataset: {TOOL_SELECTION_DATASET_NAME} ({len(TOOL_SELECTION_EXAMPLES)} examples)")
        return dataset
    except Exception as e:
        print(f"⚠️  Dataset '{TOOL_SELECTION_DATASET_NAME}' may already exist: {e}")
        return None


# ===========================================================================
# 2. Target Functions - Gọi Tit Chatbot thực tế
# ===========================================================================

def chatbot_target(inputs: dict, model_name: str = None) -> dict:
    """
    Gọi Tit Chatbot agent và trả về response.
    Tương thích với LangSmith evaluate() format.
    """
    question = inputs.get("question", "")
    if not question.strip():
        return {
            "response": "Please provide a question or message so I can help you.",
            "answer": "Please provide a question or message so I can help you.",
            "latency_seconds": 0.0,
        }

    agent = get_agent(model_name)

    config = {
        "configurable": {
            "thread_id": f"eval-chatbot-{hash(question) % 100000}"
        }
    }

    start_time = time.time()

    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=question)]},
            config=config,
        )

        elapsed = time.time() - start_time

        # Lấy message cuối cùng của AI
        ai_messages = [
            m for m in result["messages"]
            if isinstance(m, AIMessage) and m.content and not m.tool_calls
        ]

        response_text = ai_messages[-1].content if ai_messages else ""

        return {
            "response": response_text,
            "answer": response_text,  # alias cho correctness metric
            "latency_seconds": round(elapsed, 3),
        }

    except Exception as e:
        elapsed = time.time() - start_time
        return {
            "response": f"Error: {str(e)}",
            "answer": f"Error: {str(e)}",
            "latency_seconds": round(elapsed, 3),
        }


def tool_selection_target(inputs: dict) -> dict:
    """
    Gọi Tit Chatbot và kiểm tra tool nào được gọi.
    Trả về danh sách tools đã sử dụng.
    """
    question = inputs.get("question", "")
    agent = get_agent()

    config = {
        "configurable": {
            "thread_id": f"eval-tool-{hash(question) % 100000}"
        }
    }

    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=question)]},
            config=config,
        )

        # Phân tích xem tool nào đã được gọi
        tools_used = []
        for msg in result["messages"]:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    tools_used.append(tc["name"])

        return {
            "tools_used": tools_used if tools_used else ["none"],
            "response": result["messages"][-1].content if result["messages"] else "",
        }

    except Exception as e:
        return {
            "tools_used": ["error"],
            "response": f"Error: {str(e)}",
        }


# ===========================================================================
# 3. Run Evaluations
# ===========================================================================

def run_chatbot_evaluation(model_name: str = None):
    """
    Chạy Chatbot evaluation cho một model cụ thể.
    Metrics: correctness, concision, helpfulness, coherence, safety, latency
    """
    model = model_name or CHATBOT_MODELS[0]
    prefix = model.replace("/", "-")

    print(f"\n{'='*60}")
    print(f"🤖 Running Chatbot Evaluation: {model}")
    print(f"{'='*60}")

    def target(inputs: dict) -> dict:
        return chatbot_target(inputs, model_name=model)

    results = client.evaluate(
        target,
        data=CHATBOT_DATASET_NAME,
        evaluators=CHATBOT_EVALUATORS,
        experiment_prefix=f"tit-chatbot-{prefix}",
        metadata={"model": model, "type": "chatbot"},
    )

    print(f"✅ Chatbot evaluation complete for {model}")
    return results


def run_tool_selection_evaluation():
    """
    Chạy Tool Selection evaluation.
    Metrics: tool_selection (LLM judge)
    """
    print(f"\n{'='*60}")
    print(f"🔧 Running Tool Selection Evaluation")
    print(f"{'='*60}")

    results = client.evaluate(
        tool_selection_target,
        data=TOOL_SELECTION_DATASET_NAME,
        evaluators=TOOL_EVALUATORS,
        experiment_prefix="tit-tool-selection",
        metadata={"type": "tool_selection"},
    )

    print(f"✅ Tool Selection evaluation complete")
    return results


def run_model_comparison():
    """
    So sánh hiệu suất giữa các models trên cùng dataset.
    """
    print(f"\n{'='*60}")
    print(f"📊 Running Model Comparison")
    print(f"   Models: {CHATBOT_MODELS}")
    print(f"{'='*60}")

    all_results = {}
    for model in CHATBOT_MODELS:
        results = run_chatbot_evaluation(model_name=model)
        all_results[model] = results

    print(f"\n✅ Model comparison complete! Check LangSmith for side-by-side results.")
    return all_results


# ===========================================================================
# 4. Main
# ===========================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tit Chatbot Evaluation")
    parser.add_argument("--mode", choices=["chatbot", "tools", "compare", "all"],
                        default="chatbot", help="Evaluation mode")
    parser.add_argument("--model", type=str, default=None,
                        help="Specific model to evaluate")
    parser.add_argument("--create-datasets", action="store_true",
                        help="Create LangSmith datasets before evaluation")

    args = parser.parse_args()

    if args.create_datasets:
        create_chatbot_dataset()
        create_tool_selection_dataset()

    if args.mode == "chatbot":
        run_chatbot_evaluation(model_name=args.model)
    elif args.mode == "tools":
        run_tool_selection_evaluation()
    elif args.mode == "compare":
        run_model_comparison()
    elif args.mode == "all":
        run_chatbot_evaluation(model_name=args.model)
        run_tool_selection_evaluation()

