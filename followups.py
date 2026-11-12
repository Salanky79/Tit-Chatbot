"""Generate two concise, structured follow-up prompts for a completed answer."""

from __future__ import annotations

import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from token_utils import estimate_tokens, truncate_text_to_tokens
from usage_logging import log_token_usage


logger = logging.getLogger(__name__)


def _int_setting(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _float_setting(name: str, default: float, minimum: float = 0.1) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


FOLLOWUPS_ENABLED = os.getenv("FOLLOWUPS_ENABLED", "true").strip().lower() not in {
    "0", "false", "no", "off"
}
FOLLOWUP_MODEL = (os.getenv("FOLLOWUP_MODEL") or "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"
FOLLOWUP_TIMEOUT_SECONDS = _float_setting("FOLLOWUP_TIMEOUT_SECONDS", 3.0)
FOLLOWUP_MAX_USER_TOKENS = _int_setting("FOLLOWUP_MAX_USER_TOKENS", 150)
FOLLOWUP_MAX_ANSWER_TOKENS = _int_setting("FOLLOWUP_MAX_ANSWER_TOKENS", 300)
FOLLOWUP_OUTPUT_TOKENS = _int_setting("FOLLOWUP_OUTPUT_TOKENS", 180)
FOLLOWUP_MAX_PROMPT_CHARS = _int_setting("FOLLOWUP_MAX_PROMPT_CHARS", 180)


class FollowupSuggestions(BaseModel):
    """Structured model output; exact item count is validated after parsing."""

    prompts: list[str] = Field(description="Exactly two distinct, concise follow-up prompts.")


def _prepare_context(text: str, max_tokens: int) -> str:
    text = (text or "").strip()
    if estimate_tokens(text) <= max_tokens:
        return text
    return truncate_text_to_tokens(text, max_tokens)


def _validated_prompts(parsed: object) -> list[str]:
    if isinstance(parsed, FollowupSuggestions):
        prompts = parsed.prompts
    elif isinstance(parsed, dict):
        prompts = parsed.get("prompts")
    else:
        return []

    if not isinstance(prompts, list) or len(prompts) != 2:
        return []
    if any(not isinstance(prompt, str) for prompt in prompts):
        return []

    cleaned = [prompt.strip() for prompt in prompts]
    if any(not prompt or len(prompt) > FOLLOWUP_MAX_PROMPT_CHARS for prompt in cleaned):
        return []
    if cleaned[0].casefold() == cleaned[1].casefold():
        return []
    return cleaned


def generate_followup_questions(
    user_message: str,
    assistant_answer: str,
    model_name: str | None = None,
) -> list[str]:
    """Return two validated user prompts, or an empty list if generation fails."""
    if not FOLLOWUPS_ENABLED or not assistant_answer.strip():
        return []

    clean_user = _prepare_context(user_message, FOLLOWUP_MAX_USER_TOKENS)
    clean_answer = _prepare_context(assistant_answer, FOLLOWUP_MAX_ANSWER_TOKENS)
    selected_model = (model_name or FOLLOWUP_MODEL).strip() or FOLLOWUP_MODEL

    try:
        model = ChatGroq(
            model=selected_model,
            temperature=0.2,
            max_tokens=FOLLOWUP_OUTPUT_TOKENS,
            request_timeout=FOLLOWUP_TIMEOUT_SECONDS,
        )
        structured_model = model.with_structured_output(
            FollowupSuggestions,
            method="function_calling",
            include_raw=True,
        )
        response = structured_model.invoke([
            SystemMessage(content=(
                "Create exactly two concise, distinct follow-up prompts that a user could send next. "
                "Use the same language as the supplied exchange. Write each as a direct request or "
                "question from the user's perspective. Make them specific to the answer, useful, and "
                "not repetitions of what was already answered. Return only the requested structured output."
            )),
            HumanMessage(content=(
                f"Latest user message:\n{clean_user}\n\n"
                f"Assistant answer:\n{clean_answer}"
            )),
        ])

        if not isinstance(response, dict):
            logger.warning("Follow-up model returned an unexpected structured-output value.")
            return []

        raw_response = response.get("raw")
        if raw_response is not None:
            log_token_usage(selected_model, "Follow-up Suggestions", raw_response)

        if response.get("parsing_error"):
            logger.warning("Could not parse follow-up structured output: %s", response["parsing_error"])
            return []

        prompts = _validated_prompts(response.get("parsed"))
        if len(prompts) != 2:
            logger.warning("Follow-up output did not contain two valid prompts.")
        return prompts
    except Exception as exc:
        logger.warning("Follow-up generation failed: %s", exc)
        return []
