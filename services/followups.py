"""
Module này dùng để tự động tạo ra 2 câu hỏi gợi ý tiếp theo (follow-up suggestions)
cho người dùng sau khi chatbot trả lời xong.
Ví dụ: Người dùng hỏi "Tuyển sinh trường TDTU", bot trả lời xong thì hàm này gợi ý:
1. "Điểm chuẩn các ngành năm ngoái là bao nhiêu?"
2. "Học phí một năm khoảng bao nhiêu?"
"""

from __future__ import annotations

import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from prompts.followups import FOLLOWUP_PROMPT
from utils.token_utils import estimate_tokens, truncate_text_to_tokens
from utils.usage_logging import log_token_usage


logger = logging.getLogger(__name__)


# ==============================================================================
# HÀM HỖ TRỢ ĐỌC BIẾN MÔI TRƯỜNG (ENVIRONMENT VARIABLES) AN TOÀN
# ==============================================================================

def _int_setting(name: str, default: int, minimum: int = 1) -> int:
    """
    Đọc biến môi trường dạng số nguyên (integer).
    - Nếu không tìm thấy hoặc giá trị không hợp lệ (lỗi chữ/chuỗi), trả về giá trị mặc định `default`.
    - Đảm bảo giá trị trả về không nhỏ hơn `minimum` để tránh lỗi cấu hình (ví dụ max_tokens = 0 hoặc âm).
    """
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _float_setting(name: str, default: float, minimum: float = 0.1) -> float:
    """
    Đọc biến môi trường dạng số thực (float), ví dụ số giây timeout (5.0s).
    - Tương tự như _int_setting nhưng dùng cho kiểu số thập phân.
    """
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


# ==============================================================================
# CÁC CẤU HÌNH (CONFIGURATION) CHO TÍNH NĂNG GỢI Ý CÂU HỎI TIẾP THEO
# ==============================================================================

# Cờ bật/tắt tính năng gợi ý câu hỏi: nếu biến FOLLOWUPS_ENABLED là "0", "false", "no", "off" thì tắt
FOLLOWUPS_ENABLED = os.getenv("FOLLOWUPS_ENABLED", "true").strip().lower() not in {
    "0", "false", "no", "off"
}

