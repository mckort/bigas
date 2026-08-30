# Bigas AI usage (Cursor + LLM logs)

List-price visibility for Bigas AI spend plus GCP invoice line items and Tavily:

1. **Per autofix round** — Discord includes Cursor token usage + list-price estimate when `autofix_followup` finalizes.
2. **Per LLM call** — `get_llm_client()` wraps the provider client and emits `event: llm_usage` (chat, PR review, marketing, Jira, …).
3. **Historical / weekly** — modular `UsageProvider`s fetch the last N days without a local event store. Cloud Scheduler (`bigas-cto-ai-usage-weekly`, Sunday 16:00) posts numbers + LLM analysis to the **CFO** chat thread.

Estimates are **operational only**, not invoices. Gemini thinking is billed as output: when `total_tokens` is present, billed output is `total − prompt` (same rule as VC Field Assistant), so Pro PR-review is not undercounted. `gcp_billing` is the Cloud Billing export (invoice, ~1 day lag). Tavily is VCFA Firestore list-price, not GCP.

## Providers

Registered under domain `usage` (see `GET /mcp/providers`). **None are on by default.** List the names you want in `BIGAS_USAGE_PROVIDERS` (comma-separated). Omit it and the weekly report runs with no cost sources.

| Provider | Source | Also needs |
|---|---|---|
| `cursor` | Cursor Cloud Agents API (`List Agents` + `/usage`) | `CURSOR_API_KEY` |
| `llm_logs` | Cloud Logging lines with `event: llm_usage` | Home project + `vcfieldassistant` (override with `BIGAS_LLM_USAGE_PROJECTS`). Runtime SA (`bigas-run`) needs `roles/logging.viewer` on **each** scanned project (home + VCFA). |
| `gcp_billing` | Cloud Billing export in BigQuery (`gcp_billing` dataset) | Same project list as `llm_logs`. Runtime SA needs `bigquery.jobUser` + dataset read. Enable **Standard usage cost** export to `bigas-503008.gcp_billing` in [Billing export](https://console.cloud.google.com/billing/011097-9C6611-22F8ED/export?project=bigas-503008). If the preferred dataset is empty, Bigas also scans other datasets in the project for `gcp_billing_export_v1_*` tables. EU dataset backfills current + previous month. Gemini on the invoice is `gcp.gemini_invoice` — do not add it to `llm_logs`. |
| `tavily` | VCFA Firestore `aiUsageDaily` shards (`byProvider.tavily` / `tavily.*` features) | `BIGAS_TAVILY_USAGE_PROJECT` (default `vcfieldassistant`). Runtime SA needs `roles/datastore.viewer` on that project. |

To add another system, drop a `UsageProvider` subclass in `bigas/providers/usage/` and list its `name` in `BIGAS_USAGE_PROVIDERS`. See README [Plug in a usage source](../README.md#plug-in-a-usage-source).

Adding Claude (or any LLM) later usually needs **no new history provider** if calls go through `bigas.llm` (`get_llm_client`) and emit `event: llm_usage` structured logs. VC Field Assistant emits the same line from `recordLlmUsage`, with extra fields:

- `app` (`bigas` | `vcfieldassistant`)
- `gcp_project`
- `model_tier` (`judgment` = Pro / thesis-moats-landscape, `helper` = Flash)
- `analysis_pass` (e.g. `derived`, `competitor_proposal`)
- `empty_response` / `empty_fallback` / `thinking_budget`

`fetch_ai_usage` totals include `by_app`, `by_model_tier`, `empty_response_events`, and `empty_fallback_events`. The **CFO** chat agent reads these and proposes savings.

Optional: `BIGAS_USAGE_PROVIDERS` — comma-separated names to enable (`cursor,llm_logs,gcp_billing,tavily`). Unset = none.
`BIGAS_CTO_USAGE_LOG_FILTER` — extra Cloud Logging filter clause ANDed into the query.
`BIGAS_LLM_USAGE_PROJECTS` — comma-separated GCP project IDs (default: current project + `vcfieldassistant`).
`BIGAS_GCP_BILLING_DATASET` — BigQuery dataset for billing export (default `gcp_billing`).
`BIGAS_TAVILY_USAGE_PROJECT` — Firestore project for Tavily rollups (default `vcfieldassistant`).

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

- `provider`: `all` | `cursor` | `llm_logs` | `gcp_billing` | `tavily`
- `feature_prefix`: optional filter. Omit or `""` for all features (Bigas chat/PR review **and** VCFA living analysis). Example: `cto_` or `llm.`.
- Returns totals (including `activity_by_feature` LLM call counts), per-provider / per-feature costs, top PRs (from Cursor agent names), and events (capped at 200 in the HTTP body). Weekly CFO reports use `totals.activity_by_feature`, not the truncated events list.

### `POST /mcp/tools/weekly_cto_ai_report`

```json
{
  "days": 7,
  "post_to_discord": true
}
```

Aggregates all active usage providers **without** a feature prefix (Cursor autofix + every `llm_usage` feature), asks an LLM for a usage analysis and a model-landscape check (current stack plus leading Gemini/Claude/GPT/Cursor alternatives), and posts to the **CFO** chat thread by default. The numbers block leads with an executive summary (list-price, GCP invoice or blocked status, week-over-week vs the prior equal window, top drivers, apps), then area buckets, top features with share/calls/$/call, and top PRs. The analysis uses fixed headings (`Drivers` / `Savings` / `Model`, ≤180 words). Delivery uses `post_long_to_discord` so **chat gets the full message once** while Discord is chunked under the 2k limit. The analysis may challenge keeping Pro on living-analysis judgment only if quality would hold or improve — performance must not get worse. Optional `DISCORD_WEBHOOK_URL_CFO`. Does not post to the CTO thread.

## Cloud Scheduler example

```bash
gcloud scheduler jobs create http cto-ai-usage-weekly \
  --location=europe-west1 \
  --schedule="0 16 * * 0" \
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
**CFO: AI + cloud usage (last 7 days)**
List-price (LLM + Cursor + Tavily): ≈$12.34 · Events: 120
GCP invoice: unavailable — enable [Standard usage cost export](https://console.cloud.google.com/billing/011097-9C6611-22F8ED/export?project=bigas-503008) to `bigas-503008.gcp_billing` (first rows can take a few hours).
vs prior 7d: $10.10 → $12.34 (+22%)
Top drivers: cto_pr_review 54% (≈$6.66) · llm.living_analysis 18% (≈$2.22) · …
Apps: bigas $9.10 · vcfieldassistant $3.24

By area:
- Engineering (PR + autofix): ≈$8.00 (65%)
…

**CFO analysis**
### Drivers
…
### Savings
…
### Model
…
```

Posted via `post_long_to_discord` → full text once in the CFO chat thread; Discord receives newline-safe chunks.
## Limits

- Cursor list has no date filter — providers page newest-first until `createdAt` is before the window.
- Deleted Cursor agents disappear from history.
- `llm_logs` depends on Cloud Logging retention; `logger.info(json.dumps(...))` is parsed from `textPayload` or `jsonPayload`.
- Dollar amounts are list-price estimates (Composer standard tier rates for `composer-2.5` unless model id contains `fast`).
