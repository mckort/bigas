# Bigas architecture (for contributors)

This document preserves architecture, service design, and API implementation details for contributors. The main [README](../README.md) is kept short for users; use this doc when working on the codebase.

## High-level architecture

Bigas is built as a **modular monolith** with a service-oriented architecture:

```text
+--------------------------+
|         Clients          |
|  - MCP client (HTTP RPC) |
|  - Manual User (curl)    |
|  - Google Cloud Scheduler|
+--------------------------+
             |
             v
+--------------------------+
|   Google Cloud Run       |
|  (Hosting Environment)   |
+--------------------------+
             |
             v
+-------------------------------------------------+
|  Bigas Platform (app.py - Flask App)            |
|                                                 |
| +---------------------------------------------+ |
| | /mcp — Streamable HTTP MCP (POST JSON-RPC)  | |
| | /mcp/tools/* — HTTP tool endpoints          | |
| | API Gateway / Router                        | |
| +---------------------------------------------+ |
|   |                      |                      |
|   | (/marketing/*)       | (/product/*)         |
|   v                      v                      |
| +----------------------+ +--------------------+ |
| | Marketing Resource   | | Product Resource   | |
| | (GA4, LLM,           | | (Jira, LLM,        | |
| |  Discord, Storage)   | |  Discord)          | |
| +----------------------+ +--------------------+ |
|                                                 |
+-------------------------------------------------+
```

## Paid ads analytics (Google Ads, Meta, LinkedIn, Reddit)

A multi-platform **Paid Ads Analytics Orchestrator** standardizes how ads data is fetched, stored, and summarized across Google Ads, Meta (Facebook/Instagram), LinkedIn Ads, and Reddit Ads.

```text
+------------------------------+
|   Triggers / Clients         |
|  - Cloud Scheduler           |
|  - Manual curl / MCP tools   |
+------------------------------+
              |
              v
+----------------------------------------------+
|  Marketing Ads Orchestrator (Flask)          |
|  - /mcp/tools/run_google_ads_portfolio_report|
|  - /mcp/tools/run_meta_portfolio_report      |
|  - /mcp/tools/run_linkedin_portfolio_report  |
|  - /mcp/tools/run_reddit_portfolio_report    |
|  - /mcp/tools/run_cross_platform_marketing_  |
|    analysis (all four → comparison)          |
|  - /mcp/tools/fetch_*_ad_analytics_report    |
|  - /mcp/tools/fetch_*_audience_report        |
|  - /mcp/tools/summarize_*_ad_analytics       |
+----------------------------------------------+
      |              |              |              |
      v              v              v              v
+-----------+  +-----------+  +---------------+  +------------------+
|GoogleAds |  | MetaAds   |  | LinkedInAds   |  | RedditAds        |
|Service   |  | Service   |  | Service       |  | Service          |
|(ADC +    |  | (Graph API|  | (OAuth +      |  | (OAuth +         |
| API)     |  | + token)  |  |  adAnalytics) |  |  Reports API v3) |
+-----------+  +-----------+  +---------------+  +------------------+
      \              |              /                    /
       \             |             /                    /
        \            |            /                    /
         v           v           v                    v
      +----------------------------------------------+
      | StorageService (Google Cloud Storage)        |
      |  - raw_ads/google_ads/...                     |
      |  - raw_ads/meta/...                           |
      |  - raw_ads/linkedin/...                       |
      |  - raw_ads/reddit/...                         |
      |  - *.enriched.json (normalized payloads)     |
      +----------------------------------------------+
                         |
                         v
      +----------------------------------------------+
      | LLM (OpenAI or Gemini via bigas.llm)         |
      | AD_SUMMARY_PROMPTS registry                    |
      |  - ("google_ads", "ad_analytics")             |
      |  - ("meta", "ad_analytics")                  |
      |  - ("linkedin", "ad_analytics")               |
      |  - ("linkedin", "creative_portfolio")        |
      |  - ("reddit",  "ad_analytics")               |
      |  - ("reddit",  "portfolio")                  |
      |  - ("cross_platform", "budget_analysis")     |
      +----------------------------------------------+
                         |
                         v
               +------------------------+
               | Discord (Marketing)    |
               | - Portfolio reports    |
               | - Segment insights     |
               | - Actionable recs      |
               +------------------------+
```

At a high level:

- **Platform services** (`GoogleAdsService`, `MetaAdsService`, `LinkedInAdsService`, `RedditAdsService`) hide API specifics, authentication, and rate limits.
- **Standardized storage** uses `raw_ads/{platform}/{date}/...` for raw reports and matching `.enriched.json` files for normalized, LLM-ready payloads.
- The **Paid Ads Analytics Orchestrator** endpoints handle date ranges, **caching**, and coordinating multi-step jobs (discovery → fetch → enrich → summarize).
- **Summaries** are generated via a shared `AD_SUMMARY_PROMPTS` registry, so each platform + report type gets a consistent, opinionated analysis, including a dedicated prompt for cross-platform budget recommendations (portfolio overview, key segments, underperformers, concrete next steps).
- **Output** is posted to Discord for marketing stakeholders.

