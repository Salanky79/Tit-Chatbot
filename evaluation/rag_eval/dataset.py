"""Small, reviewable RAG benchmark and explicit LangSmith sync helper.

The benchmark is deliberately self-contained. Documents are indexed through
the project's existing ``services.rag.add_document_to_rag`` function by the
evaluation target; no separate corpus file is required.
"""

from __future__ import annotations

import config as cfg
from typing import Any

DATASET_NAME = cfg.DATASET_NAME
DATASET_DESCRIPTION = cfg.DATASET_DESCRIPTION
DATASET_VERSION = cfg.DATASET_VERSION
MAX_CASES_PER_RUN = cfg.MAX_CASES_PER_RUN

RAG_TEST_DOCUMENTS: list[dict[str, str]] = [
    {
        "document_id": "eval_manual",
        "filename": "evaluation_manual.txt",
        "raw_text": (
            "Tit Helpdesk — Hướng dẫn xử lý yêu cầu.\n\n"
            "Yêu cầu hỗ trợ được phân loại thành ba mức: thường, ưu tiên và khẩn cấp. "
            "Yêu cầu thường được phản hồi trong vòng hai ngày làm việc. Yêu cầu ưu tiên "
            "được phản hồi trong vòng tám giờ làm việc. Yêu cầu khẩn cấp được phản hồi "
            "trong vòng một giờ.\n\n"
            "Để gửi yêu cầu, người dùng cung cấp tiêu đề, mô tả sự cố và địa chỉ email "
            "liên hệ. Ảnh chụp màn hình là tùy chọn. Nhân viên hỗ trợ xác nhận đã nhận "
            "yêu cầu qua email.\n\n"
            "Yêu cầu được xem là đã giải quyết khi người gửi xác nhận cách khắc phục "
            "hoạt động. Nếu sau hai ngày làm việc người gửi chưa phản hồi, nhân viên "
            "gửi một email nhắc. Nếu tiếp tục không có phản hồi trong ba ngày làm việc, "
            "yêu cầu được đóng. Người gửi có thể mở lại yêu cầu đã đóng bằng cách trả "
            "lời email thông báo đóng trong vòng bảy ngày."
        ),
    },
    {
        "document_id": "eval_security",
        "filename": "evaluation_security.txt",
        "raw_text": (
            "Tit Helpdesk — Bảo mật tài khoản.\n\n"
            "Nhân viên hỗ trợ không bao giờ yêu cầu mật khẩu hoặc mã xác minh một lần. "
            "Người dùng nên đổi mật khẩu ngay nếu nghi ngờ tài khoản bị truy cập trái phép.\n\n"
            "Báo cáo sự cố bảo mật được xếp mức khẩn cấp. Trong báo cáo cần có tên "
            "tài khoản, thời điểm phát hiện và mô tả dấu hiệu đáng ngờ. Không gửi mật khẩu, "
            "mã xác minh hoặc thông tin thẻ thanh toán trong báo cáo."
        ),
    },
]

