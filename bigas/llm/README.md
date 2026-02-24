# LLM abstraction (OpenAI + Gemini)

The `bigas.llm` package provides a single provider-agnostic interface for chat-style LLM calls used across CTO PR review, progress updates, release notes, marketing analytics, and SaaS duplicate recommendation checks.

## Usage

- **Feature code** does not import `openai` or `google.generativeai` directly. It uses:
  ```python
  from bigas.llm.factory import get_llm_client
  llm, model = get_llm_client(feature="cto_pr_review", explicit_model=request_model)
  text = llm.complete(messages=[...], max_tokens=4000, temperature=0.3)
  ```

- **Provider** is inferred from the **model name**:
  - `gpt-*` → OpenAI (requires `OPENAI_API_KEY`)
  - `gemini-*` → Gemini (requires `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey))

- **Model resolution** (same for all features):
  1. `explicit_model` (e.g. request body `llm_model`)
  2. Per-feature env (e.g. `BIGAS_CTO_PR_REVIEW_MODEL`)
  3. `LLM_MODEL` (provider-agnostic)
  4. Default `gemini-2.5-pro`

## Gemini (Google AI API key)

Set `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey). Use a Gemini model name (e.g. `LLM_MODEL=gemini-2.5-pro`) so the provider is inferred.

## Per-feature env overrides

| Feature                   | Env var                              |
|---------------------------|--------------------------------------|
| CTO PR review             | `BIGAS_CTO_PR_REVIEW_MODEL`          |
| Progress updates          | `BIGAS_PROGRESS_UPDATES_MODEL`       |
| Release notes             | `BIGAS_RELEASE_NOTES_MODEL`          |
| Marketing                 | `BIGAS_MARKETING_LLM_MODEL`          |
| Duplicate recommendation  | `BIGAS_DUPLICATE_RECOMMENDATION_MODEL` |

## Adding another provider (e.g. Claude)

1. Implement a class that satisfies the `LLMClient` protocol (`complete(messages, *, max_tokens, temperature, **kwargs) -> str`).
2. In `factory.py`, extend `_infer_provider_from_model` and add a branch that builds and returns the new client.
