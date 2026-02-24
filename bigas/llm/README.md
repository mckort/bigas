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
  - `gemini-*` → Gemini: either Google AI (`GEMINI_API_KEY`) or Vertex AI (ADC, no key)

- **Model resolution** (same for all features):
  1. `explicit_model` (e.g. request body `llm_model`)
  2. Per-feature env (e.g. `BIGAS_CTO_PR_REVIEW_MODEL`)
  3. `LLM_MODEL` (provider-agnostic)
  4. Default `gpt-4o`

## Gemini: Vertex AI (default on GCP) vs API key

- **Vertex AI (default when `GOOGLE_PROJECT_ID` is set)**  
  On Cloud Run, `GOOGLE_PROJECT_ID` is always set, so we use **Vertex AI with ADC** automatically. No `GEMINI_USE_VERTEX` or API key needed. Give the Cloud Run service account **Vertex AI User** and enable the **Vertex AI API**. Optional: `VERTEX_AI_LOCATION` (default `europe-west1`).

- **Google AI (API key)**  
  For local runs without a project, or to force the API key path, set `GEMINI_API_KEY` (from [Google AI Studio](https://aistudio.google.com/apikey)). To use the API key even when `GOOGLE_PROJECT_ID` is set (e.g. testing), set `GEMINI_USE_API_KEY=true`.

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