RAG_EXAMPLES: list[dict[str, Any]] = [
    {
        "case_id": "case_001",
        "inputs": {"question": "Yêu cầu ưu tiên được phản hồi trong bao lâu?"},
        "outputs": {
            "reference_answer": "Yêu cầu ưu tiên được phản hồi trong vòng tám giờ làm việc.",
            "supporting_documents": ["eval_manual"],
            "supporting_evidence": ["trong vòng tám giờ làm việc"],
            "answerable": True,
        },
        "metadata": {"case_id": "case_001", "split": "calibration", "language": "vi", "question_type": "single_hop"},
    },
    {
        "case_id": "case_002",
        "inputs": {"question": "Yêu cầu khẩn cấp có thời gian phản hồi là bao lâu?"},
        "outputs": {
            "reference_answer": "Yêu cầu khẩn cấp được phản hồi trong vòng một giờ.",
            "supporting_documents": ["eval_manual"],
            "supporting_evidence": ["Yêu cầu khẩn cấp được phản hồi trong vòng một giờ"],
            "answerable": True,
        },
        "metadata": {"case_id": "case_002", "split": "calibration", "language": "vi", "question_type": "single_hop"},
    },
    {
        "case_id": "case_003",
        "inputs": {"question": "Người dùng cần cung cấp những gì khi gửi yêu cầu hỗ trợ?"},
        "outputs": {
            "reference_answer": "Cần có tiêu đề, mô tả sự cố và email liên hệ; ảnh chụp màn hình là tùy chọn.",
            "supporting_documents": ["eval_manual"],
            "supporting_evidence": ["tiêu đề, mô tả sự cố và địa chỉ email liên hệ"],
            "answerable": True,
        },
        "metadata": {"case_id": "case_003", "split": "calibration", "language": "vi", "question_type": "list"},
    },
    {
        "case_id": "case_004",
        "inputs": {"question": "Ảnh chụp màn hình có bắt buộc khi gửi yêu cầu không?"},
        "outputs": {
            "reference_answer": "Không. Ảnh chụp màn hình là tùy chọn.",
            "supporting_documents": ["eval_manual"],
            "supporting_evidence": ["Ảnh chụp màn hình là tùy chọn"],
            "answerable": True,
        },
        "metadata": {"case_id": "case_004", "split": "calibration", "language": "vi", "question_type": "yes_no"},
    },
    {
        "case_id": "case_005",
        "inputs": {"question": "Khi nào một yêu cầu được xem là đã giải quyết?"},
        "outputs": {
            "reference_answer": "Khi người gửi xác nhận cách khắc phục hoạt động.",
            "supporting_documents": ["eval_manual"],
            "supporting_evidence": ["người gửi xác nhận cách khắc phục hoạt động"],
            "answerable": True,
        },
        "metadata": {"case_id": "case_005", "split": "calibration", "language": "vi", "question_type": "single_hop"},
    },
    {
        "case_id": "case_006",
        "inputs": {"question": "Sau bao lâu không phản hồi thì yêu cầu bị đóng?"},
        "outputs": {
            "reference_answer": "Sau email nhắc, nếu người gửi tiếp tục không phản hồi trong ba ngày làm việc thì yêu cầu được đóng.",
            "supporting_documents": ["eval_manual"],
            "supporting_evidence": ["tiếp tục không có phản hồi trong ba ngày làm việc"],
            "answerable": True,
        },
        "metadata": {"case_id": "case_006", "split": "calibration", "language": "vi", "question_type": "multi_step"},
    },
    {
        "case_id": "case_007",
        "inputs": {"question": "Có thể mở lại yêu cầu đã đóng không, và trong thời hạn nào?"},
        "outputs": {
            "reference_answer": "Có. Người gửi có thể trả lời email thông báo đóng trong vòng bảy ngày để mở lại yêu cầu.",
            "supporting_documents": ["eval_manual"],
            "supporting_evidence": ["mở lại yêu cầu đã đóng bằng cách trả lời email thông báo đóng trong vòng bảy ngày"],
            "answerable": True,
        },
        "metadata": {"case_id": "case_007", "split": "calibration", "language": "vi", "question_type": "single_hop"},
    },
    {
        "case_id": "case_008",
        "inputs": {"question": "Nhân viên hỗ trợ có được yêu cầu mật khẩu hoặc mã xác minh một lần không?"},
        "outputs": {
            "reference_answer": "Không. Nhân viên hỗ trợ không bao giờ yêu cầu mật khẩu hoặc mã xác minh một lần.",
            "supporting_documents": ["eval_security"],
            "supporting_evidence": ["không bao giờ yêu cầu mật khẩu hoặc mã xác minh một lần"],
            "answerable": True,
        },
        "metadata": {"case_id": "case_008", "split": "calibration", "language": "vi", "question_type": "policy"},
    },
    {
        "case_id": "case_009",
        "inputs": {"question": "Thông tin nào cần có trong báo cáo sự cố bảo mật?"},
        "outputs": {
            "reference_answer": "Tên tài khoản, thời điểm phát hiện và mô tả dấu hiệu đáng ngờ.",
            "supporting_documents": ["eval_security"],
            "supporting_evidence": ["tên tài khoản, thời điểm phát hiện và mô tả dấu hiệu đáng ngờ"],
            "answerable": True,
        },
        "metadata": {"case_id": "case_009", "split": "calibration", "language": "vi", "question_type": "list"},
    },
    {
        "case_id": "case_010",
        "inputs": {"question": "Số điện thoại trực hỗ trợ ngoài giờ là gì?"},
        "outputs": {
            "reference_answer": "Tài liệu không cung cấp số điện thoại trực hỗ trợ ngoài giờ.",
            "supporting_documents": [],
            "supporting_evidence": [],
            "answerable": False,
        },
        "metadata": {"case_id": "case_010", "split": "calibration", "language": "vi", "question_type": "unanswerable"},
    },
]



# =============================================================================
# Kiểm tra dữ liệu đánh giá
# =============================================================================

