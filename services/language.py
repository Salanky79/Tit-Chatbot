"""Centralized language resolution and request-scoped context management.

Quản lý ngôn ngữ tập trung:
- Trích xuất mã ngôn ngữ chuẩn BCP 47 từ HTTP Header (X-Language, Accept-Language).
- Lưu trữ ngữ cảnh ngôn ngữ theo từng request qua ContextVar (an toàn đa luồng).
- Cung cấp hàm kiểm tra ngôn ngữ và fallback quốc tế (en cho người nước ngoài, vi cho tiếng Việt).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

# Ngăn kéo tạm (ContextVar) lưu mã ngôn ngữ của request hiện tại, mặc định là "vi"
_current_language: ContextVar[str] = ContextVar("current_language", default="vi")


# Trích xuất mã ngôn ngữ chính theo chuẩn BCP 47 (bảo toàn cả mã 2 ký tự như 'vi', 'en' và 3 ký tự như 'fil')

# =============================================================================
# 01. Xác định ngôn ngữ từ request
# =============================================================================

def extract_primary_language(raw_lang: str | None) -> str:
    """Extract primary language subtag (BCP 47), preserving 2-letter or 3-letter ISO codes."""
    if not raw_lang:
        return "vi"
    # Lấy phần tử đầu tiên nếu có dấu phẩy và cắt trước dấu gạch ngang '-' hoặc gạch dưới '_'
    clean = str(raw_lang).strip().split(",")[0].strip()
    primary = clean.split("-")[0].split("_")[0].strip().lower()
    return primary or "vi"


# Đọc mã ngôn ngữ từ HTTP headers (ưu tiên X-Language, fallback sang Accept-Language)
def resolve_request_language(headers: Mapping[str, Any] | None) -> str:
    """Resolve user language from request headers (X-Language preferred, then Accept-Language)."""
    if not headers:
        return "vi"
    # Kiểm tra X-Language tùy biến trước
    for key, val in headers.items():
        if str(key).casefold() == "x-language" and val:
            return extract_primary_language(str(val))
    # Fallback sang Accept-Language chuẩn của trình duyệt
    for key, val in headers.items():
        if str(key).casefold() == "accept-language" and val:
            return extract_primary_language(str(val))
    return "vi"


# Cất mã ngôn ngữ vào ContextVar cho lượt request hiện tại

# =============================================================================
# 02. Ngôn ngữ theo context hiện tại
# =============================================================================

def set_current_language(lang: str) -> None:
    """Set language code for the current request context."""
    _current_language.set(extract_primary_language(lang))


# Lấy mã ngôn ngữ từ ContextVar của request hiện tại
def get_current_language() -> str:
    """Get language code from the current request context."""
    return _current_language.get()


# Kiểm tra xem ngôn ngữ hiện tại có phải là tiếng Việt hay không
def is_vietnamese(language: str | None = None) -> bool:
    """Check if the provided or current context language is Vietnamese."""
    code = extract_primary_language(language) if language is not None else get_current_language()
    return code == "vi"
