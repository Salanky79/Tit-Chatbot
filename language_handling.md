# Language handling

This project supports Vietnamese (`vi`) and English (`en`). Language detection and user-visible system messages are centralized in `services/language.py` and the `locales/` catalogs. Technical logs remain in English.

## Request language flow

For each `/chat/stream` request, the server resolves the language in this order:

1. A valid `response_language` value supplied by an API client (`vi` or `en`).
2. A confident language result for the user's authored text, using Lingua with only Vietnamese and English enabled.
3. The last resolved language stored on the conversation record. This handles short or ambiguous messages in an established thread.
4. The browser's `Accept-Language` preference, limited to the two supported languages.
5. `DEFAULT_RESPONSE_LANGUAGE`, then Vietnamese as the application fallback.

The browser sends `language_text` separately from the message passed to the agent so attachment labels do not skew detection. The resolved value is persisted on the conversation, passed to LangGraph as `configurable.response_language`, and returned in the standard `Content-Language` response header. The frontend carries it into follow-up generation and speech recognition.

An explicit request to translate or produce particular content in another language remains an instruction to the agent. For predictable backend notices, API clients can set `response_language` explicitly; the detector does not try to infer translation intent from prose.

## Detection and configuration

Lingua runs locally and does not call an LLM or external service. The detector instance is cached and reused. Short inputs are treated as ambiguous; detector failures are logged in English and use the normal fallback chain.

Install the repository dependencies in the same Python environment used to launch the app so automatic detection is enabled:

```powershell
python -m pip install -r requirements.txt
```

If Lingua is missing, the app can still start and will use the conversation, browser, and default language fallbacks until the dependency is installed.

Optional environment settings:

| Setting | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_RESPONSE_LANGUAGE` | `vi` | Final supported-language fallback (`vi` or `en`) |
| `LANGUAGE_DETECTION_MIN_SCORE` | `0.60` | Minimum top candidate confidence |
| `LANGUAGE_DETECTION_MIN_MARGIN` | `0.15` | Minimum gap over the second candidate |

## User-visible messages and prompts

Stable message keys live in `locales/vi.json` and `locales/en.json`. `services.language.tr()` formats these strings; application code should pass a message key and values rather than branch on language to build a sentence.

The resolved language is added as a system instruction for the chatbot and follow-up generator. Image analysis receives the same instruction. Document summaries keep the source document's language when `target_language` is `auto`; a confident document-language result selects the summary language, with the current conversation language as fallback. Partial-summary notices use the conversation language.

Technical logs, exception traces, and diagnostic messages are written in English. The user-facing stream translates rate-limit, daily-quota, model-access, and generic error notices through the locale catalog. Unexpected provider exception details are logged, not exposed verbatim to the chat UI.

## Extending language support

To add a language, update the supported language type and normalization, add it to the restricted Lingua detector, provide a complete locale catalog, update the fallback validation, and add any language-specific voice-recognition locale in the frontend. Keep the request precedence and user-facing message keys shared across languages.
