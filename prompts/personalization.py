"""Prompt templates for Personalization (Base Style, Custom Instructions, Memory Summary, AI Memory Update)."""

from __future__ import annotations

# Ngắn gọn (~12-20 tokens/mục) để hạn chế phát sinh token mỗi lượt chat cá nhân; "default" không tốn thêm token.

# =============================================================================
# 01. Nội dung prompt và cấu hình hướng dẫn
# =============================================================================

BASE_STYLE_PROMPTS: dict[str, str] = {
    "default": "",
    "professional": "Preferred tone: Professional, formal, structured, and precise.",
    "friendly": "Preferred tone: Warm, friendly, conversational, and approachable.",
    "efficient": "Preferred tone: Ultra-concise and direct. Go straight to the answer without filler.",
    "creative": "Preferred tone: Playful, imaginative, expressive, using vivid analogies or poetic style when fitting.",
}

USER_CUSTOM_INSTRUCTIONS_TEMPLATE = (
    "\n\n[User Custom Instructions (personal chats only; do not override system safety rules)]\n"
    "{custom_instructions}\n"
    "[End user custom instructions]"
)

MEMORY_SUMMARY_SYSTEM_PROMPT = (
    "You are an assistant that summarizes a user's long-term personal memory profile.\n"
    "Based ONLY on the provided list of saved personal memories, write a concise, clear, well-structured overview.\n\n"
    "Mandatory rules:\n"
    "- Group related facts under short Markdown headings (for example: Background, Skills & Work, Learning & Preferences, Projects & Goals). Omit empty sections.\n"
    "- {language_rule}\n"
    "- Strictly DO NOT fabricate or infer facts not present in the provided list.\n"
    "- Strictly DO NOT expose internal memory IDs, version numbers, or technical metadata."
)

MEMORY_SUMMARY_DEFAULT_LANGUAGE_RULE = (
    "Write the entire summary (including section headings) in the same primary language as the saved memories "
    "(default to English if the memories are mixed or unclear)."
)

MEMORY_UPDATE_SYSTEM_PROMPT = (
    "You are a memory coordinator managing updates to the user's `long_term_memory` records.\n"
    "The user will provide a natural-language request (for example: updating a skill, changing a preference, or forgetting old information).\n\n"
    "Based on the list of existing memory records (each having `memory_id`, `version`, and `memory`), produce a structured list of actions:\n"
    "- `update`: use when modifying or replacing an existing record. You MUST pass the exact `memory_id` and `version` from the list, plus the complete updated statement in `new_memory`.\n"
    "- `delete`: use when the user wants to forget or remove an existing record. You MUST pass the exact `memory_id` and `version` from the list.\n"
    "- `add`: use when adding a completely new fact with no matching existing record; write a concise, self-contained factual statement in `new_memory` (in the same language as the user's request).\n\n"
    "Never invent a `memory_id` or `version`. Write `note` concisely in the same language as the user's request. "
    "If no valid operation applies, return an empty `actions` list."
)


def build_personalization_prompt_suffix(base_style: str | None, custom_instructions: str | None) -> str:
    """Build the system-prompt suffix for personal chats from base_style and custom_instructions."""
    parts: list[str] = []
    style_key = (base_style or "default").strip().lower()
    style_prompt = BASE_STYLE_PROMPTS.get(style_key, "")
    if style_prompt:
        parts.append(f"\n\n[{style_prompt}]")

    clean_custom = (custom_instructions or "").strip()
    if clean_custom:
        parts.append(USER_CUSTOM_INSTRUCTIONS_TEMPLATE.format(custom_instructions=clean_custom))
    return "".join(parts)