def validate_examples(
    documents: list[dict[str, str]] = RAG_TEST_DOCUMENTS,
    examples: list[dict[str, Any]] = RAG_EXAMPLES,
) -> None:
    """Validate IDs, required fields, and GT references without network calls."""
    document_ids = {document.get("document_id") for document in documents}
    if len(document_ids) != len(documents) or None in document_ids:
        raise ValueError("Benchmark document_id values must be present and unique.")
    case_ids: set[str] = set()
    for example in examples:
        case_id = example.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in case_ids:
            raise ValueError("Each example must have a unique non-empty case_id.")
        case_ids.add(case_id)
        if not (example.get("inputs") or {}).get("question", "").strip():
            raise ValueError(f"{case_id}: question is required.")
        outputs = example.get("outputs") or {}
        if not outputs.get("reference_answer", "").strip():
            raise ValueError(f"{case_id}: reference_answer is required.")
        refs = outputs.get("supporting_documents") or []
        if not set(refs).issubset(document_ids):
            raise ValueError(f"{case_id}: supporting_documents references an unknown document.")
        if outputs.get("answerable") is True and not refs:
            raise ValueError(f"{case_id}: answerable case must reference supporting_documents.")
        meta = example.get("metadata") or {}
        if meta.get("case_id") and meta.get("case_id") != case_id:
            raise ValueError(f"{case_id}: metadata.case_id does not match example case_id.")
    for document in documents:
        if not isinstance(document.get("raw_text"), str) or not document["raw_text"].strip():
            raise ValueError(f"{document.get('document_id')}: raw_text must be a non-empty string.")



# =============================================================================
# Đồng bộ dataset khi được yêu cầu
# =============================================================================

def sync_dataset(client: Any | None = None) -> Any:
    """Create a versioned LangSmith dataset, only when explicitly called.

    Existing datasets are left untouched. This avoids accidental duplicate or
    destructive synchronization when benchmark rows have changed.
    """
    validate_examples()
    if len(RAG_EXAMPLES) > MAX_CASES_PER_RUN:
        raise ValueError(f"Dataset exceeds the initial {MAX_CASES_PER_RUN}-case safety cap.")
    if client is None:
        from dotenv import load_dotenv
        from langsmith import Client

        load_dotenv()
        client = Client()
    try:
        from langsmith.utils import LangSmithNotFoundError
    except ImportError:
        LangSmithNotFoundError = ()

    try:
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
    except Exception as exc:
        is_not_found = (
            (bool(LangSmithNotFoundError) and isinstance(exc, LangSmithNotFoundError))
            or getattr(exc, "status_code", None) == 404
            or "not found" in str(exc).lower()
        )
        if not is_not_found:
            raise
        dataset = client.create_dataset(
            dataset_name=DATASET_NAME,
            description=DATASET_DESCRIPTION,
        )
    existing = list(client.list_examples(dataset_id=dataset.id))
    unlabeled_remote_rows = [
        row for row in existing
        if not (getattr(row, "metadata", None) or {}).get("case_id")
    ]
    if unlabeled_remote_rows:
        raise ValueError(
            f"LangSmith dataset contains {len(unlabeled_remote_rows)} example(s) missing 'metadata.case_id'. "
            "Clean up or tag these examples before synchronizing to prevent duplicate cases."
        )
    existing_by_case = {
        (getattr(row, "metadata", None) or {}).get("case_id"): row
        for row in existing
    }
    unexpected_cases = set(existing_by_case) - {example["case_id"] for example in RAG_EXAMPLES}
    if unexpected_cases:
        raise ValueError(
            "LangSmith dataset contains unexpected case IDs; refusing to evaluate an unreviewed larger set: "
            + ", ".join(sorted(str(case_id) for case_id in unexpected_cases))
        )
    to_create = []
    for example in RAG_EXAMPLES:
        prior = existing_by_case.get(example["case_id"])
        if prior is None:
            payload = dict(example)
            payload["metadata"] = dict(payload.get("metadata") or {})
            payload["metadata"]["case_id"] = example["case_id"]
            to_create.append(payload)
            continue
        if prior.inputs != example["inputs"] or prior.outputs != example["outputs"]:
            raise ValueError(
                f"Dataset case {example['case_id']} differs from the existing LangSmith row. "
                "Use a new DATASET_NAME/version rather than overwriting it."
            )
    if to_create:
        client.create_examples(dataset_id=dataset.id, examples=to_create)
    return dataset
