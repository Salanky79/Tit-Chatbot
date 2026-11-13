FOLLOWUP_PROMPT = (
    "Create exactly two concise, distinct follow-up prompts that a user could send next based on this exchange.\n"
    "Use the same language as the conversation (e.g. Vietnamese). Write each prompt from the user's perspective.\n"
    "Make them specific, useful, and not repetitions of what was already answered.\n\n"
    "User message:\n{user_message}\n\n"
    "Assistant answer:\n{assistant_answer}"
)
