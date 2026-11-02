"""
Evaluation Datasets - Bộ dữ liệu test cho Tit Chatbot.

Chứa các test cases cho:
  1. Chatbot Evaluation (general Q&A, tool usage, multi-turn)
  2. RAG Evaluation (document-based Q&A)
"""


# ===========================================================================
# PHẦN 1: CHATBOT EVALUATION DATASETS
# ===========================================================================

CHATBOT_DATASET_NAME = "Tit Chatbot Evaluation"

CHATBOT_EXAMPLES = [
    # --- General Knowledge ---
    {
        "inputs": {"question": "What is Python?"},
        "outputs": {"answer": "Python is a high-level, interpreted programming language known for its readability and versatility."},
    },
    {
        "inputs": {"question": "What is machine learning?"},
        "outputs": {"answer": "Machine learning is a subset of artificial intelligence that enables systems to learn and improve from data without being explicitly programmed."},
    },
    {
        "inputs": {"question": "What is Docker?"},
        "outputs": {"answer": "Docker is a platform for developing, shipping, and running applications inside lightweight, portable containers."},
    },
    {
        "inputs": {"question": "What is FastAPI?"},
        "outputs": {"answer": "FastAPI is a modern, fast web framework for building APIs with Python based on standard Python type hints."},
    },
    {
        "inputs": {"question": "Explain what a REST API is."},
        "outputs": {"answer": "A REST API is an application programming interface that follows REST architectural constraints, using HTTP methods to access and manipulate resources."},
    },

    # --- Math / Calculator Tool ---
    {
        "inputs": {"question": "What is 15 * 23?"},
        "outputs": {"answer": "345"},
    },
    {
        "inputs": {"question": "Calculate the square root of 144."},
        "outputs": {"answer": "12"},
    },

    # --- Memory Tool ---
    {
        "inputs": {"question": "Remember that my favorite programming language is Python."},
        "outputs": {"answer": "I've saved that your favorite programming language is Python."},
    },

    # --- Edge Cases ---
    {
        "inputs": {"question": ""},
        "outputs": {"answer": "Please provide a question or message so I can help you."},
    },
    {
        "inputs": {"question": "Tell me a joke."},
        "outputs": {"answer": "A humorous response related to programming or technology."},
    },
]


# ===========================================================================
# PHẦN 2: TOOL SELECTION DATASET
# Kiểm tra chatbot có chọn đúng tool cho từng loại câu hỏi không
# ===========================================================================

TOOL_SELECTION_DATASET_NAME = "Tit Tool Selection Evaluation"

TOOL_SELECTION_EXAMPLES = [
    {
        "inputs": {"question": "What is 2 + 2?"},
        "outputs": {"expected_tool": "calculator"},
    },
    {
        "inputs": {"question": "What is the latest news about AI?"},
        "outputs": {"expected_tool": "tavily_search"},
    },
    {
        "inputs": {"question": "Search my uploaded document about machine learning."},
        "outputs": {"expected_tool": "search_uploaded_documents"},
    },
    {
        "inputs": {"question": "Remember that I like coffee."},
        "outputs": {"expected_tool": "remember_this"},
    },
    {
        "inputs": {"question": "What did I tell you to remember?"},
        "outputs": {"expected_tool": "recall_memory"},
    },
    {
        "inputs": {"question": "What is the capital of France?"},
        "outputs": {"expected_tool": "none"},
    },
    {
        "inputs": {"question": "Calculate math.sqrt(256) + 10"},
        "outputs": {"expected_tool": "calculator"},
    },
    {
        "inputs": {"question": "What is the current price of Bitcoin?"},
        "outputs": {"expected_tool": "tavily_search"},
    },
]


# ===========================================================================
# PHẦN 3: RAG EVALUATION DATASET
# Dùng để test RAG pipeline - cần upload document trước khi chạy
# ===========================================================================

RAG_DATASET_NAME = "Tit RAG Evaluation"

# Sample document content dùng để tạo test data
# (sẽ được tự động upload vào RAG khi chạy evaluation)
RAG_TEST_DOCUMENT = """
Artificial Intelligence (AI) is a branch of computer science that aims to create
intelligent machines. Machine Learning (ML) is a subset of AI that provides systems
the ability to automatically learn and improve from experience without being
explicitly programmed.

Deep Learning is a subset of machine learning that uses neural networks with many
layers (deep neural networks). It has been successful in areas like image recognition,
natural language processing, and speech recognition.

Key concepts in AI:
1. Supervised Learning: The model learns from labeled training data.
2. Unsupervised Learning: The model finds patterns in unlabeled data.
3. Reinforcement Learning: The model learns by interacting with an environment
   and receiving rewards or penalties.

Natural Language Processing (NLP) is a field of AI that gives machines the ability
to read, understand, and derive meaning from human languages. Key NLP tasks include:
- Text classification
- Named entity recognition
- Machine translation
- Sentiment analysis
- Question answering

LangChain is a framework for developing applications powered by language models.
It provides tools for:
- Prompt management
- Chain composition
- Memory management
- Agent creation with tool use
- RAG (Retrieval Augmented Generation)

RAG combines retrieval and generation: it first retrieves relevant documents from
a knowledge base, then uses an LLM to generate answers based on those documents.
This helps reduce hallucination and provides more accurate, up-to-date responses.
"""

RAG_EXAMPLES = [
    {
        "inputs": {"question": "What is Deep Learning?"},
        "outputs": {
            "answer": "Deep Learning is a subset of machine learning that uses neural networks with many layers (deep neural networks). It has been successful in areas like image recognition, natural language processing, and speech recognition."
        },
    },
    {
        "inputs": {"question": "What are the three types of learning in AI?"},
        "outputs": {
            "answer": "The three types of learning in AI are: (1) Supervised Learning where the model learns from labeled training data, (2) Unsupervised Learning where the model finds patterns in unlabeled data, and (3) Reinforcement Learning where the model learns by interacting with an environment and receiving rewards or penalties."
        },
    },
    {
        "inputs": {"question": "What is RAG and why is it useful?"},
        "outputs": {
            "answer": "RAG (Retrieval Augmented Generation) combines retrieval and generation: it first retrieves relevant documents from a knowledge base, then uses an LLM to generate answers based on those documents. It helps reduce hallucination and provides more accurate, up-to-date responses."
        },
    },
    {
        "inputs": {"question": "What NLP tasks are mentioned in the document?"},
        "outputs": {
            "answer": "The NLP tasks mentioned include text classification, named entity recognition, machine translation, sentiment analysis, and question answering."
        },
    },
    {
        "inputs": {"question": "What does LangChain provide?"},
        "outputs": {
            "answer": "LangChain provides tools for prompt management, chain composition, memory management, agent creation with tool use, and RAG (Retrieval Augmented Generation)."
        },
    },
]

