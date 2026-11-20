"""Language detection, request-scoped language state, and UI message catalog."""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar, Token
from functools import lru_cache
from pathlib import Path
from typing import Literal

LanguageCode = Literal["vi", "en"]
LOGGER = logging.getLogger(__name__)
DEFAULT_LANGUAGE: LanguageCode = "vi"
_current_language: ContextVar[LanguageCode] = ContextVar("response_language", default=DEFAULT_LANGUAGE)
_LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"


@lru_cache(maxsize=1)
def _detector():
    try:
        from lingua import Language, LanguageDetectorBuilder
    except ImportError:
        LOGGER.error(
            "Lingua language detector is unavailable. Install project dependencies with "
            "'python -m pip install -r requirements.txt' to enable automatic language detection."
        )
        return None
    return LanguageDetectorBuilder.from_languages(Language.VIETNAMESE, Language.ENGLISH).build()


def normalize_language(value: str | None) -> LanguageCode | None:
    if not value:
        return None
    normalized = value.strip().lower().replace("_", "-").split("-", 1)[0]
    normalized = {
        "vietnamese": "vi", "english": "en",
        "tiếng việt": "vi", "tieng viet": "vi",
        "tiếng anh": "en", "tieng anh": "en",
    }.get(normalized, normalized)
    return normalized if normalized in ("vi", "en") else None  # type: ignore[return-value]


def detect_language(text: str | None) -> LanguageCode | None:
    """Return vi/en only when Lingua has a clear winner; otherwise defer to fallback."""
    clean_text = (text or "").strip()
    if len(clean_text) < 12:
        return None
    try:
        detector = _detector()
        if detector is None:
            return None
        scores = detector.compute_language_confidence_values(clean_text)
    except Exception:
        LOGGER.exception("Language detection failed; using configured fallback.")
        return None
    if not scores:
        return None
    winner = scores[0]
    runner_up = scores[1].value if len(scores) > 1 else 0.0
    try:
        min_score = float(os.getenv("LANGUAGE_DETECTION_MIN_SCORE", "0.60"))
        min_margin = float(os.getenv("LANGUAGE_DETECTION_MIN_MARGIN", "0.15"))
    except (TypeError, ValueError):
        min_score, min_margin = 0.60, 0.15
    min_score = min(max(min_score, 0.0), 1.0)
    min_margin = min(max(min_margin, 0.0), 1.0)
    if winner.value < min_score or winner.value - runner_up < min_margin:
        return None
    code = winner.language.iso_code_639_1.name.lower()
    return normalize_language(code)


def resolve_language(
    text: str | None,
    *,
    explicit: str | None = None,
    previous: str | None = None,
    accept_language: str | None = None,
) -> LanguageCode:
    """Resolve request language: explicit choice, confident input, thread, browser, default."""
    return (
        normalize_language(explicit)
        or detect_language(text)
        or normalize_language(previous)
        or _preferred_accept_language(accept_language)
        or normalize_language(os.getenv("DEFAULT_RESPONSE_LANGUAGE"))
        or DEFAULT_LANGUAGE
    )


def _preferred_accept_language(header: str | None) -> LanguageCode | None:
    if not header:
        return None
    choices: list[tuple[float, int, LanguageCode]] = []
    for index, item in enumerate(header.split(",")):
        parts = item.strip().split(";")
        language = normalize_language(parts[0])
        quality = 1.0
        for parameter in parts[1:]:
            key, separator, value = parameter.strip().partition("=")
            if separator and key.lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if language and quality > 0:
            choices.append((quality, -index, language))
    return max(choices, default=(0.0, 0, None))[2]


def set_current_language(language: str | None) -> Token[LanguageCode]:
    return _current_language.set(normalize_language(language) or DEFAULT_LANGUAGE)


def reset_current_language(token: Token[LanguageCode]) -> None:
    _current_language.reset(token)


def get_current_language() -> LanguageCode:
    return _current_language.get()


@lru_cache(maxsize=2)
def _catalog(language: LanguageCode) -> dict[str, str]:
    path = _LOCALE_DIR / f"{language}.json"
    with path.open(encoding="utf-8") as catalog_file:
        return json.load(catalog_file)


def tr(key: str, language: str | None = None, **values: object) -> str:
    """Translate a stable message key using the application's locale catalog."""
    locale = normalize_language(language) or get_current_language()
    template = _catalog(locale).get(key)
    if template is None:
        LOGGER.error("Missing localization key '%s' for language '%s'.", key, locale)
        template = _catalog(DEFAULT_LANGUAGE).get(key, key)
    return template.format(**values)


def response_language_instruction(language: str | None) -> str:
    return tr("agent_language_instruction", language)