## MCP bridge (Streamable HTTP)

The `/mcp` endpoint in `app.py` implements Streamable HTTP MCP for Cursor, Claude, and Grok Bot / Cloud Agents:

- **POST /mcp** accepts JSON-RPC 2.0 requests: `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`. The handler builds the combined tool manifest and, for `tools/call`, dispatches to the corresponding `/mcp/tools/*` route via the Flask test client. Tool responses are returned as MCP result content.
- **GET /mcp** returns `405 Method Not Allowed`. Optional GET SSE is omitted on purpose: a long-lived stream pinned gunicorn's single worker, and Cursor Cloud Agents do not support SSE.
- **GET /.well-known/mcp.json** is public. OAuth discovery URLs under `/.well-known/oauth-*` return 404 (Bigas uses a static access key, not OAuth).

Access control (`BIGAS_ACCESS_MODE`, `BIGAS_ACCESS_KEYS`) applies to POST `/mcp`. Clients send `X-Bigas-Access-Key` or `Authorization: Bearer <key>`. A 401 includes `WWW-Authenticate: Bearer`.

### Caching (ads reports)

Reports are cached in GCS by a SHA-256 hash of request parameters. Use `"force_refresh": true` in the request body to bypass the cache. Raw and enriched blobs live under `raw_ads/{platform}/{date}/...`; summarize endpoints read from these stored blobs.

## Service layer

### Marketing analytics

- **`GA4Service`**: Google Analytics 4 API interactions and data extraction.
- **LLM-backed analysis**: Marketing (and product) features use the shared `bigas.llm` abstraction (OpenAI or Gemini; model via `LLM_MODEL` or per-feature env such as `BIGAS_CTO_PR_REVIEW_MODEL`). See `bigas/llm/README.md`.
- **`OpenAIService`**: Legacy wrapper for marketing analysis; in practice uses the same LLM abstraction.
- **`TemplateService`**: Template-driven analytics queries, including event analysis templates.
- **`TrendAnalysisService`**: Orchestrates trend analysis and underperforming-page identification.
- **`StorageService`**: Weekly report storage in Google Cloud Storage with enhanced metadata; also `raw_ads/{platform}/{date}/` for ads.
- **`WebScrapingService`**: Fetches and analyzes actual page content for concrete, page-specific CRO recommendations.

### Product

- **`CreateReleaseNotesService`**: Fetches Jira issues by Fix Version and generates customer-facing release notes + comms pack (blog, social drafts).
- **`ProgressUpdatesService`**: Jira issues moved to Done in a window → team progress “coach” message (e.g. to Discord).
- **`XPostsService`**: last N days of git activity → LLM-filtered X draft stored in GCS (`x_drafts/`) → Discord Approve/Decline. Publishing uses the X notification provider (`bigas/providers/notifications/x.py`).

### CTO

- PR review flow: GitHub PR diff → LLM (OpenAI or Gemini) → single comment posted/updated on the PR via GitHub API (marker-based updates to avoid spam). See `docs/cto-pr-review.md`.
- **Automated MCP QA (BIG-5):** `POST /mcp/tools/run_qa` accepts a diff and target MCP URL. The agent lists tools via `/mcp/manifest`, plans relevant calls with an LLM, invokes them through `POST /mcp` (`tools/call`), and evaluates output quality. Excellent runs post a brief summary to `DISCORD_WEBHOOK_URL_QA`. Improvements are stored under `qa_drafts/` in GCS with signed Approve/Decline links at `/api/qa-proposals/*` (same HMAC pattern as X posts). Approve creates a Jira issue in `JIRA_CTO_PROJECT_KEY`; new-feature suggestions create issues in `JIRA_PM_PROJECT_KEY` and notify `DISCORD_WEBHOOK_URL_PRODUCT`. Optional trigger: `.github/workflows/qa_agent.yml` when `BIGAS_QA_ENABLED=true`.

### DevOps (BIG-7)

- **Pre-flight risk check:** `POST /mcp/tools/check_deployment_risk` compares git refs (latest release tag → default branch by default) and flags database migrations, dependency lockfiles, and deploy/infrastructure config changes.
- **Deploy trigger:** `POST /mcp/tools/trigger_deployment` dispatches GitHub Actions workflows configured in `BIGAS_DEPLOY_WORKFLOW_MAP` (e.g. separate `deploy-backend.yml` and `deploy-web.yml` for VFA/vcfieldassistant). Requires `GITHUB_TOKEN` with Actions write access and `workflow_dispatch` on target workflows.
- **Status & health:** `POST /mcp/tools/get_deployment_status` polls a workflow run by ID; `POST /mcp/tools/check_website_health` performs an HTTP GET against the live site URL.
- **Chat agent:** DevOps specialist in `bigas/chat/db.py` with tools routed via `bigas/agents/chief_of_staff.py`.

### Provider registry

