# RAG evaluation

This package evaluates a small, hand-authored Vietnamese benchmark with four
LLM judges. The dataset and its two short fixture documents are defined in
`dataset.py`; no external benchmark or separate corpus file is required.

## Safety defaults

- `python -m evaluation.rag_eval.runner --validate-only` validates fixtures and
  performs no model, LangSmith, ChromaDB, or MongoDB calls.
- Dataset synchronization is explicit: add `--sync-langsmith`. `case_id` is
  persisted inside example metadata so sync and remote lookups remain consistent.
- Local runs disable LangSmith tracing for the duration of the run and restore
  the previous environment values afterward.
- A local model/Chroma evaluation run is explicit and requires
  `--run-eval --allow-eval-chroma-write`.
- LangSmith evaluation is explicit and requires
  `--langsmith-run --allow-eval-chroma-write`; add `--sync-langsmith` only when
  the dataset also needs to be created or synchronized.
- Every Chroma persistence directory and result artifact is scoped to a unique
  directory under `evaluation/rag_eval/results/`. Existing run directories are
  never reused.
- The target reuses `services.rag.add_document_to_rag()` and
  `services.rag.retrieve_from_rag()`. During a run, an evaluation-only
  vector-store binding is installed and restored in `finally`. Runs must be
  sequential in a process because the binding temporarily replaces a module
  function.
- Generation uses the project Groq provider with an evaluation QA prompt. It
  does not construct the LangGraph agent or access MongoDB checkpoints.
- The initial benchmark has 10 cases. A hard cap of 20 cases per run is enforced
  until the smaller set has been reviewed. Generation output is capped at 512
  tokens by default; judge output is capped at 700 tokens by default.
- Structured judge invocation uses resilient schema validation: `strict=False`
  with automatic fallback to `json_mode` and raw JSON cleanup to avoid Groq
  HTTP 400 `json_validate_failed` errors on nested Pydantic schemas.
- Evaluator context matching normalizes IDs and marks missing judge grades as
  `partial` with per-context breakdown, avoiding misleading artificial score penalties.
- Rich evaluation diagnostics (claim lists, per-chunk grades, evidence snippets)
  are preserved in LangSmith and local JSONL metadata.
- Run lifecycle states (`initializing` -> `running` -> `completed` or `failed`)
  are tracked in `run_metadata.json` to prevent orphaned run directories.
- Transient provider failures are retried up to four attempts; if a provider
  returns a retry-after duration, the runner honors it (capped at 30 seconds).

## Evaluators and scoring formulas

1. **Retrieval relevance** (`retrieval_relevance`, scale 0-4):
   Judges relevance of each retrieved chunk independently (0 = unrelated, 4 = directly provides needed evidence). Returns the mean score across retrieved contexts.

2. **Answer relevance** (`answer_relevance`, scale 0-4):
   Judges whether the answer directly addresses the user question and its requested scope, independent of truthfulness.

3. **Groundedness** (`groundedness`, scale 0-4):
   Decomposes the answer into atomic factual claims. For `N` claims, let `S` be the number labeled `supported`, `C` the number labeled `contradicted`, and `U` the number labeled `not_found`:
   ```text
   groundedness_score_0_to_4 = round(4 * S / (S + C + U), 2)
   normalized_score = groundedness_score_0_to_4 / 4
   ```
   Both contradicted and not-found claims contribute zero. If there are no factual claims, the metric returns `no_claims` with no numeric score. Common label synonyms (e.g. `unsupported`) are normalized automatically.

4. **Correctness** (`correctness`, scale 0-4):
   Compares the generated answer against the reviewed reference answer for semantic accuracy and completeness.

## Running tests

Tests use `unittest`, fake vector stores and mocked model clients. They do not
write to either production or evaluation ChromaDB, call MongoDB, sync LangSmith,
or call a model API.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m unittest discover -s evaluation\rag_eval\tests -v
```

## Running evaluation

Validate fixtures only (no network/writes):
```powershell
python -m evaluation.rag_eval.runner --validate-only
```

Run local evaluation (requires explicit Chroma write permission):
```powershell
python -m evaluation.rag_eval.runner --run-eval --allow-eval-chroma-write --limit 10
```

## Output

Each evaluation run writes `results.jsonl` and `run_metadata.json` below its
unique run directory under `evaluation/rag_eval/results/`. Local run results are
append-only by unique path; they do not replace the benchmark or another run's
output.
