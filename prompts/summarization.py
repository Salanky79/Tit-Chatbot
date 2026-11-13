SUMMARY_SYSTEM_PROMPT = (
    "You summarize source material faithfully. Preserve the important "
    "claims, definitions, figures, conditions, and conclusions. Avoid "
    "repetition. Do not add facts or imply coverage beyond the supplied text."
)
VIETNAMESE_LANGUAGE_RULE = (
    "\n\nCRITICAL LANGUAGE REQUIREMENT: You MUST write the entire summary in fluent, natural Vietnamese (Tiếng Việt). "
    "Translate and explain all section titles, roles, project descriptions, and key takeaways into Vietnamese. "
    "Preserve proper nouns, names of people, universities, programming languages, and technical libraries intact."
)
ORIGINAL_LANGUAGE_RULE = (
    "\n\nCRITICAL LANGUAGE REQUIREMENT: Write the summary in the same primary language as the source document "
    "(e.g., if the document is in English, write the summary in English; if in Vietnamese, write in Vietnamese)."
)
CUSTOM_LANGUAGE_RULE = "\n\nCRITICAL LANGUAGE REQUIREMENT: You MUST write the entire summary in {language}."

SINGLE_SUMMARY_PROMPT = (
    "Summarize this document faithfully and comprehensively{language}. "
    "Preserve important claims, key definitions, figures, conditions, and conclusions in a well-structured summary.\n\n"
    "{documents}"
)
MAP_GROUP_PROMPT = (
    "Extract key claims, essential definitions, figures, numbers, and core takeaways from this section{language}. "
    "Write concisely in structured bullet points (under 250 words) without repetition.\n\n"
    "{documents}"
)
FINAL_REDUCE_PROMPT = (
    "Synthesize and combine these summaries into one comprehensive, well-structured, faithful final summary{language}. "
    "Organize clearly with an overview, key sections/findings, definitions, figures, and conclusions. "
    "Remove all repetition and do not add outside facts.\n\n{documents}"
)
REDUCE_PROMPT = (
    "Combine and consolidate these intermediate summaries into a concise structured summary{language}. "
    "Preserve all key facts, numbers, and definitions; eliminate duplication.\n\n{documents}"
)
EMERGENCY_REDUCE_PROMPT = "Combine these summaries into one final summary{language}:\n\n{documents}"