# Tên mô hình LLM dùng để sinh câu hỏi gợi ý (mặc định dùng mô hình nhẹ/nhanh trên Groq)
FOLLOWUP_MODEL = (os.getenv("FOLLOWUP_MODEL") or "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"

# Thời gian tối đa (giây) chờ API trả lời. Nếu quá thời gian này sẽ hủy để tránh làm người dùng đợi lâu
FOLLOWUP_TIMEOUT_SECONDS = _float_setting("FOLLOWUP_TIMEOUT_SECONDS", 5.0)

# Giới hạn số token tối đa của câu hỏi user được đưa vào prompt (tránh prompt quá dài tốn chi phí và chậm)
FOLLOWUP_MAX_USER_TOKENS = _int_setting("FOLLOWUP_MAX_USER_TOKENS", 150)

# Giới hạn số token tối đa của câu trả lời bot được đưa vào prompt
FOLLOWUP_MAX_ANSWER_TOKENS = _int_setting("FOLLOWUP_MAX_ANSWER_TOKENS", 300)

# Số lượng token tối đa mà mô hình được phép sinh ra (output tokens).
# Lưu ý: Một số mô hình suy luận (reasoning model) như openai/gpt-oss-20b sẽ tạo ra khoảng 150-200 token suy nghĩ
# trước khi sinh ra chuỗi JSON kết quả. Do đó cần đặt ít nhất 500 tokens để tránh JSON bị đứt đoạn giữa chừng.
FOLLOWUP_OUTPUT_TOKENS = _int_setting("FOLLOWUP_OUTPUT_TOKENS", 500)

# Độ dài tối đa (số ký tự) cho mỗi câu hỏi gợi ý để đảm bảo ngắn gọn, vừa vặn trên giao diện UI
FOLLOWUP_MAX_PROMPT_CHARS = _int_setting("FOLLOWUP_MAX_PROMPT_CHARS", 180)


# ==============================================================================
# ĐỊNH NGHĨA CẤU TRÚC DỮ LIỆU ĐẦU RA (PYDANTIC SCHEMA)
# ==============================================================================

class FollowupSuggestions(BaseModel):
    """
    Khuôn mẫu (Schema) định nghĩa cấu trúc dữ liệu JSON mà ta yêu cầu LLM phải trả về.
    Mô hình bắt buộc phải trả lời đúng định dạng: {"prompts": ["câu hỏi 1", "câu hỏi 2"]}
    """
    prompts: list[str] = Field(description="Exactly two distinct, concise follow-up prompts.")


# ==============================================================================
# CÁC HÀM XỬ LÝ DỮ LIỆU VÀ KIỂM TRA (VALIDATION)
# ==============================================================================

def _prepare_context(text: str, max_tokens: int) -> str:
    """
    Chuẩn hóa và cắt ngắn đoạn văn bản nếu vượt quá giới hạn token cho phép.
    Điều này giúp prompt luôn gọn gàng, tăng tốc độ phản hồi và tiết kiệm token.
    """
    text = (text or "").strip()
    if estimate_tokens(text) <= max_tokens:
        return text
    return truncate_text_to_tokens(text, max_tokens)


def _validated_prompts(parsed: object) -> list[str]:
    """
    Kiểm tra tính hợp lệ nghiêm ngặt của kết quả do LLM trả về:
    1. Phải là Pydantic model `FollowupSuggestions` hoặc một dict chứa key 'prompts'.
    2. Danh sách prompts phải có ĐÚNG 2 phần tử và cả 2 đều là chuỗi (string).
    3. Không được để trống và độ dài mỗi câu không được vượt quá FOLLOWUP_MAX_PROMPT_CHARS.
    4. Hai câu hỏi không được trùng lặp nội dung (so sánh không phân biệt hoa thường bằng casefold).
    Nếu vi phạm bất kỳ điều kiện nào -> xem như không hợp lệ và trả về mảng rỗng [].
    """
    # Trích xuất danh sách prompts tùy thuộc kiểu dữ liệu trả về (Pydantic object hay dict)
    if isinstance(parsed, FollowupSuggestions):
        prompts = parsed.prompts
    elif isinstance(parsed, dict):
        prompts = parsed.get("prompts")
    else:
        return []

    # Kiểm tra xem có đúng là list và có đúng 2 câu gợi ý hay không
    if not isinstance(prompts, list) or len(prompts) != 2:
        return []
    # Đảm bảo mỗi phần tử trong list đều là chuỗi ký tự
    if any(not isinstance(prompt, str) for prompt in prompts):
        return []

    # Xóa khoảng trắng thừa ở đầu/cuối mỗi câu gợi ý
    cleaned = [prompt.strip() for prompt in prompts]
    # Kiểm tra không được rỗng và không vượt quá số ký tự tối đa
    if any(not prompt or len(prompt) > FOLLOWUP_MAX_PROMPT_CHARS for prompt in cleaned):
        return []
    # Kiểm tra 2 câu có bị trùng nhau không (casefold so sánh chuỗi không phân biệt hoa/thường chuẩn hơn lower)
    if cleaned[0].casefold() == cleaned[1].casefold():
        return []

    return cleaned


# ==============================================================================
# HÀM CHÍNH SINH CÂU HỎI GỢI Ý (MAIN FUNCTION)
# ==============================================================================

def generate_followup_questions(
    user_message: str,
    assistant_answer: str,
    model_name: str | None = None,
) -> list[str]:
    """
    Hàm chính tạo 2 câu hỏi gợi ý tiếp theo dựa trên tin nhắn người dùng và câu trả lời của bot.

    Tham số:
    - user_message: Câu hỏi hoặc tin nhắn vừa rồi của người dùng.
    - assistant_answer: Câu trả lời mà chatbot vừa sinh ra.
    - model_name: Tùy chọn chỉ định mô hình LLM cụ thể (nếu None sẽ dùng FOLLOWUP_MODEL mặc định).

    Trả về:
    - Danh sách gồm đúng 2 chuỗi câu hỏi [câu 1, câu 2] nếu thành công.
    - Danh sách rỗng [] nếu tính năng bị tắt, câu trả lời rỗng, hoặc có lỗi xảy ra (fail-safe).
    """
    # 1. Kiểm tra điều kiện tiên quyết: Nếu tắt tính năng hoặc bot chưa trả lời gì thì không làm gì cả
    if not FOLLOWUPS_ENABLED or not assistant_answer.strip():
        return []

    # 2. Cắt gọn nội dung câu hỏi của user và câu trả lời của bot theo số token tối đa cho phép
    clean_user = _prepare_context(user_message, FOLLOWUP_MAX_USER_TOKENS)
    clean_answer = _prepare_context(assistant_answer, FOLLOWUP_MAX_ANSWER_TOKENS)
    # Lấy model được truyền vào hoặc fallback về model mặc định
    selected_model = (model_name or FOLLOWUP_MODEL).strip() or FOLLOWUP_MODEL

    try:
        # 3. Khởi tạo client ChatGroq kết nối tới API Groq
        model = ChatGroq(
            model=selected_model,
            temperature=0.2,               # Nhiệt độ thấp (0.2) để câu trả lời có tính ổn định, ít bị "bịa"
            max_tokens=FOLLOWUP_OUTPUT_TOKENS,   # Giới hạn token sinh ra (đã tính kèm reasoning tokens nếu có)
            request_timeout=FOLLOWUP_TIMEOUT_SECONDS, # Timeout để tránh nghẽn luồng nếu mạng chậm
        )

        # 4. Cấu hình Structured Output (yêu cầu LLM trả về theo đúng khuôn mẫu Pydantic `FollowupSuggestions`)
        # - method="json_schema": ép buộc model tuân thủ schema JSON cụ thể
        # - include_raw=True: yêu cầu LangChain trả về cả output đã parse VÀ response gốc (raw response)
        #   từ API để ta có thể trích xuất thông tin token đã dùng và ghi log
        structured_model = model.with_structured_output(
            FollowupSuggestions,
            method="json_schema",
            include_raw=True,
        )

        # 5. Điền ngữ cảnh vào mẫu prompt định sẵn
        prompt = FOLLOWUP_PROMPT.format(
            user_message=clean_user,
            assistant_answer=clean_answer,
        )

        # 6. Gửi prompt đến LLM và nhận kết quả
        # Khi dùng include_raw=True, response trả về sẽ là một dictionary có cấu trúc:
        # {
        #     "raw": AIMessage(...),           # Tin nhắn phản hồi thô từ Groq (chứa token usage, metadata)
        #     "parsed": FollowupSuggestions,   # Dữ liệu đã được Pydantic ép kiểu thành công (hoặc None)
        #     "parsing_error": Exception|None  # Lỗi nếu mô hình trả về JSON sai định dạng
        # }
        response = structured_model.invoke([
            HumanMessage(content=prompt)
        ])

        # Kiểm tra phản hồi có đúng là dictionary hay không
        if not isinstance(response, dict):
            logger.warning("Follow-up model returned an unexpected structured-output value.")
            return []

        # 7. Ghi nhận lượng token đã tiêu thụ (Token Usage Logging) từ response thô
        raw_response = response.get("raw")
        if raw_response is not None:
            log_token_usage(selected_model, "Follow-up Suggestions", raw_response)

        # 8. Xử lý trường hợp mô hình trả về lỗi không parse được JSON
        if response.get("parsing_error"):
            logger.warning("Could not parse follow-up structured output: %s", response["parsing_error"])
            return []

        # 9. Kiểm tra và làm sạch 2 câu hỏi (đảm bảo đúng định dạng, đủ số lượng, không trùng)
        prompts = _validated_prompts(response.get("parsed"))
        if len(prompts) != 2:
            logger.warning("Follow-up output did not contain two valid prompts.")
        return prompts

    except Exception as exc:
        # 10. Fail-safe (An toàn khi lỗi): Nếu có bất kỳ lỗi mạng, lỗi timeout hay lỗi API,
        # hàm chỉ ghi warning log và trả về danh sách rỗng [], KHÔNG BAO GIỜ làm sập tính năng chat chính.
        logger.warning("Follow-up generation failed: %s", exc)
        return []
