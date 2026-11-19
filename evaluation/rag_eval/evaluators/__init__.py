"""Four RAG LLM-as-a-Judge evaluators."""

from .answer import evaluate_answer_relevance
from .correctness import evaluate_correctness
from .groundedness import evaluate_groundedness
from .retrieval import evaluate_retrieval_relevance

RAG_EVALUATORS = [
    evaluate_retrieval_relevance,  # 1. retrieval_relevance: Đo chất lượng tìm kiếm (Tài liệu ChromaDB tìm ra có liên quan đến câu hỏi không).
    evaluate_answer_relevance,     # 2. answer_relevance: Đo độ bám sát câu hỏi (Câu trả lời của bot có đi đúng trọng tâm câu hỏi không).
    evaluate_groundedness,         # 3. groundedness: Đo độ trung thực/chống ảo giác (Câu trả lời có bám sát và được chứng minh bởi tài liệu không).
    evaluate_correctness,          # 4. correctness: Đo độ chính xác ngữ nghĩa (Câu trả lời có đúng với đáp án mẫu chuẩn hay không).
]

__all__ = [
    "RAG_EVALUATORS",
    "evaluate_retrieval_relevance",
    "evaluate_answer_relevance",
    "evaluate_groundedness",
    "evaluate_correctness",
]
