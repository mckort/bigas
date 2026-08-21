# Bigas AI usage (Cursor + LLM logs)

List-price visibility for Bigas AI spend:

1. **Per autofix round** — Discord includes Cursor token usage + list-price estimate when `autofix_followup` finalizes.
2. **Per LLM call** — `get_llm_client()` wraps the provider client and emits `event: llm_usage` (chat, PR review, marketing, Jira, …).
3. **Historical / weekly** — modular `UsageProvider`s fetch the last N days without a local event store.

Estimates are **operational only**, not invoices.

## Providers

Registered under domain `usage` (see `GET /mcp/providers`):

| Provider | Source | Config |
|---|---|---|
| `cursor` | Cursor Cloud Agents API (`List Agents` + `/usage`) | `CURSOR_API_KEY` |
| `llm_logs` | Cloud Logging lines with `event: llm_usage` | `GOOGLE_CLOUD_PROJECT` (or `GOOGLE_PROJECT_ID`) + runtime credentials with `logging.read` |

Adding Claude (or any LLM) later usually needs **no new history provider** if calls go through `bigas.llm` (`get_llm_client`) and keep emitting `llm_usage` logs. Add a new provider only for an external meter (like Cursor).

Optional: `BIGAS_CTO_USAGE_LOG_FILTER` — extra Cloud Logging filter clause ANDed into the query.

List-price for Cursor uses `BIGAS_CTO_AUTOFIX_MODEL` (default `composer-2.5`) when the agent payload has no model id.

## Tools

### `POST /mcp/tools/fetch_ai_usage`

```json
{
  "days": 7,
  "provider": "all",
  "feature_prefix": "cto_",
  "post_to_discord": false
}
```

- `provider`: `all` | `cursor` | `llm_logs`
- `feature_prefix`: optional filter (default `cto_`). Pass `""` / omit via weekly report for all features (`chat`, `marketing`, `cto_pr_review`, …).
- Returns totals, per-provider / per-feature costs, top PRs (from Cursor agent names), and events (capped at 200 in the HTTP body).

### `POST /mcp/tools/weekly_cto_ai_report`

```json
{
  "days": 7,
  "post_to_discord": true
}
```

Aggregates all active usage providers **without** a feature prefix (Cursor autofix + every `llm_usage` feature) and posts a Discord summary (default).

## Cloud Scheduler example

```bash
gcloud scheduler jobs create http cto-ai-usage-weekly \
  --location=europe-west1 \
  --schedule="0 9 * * 1" \
  --uri="https://YOUR_CLOUD_RUN_URL/mcp/tools/weekly_cto_ai_report" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"days":7,"post_to_discord":true}' \
  --time-zone="Europe/Stockholm"
```

## Discord examples

Autofix completed:

```text
**CTO autofix completed**
PR: https://github.com/acme/app/pull/12
Fixes pushed to the PR branch.
Agent: https://cursor.com/agents/bc-…
Cursor usage: 36,170 tokens (in 6,320 / out 1,450 / cacheWrite 7,100 / cacheRead 21,300)
Estimated list-price: ~$0.4200 (composer-2.5)
```

Weekly:

```text
**Bigas AI usage (last 7 days)**
Estimated list-price total: ~$12.3400
…
```

## Limits

- Cursor list has no date filter — providers page newest-first until `createdAt` is before the window.
- Deleted Cursor agents disappear from history.
- `llm_logs` depends on Cloud Logging retention; `logger.info(json.dumps(...))` is parsed from `textPayload` or `jsonPayload`.
- Dollar amounts are list-price estimates (Composer standard tier rates for `composer-2.5` unless model id contains `fast`).