- Concrete providers live under `bigas/providers/**` and implement the relevant base classes (see `bigas/providers/*/base.py`).
- `bigas/registry.py` discovers active providers at startup and exposes them via `GET /mcp/providers`.
- New providers can usually be added by adding a module under `bigas/providers/...` and setting the required env vars. See [CONTRIBUTING.md](../CONTRIBUTING.md) and [DESIGN_SPEC.md](../DESIGN_SPEC.md).

## Data flow (marketing)

1. **Weekly reports**: Generated with GA4 data and stored in GCS.
2. **Page performance analysis**: Underperforming pages (conversions and events) → URLs extracted from GA4.
3. **Web scraping**: Actual page content (titles, CTAs, H1 tags) fetched for concrete recommendations.
4. **AI marketing analysis**: Executive summary and structured recommendations (e.g. 7 questions) via the configured LLM.
5. **Discord**: Structured reports with summaries and recommendations.
6. **Metadata**: Timestamps and report structure stored for organization and cleanup.
7. **Cleanup**: Old reports can be removed via `cleanup_old_reports` to manage cost. The same job also deletes expired X drafts under `x_drafts/`.

## Secret Manager (optional)

When `SECRET_MANAGER=true`, the app loads env vars listed in `SECRET_MANAGER_SECRET_NAMES` from Google Secret Manager at startup (one secret per name; payload = plain value). Bootstrap vars (e.g. `GOOGLE_PROJECT_ID`, `GOOGLE_SERVICE_ACCOUNT_EMAIL`, `SECRET_MANAGER`, `SECRET_MANAGER_SECRET_NAMES`) stay in `.env`. The Cloud Run service account needs `roles/secretmanager.secretAccessor`. You can sync from `.env` once with `python scripts/sync_env_to_secret_manager.py`.

## Chat web interface (BIG-6)

The chat UI is a React SPA (`frontend/`) served from `frontend/dist` at `/`. Flask exposes REST endpoints under `/api/*` in `bigas/resources/chat/endpoints.py`.

```text
+-------------+     Firebase Auth (prod)     +------------------+
|  Browser    | --------------------------> |  /api/auth/*     |
|  React SPA  |     poll messages/feed      |  /api/chat/*     |
+-------------+ --------------------------> |  /api/feed       |
             |                               +------------------+
             |                                        |
             v                                        v
                                    +----------------------------+
                                    | Firestore or in-memory     |
                                    | (users, threads, messages, |
                                    |  agent_configs, activity)  |
                                    +----------------------------+
                                             ^
                                             | mirror
                                    +----------------------------+
                                    | discord_webhook.py         |
                                    | (existing Discord posts)   |
                                    +----------------------------+
```

- **Chief of Staff** (`bigas/agents/chief_of_staff.py`): uses `get_llm_client(feature="chat")` and delegates to Marketing/Product/CTO/DevOps via MCP tool calls or OpenAI function calling.
- **Async callbacks**: sub-agents (or background jobs) POST to `/api/chat/callback` with `thread_id` to append results; the UI polls `GET /api/chat/threads/<id>/messages`.
- **Storage**: `bigas/chat/db.py` — `MemoryChatStore` when `CHAT_STORAGE_MODE=memory`; `FirestoreChatStore` when Firestore is configured.
- **Auth**: `bigas/chat/auth.py` — Firebase JWT verification or dev token (`CHAT_AUTH_MODE=dev`). Chat access in Firebase mode is limited to `CHAT_ALLOWED_EMAILS` (set `*` for open chat; otherwise falls back to `CHAT_ADMIN_EMAILS` when unset). Agent config updates require an email listed in `CHAT_ADMIN_EMAILS`.

## Email ingest (BIG-9)

Chief of Staff inbox triage via IMAP polling (Migadu-compatible):

```text
Cloud Scheduler (nightly) --> POST /api/v1/providers/email/sync
                                      |
                                      v
                            bigas/providers/email/imap.py
                            (fetch UNSEEN, mark read / move)
                                      |
                                      v
                            bigas/agents/email_processor.py
                            (LLM summary + action proposals)
                                      |
                                      v
                            Chief of Staff chat thread (Firestore)
                                      |
                                      v
                            React chat UI — Approve / Reject buttons
                            POST /api/v1/chat/proposals/<id>/approve|reject
```

- **Provider**: `ImapEmailProvider` under `bigas/providers/email/`; registered in `bigas/registry.py` when `BIGAS_EMAIL_IMAP_*` env vars are set.
- **Sync endpoint**: `bigas/resources/email/endpoints.py` — secured by `BIGAS_ACCESS_KEYS` when access mode is restricted (same pattern as `/mcp/tools/*`).
- **Proposals**: assistant messages carry `metadata.type=action_proposal` with `actions[]` (`delegate`, `tool`, or `draft_reply`). Approved actions execute via `execute_proposal_action()`; rejections update metadata only.
- **Target thread**: `BIGAS_EMAIL_SYNC_USER_EMAIL` / `CHAT_ADMIN_EMAILS` → user's most recent Chief thread (`get_or_create_chief_thread`).
