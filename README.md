# Bigas — Modular MCP Server for Your Virtual AI Team

<div align="center">
  <img src="assets/images/bigas-ready-to-serve.png" alt="Bigas Logo" width="200"/>
  <br/>
  <strong>Marketing analytics, product release notes, and CTO code review today — with a pluggable provider architecture for future finance, support, and more.</strong>
</div>

Follow us on X: **[@bigasmyaiteam](https://x.com/bigasmyaiteam)**

---

## What is Bigas?

**Bigas** (Latin for *team*) is an MCP server that gives solo founders a team of virtual specialists across **marketing, product, and engineering**. It is **opinionated** toward Google Cloud (Cloud Run, GA4, GCS, Cloud Scheduler), Discord, and Jira/GitHub so you can get going quickly with minimal config. The stack is **modular and provider-based**: you can plug in new data sources and capabilities (for example finance and support), and run the Flask app on other clouds or on-premises if you prefer. Active providers are discoverable via `GET /mcp/providers` (see `CONTRIBUTING.md` and `docs/architecture.md`). It connects to your data sources and delivers AI-powered insights to Discord — or can be triggered directly from any MCP client (Claude, Cursor, etc.) or automated via Cloud Scheduler. The server exposes an **SSE-based MCP endpoint** at `GET/POST /mcp` for JSON-RPC (initialize, tools/list, tools/call), so clients connect via the standard MCP transport.

It currently includes three specialists:

| Specialist | What it does |
|---|---|
| **Senior Marketing Analyst** | GA4 web analytics + paid ads (Google Ads, Meta, LinkedIn, Reddit) → weekly reports, portfolio reports, cross-platform budget analysis |
| **Product Release Manager** | Jira Fix Version → customer-facing release notes + blog draft + social copy |
| **CTO** | GitHub PR diff → AI code review comment posted directly to the PR |

---

## Quick Start

### Prerequisites

- Google Cloud CLI (`gcloud`) installed and authenticated
- A Google Cloud project with Cloud Run and Analytics APIs enabled
- A **Cloud Run service account** with:
  - `roles/analyticsdata.reader` — to read from the GA4 Data API
  - `roles/storage.objectAdmin` — to read/write/delete reports in the GCS bucket
  - `roles/secretmanager.secretAccessor` — **only if** you enable Secret Manager (`SECRET_MANAGER=true`) for loading env vars at startup
- A **deploying user or CI account** with permissions to deploy to Cloud Run (for example `roles/run.admin` and `roles/iam.serviceAccountUser` on the execution service account)

### 1. Configure environment variables

```bash
cp env.example .env
# Edit .env with your actual values — see Environment Variables below
```

### 2. Deploy

```bash
git clone https://github.com/your-username/bigas.git
cd bigas
./deploy.sh
```

### 3. Run your first report

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report
```

Results are posted to your Discord marketing channel.

---

## Environment Variables

**Required (core):**

| Variable | Description |
|---|---|
| `GA4_PROPERTY_ID` | Google Analytics 4 property ID (Admin → Property Details) |
| `GOOGLE_PROJECT_ID` | Google Cloud project ID |
| `GOOGLE_SERVICE_ACCOUNT_EMAIL` | Service account email |

**LLM (at least one provider required):**

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Gemini API key — **default LLM**; used when `LLM_MODEL` is unset or starts with `gemini-` |
| `OPENAI_API_KEY` | OpenAI API key — alternative; used when `LLM_MODEL` starts with `gpt-` |
| `LLM_MODEL` | Global default model; defaults to `gemini-2.5-pro` if unset |

**Optional:**

| Variable | Description |
|---|---|
| `DISCORD_WEBHOOK_URL_MARKETING` | Discord webhook for marketing reports |
| `DISCORD_WEBHOOK_URL_PRODUCT` | Discord webhook for release notes and team progress |
| `STORAGE_BUCKET_NAME` | GCS bucket for report storage (default: `bigas-analytics-reports`) |
| `TARGET_KEYWORDS` | Colon-separated keywords for SEO analysis (e.g. `sustainable_swag:eco_friendly_clothing`) |
| `JIRA_BASE_URL` | Jira instance URL (required for release notes and progress updates) |
| `JIRA_EMAIL` | Jira account email |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_PROJECT_KEY` | Jira project key |
| `LINKEDIN_AD_ACCOUNT_URN` | Default LinkedIn ad account URN |
| `REDDIT_AD_ACCOUNT_ID` | Default Reddit ad account ID |

Per-feature model overrides: `BIGAS_MARKETING_LLM_MODEL`, `BIGAS_RELEASE_NOTES_MODEL`, `BIGAS_PROGRESS_UPDATES_MODEL`, `BIGAS_CTO_PR_REVIEW_MODEL`. See `env.example` and `bigas/llm/README.md`.

---

## GA4 Setup

1. Go to **Google Analytics → Admin → Property Access Management**
2. Add your service account email with the **Marketer** role
3. Copy your **Property ID** (Admin → Property Details) into `GA4_PROPERTY_ID`

> If you get a 403 error, wait a few minutes for permissions to propagate.

---

## MCP / SSE endpoint

MCP clients (Claude, Cursor, etc.) can connect using the standard MCP-over-SSE transport:

- **GET /mcp** — Opens a long-lived Server-Sent Events stream. The server sends an initial `server/ready` event and keep-alive comments so the client maintains the connection.
- **POST /mcp** — Accepts MCP JSON-RPC requests: `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`. Tool calls are routed internally to the corresponding `/mcp/tools/*` endpoints.

Tools are the same as in the HTTP API; they are listed via `tools/list` and invoked via `tools/call`. When using restricted access (`BIGAS_ACCESS_MODE=restricted`), send your access key in the configured header (e.g. `X-Bigas-Access-Key`) or as `Authorization: Bearer <key>` on POST requests to `/mcp`.

---

## API Reference

All endpoints are available at `https://your-service-url.a.run.app/mcp/tools/`.

All endpoint names in the tables below are **relative to `/mcp/tools/`**. For example, `POST weekly_analytics_report` means `POST /mcp/tools/weekly_analytics_report`.

Find your service URL with:
```bash
gcloud run services describe <your-service-name> --region=your-region --format='value(status.url)'
```

### GA4 Web Analytics

| Endpoint | Description |
|---|---|
| `POST weekly_analytics_report` | Full weekly GA4 report → Discord |
| `GET get_latest_report` | Retrieve the most recent stored report |
| `GET get_stored_reports` | List all stored reports |
| `POST analyze_trends` | Trend analysis for a given metric and date range |
| `POST analyze_underperforming_pages` | CRO + SEO + UX recommendations for high-traffic, low-conversion pages |
| `POST ask_analytics_question` | Ad-hoc natural language question against your GA4 data |
| `POST fetch_analytics_report` | Standard GA4 report |
| `POST fetch_custom_report` | Custom dimensions/metrics report |
| `POST cleanup_old_reports` | Delete reports older than N days |

**Examples:**
```bash
# Weekly report
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report

# Ask a question
curl -X POST https://your-deployment-url.com/mcp/tools/ask_analytics_question \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country had the most active users last week?"}'

# Analyze underperforming pages
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_underperforming_pages \
  -H "Content-Type: application/json" \
  -d '{"max_pages": 3}'
```

### LinkedIn Ads

| Endpoint | Description |
|---|---|
| `POST run_linkedin_portfolio_report` | One-command: discover creatives → fetch analytics → summarize → Discord |
| `POST fetch_linkedin_ad_analytics_report` | Fetch raw LinkedIn adAnalytics, cache in GCS |
| `POST summarize_linkedin_ad_analytics` | Summarize an enriched report from GCS → Discord |
| `GET linkedin_ads_health_check` | Verify API access and list ad accounts |
| `POST linkedin_exchange_code` | Exchange OAuth authorization code for refresh token |

```bash
# One-command portfolio report
curl -X POST https://your-deployment-url.com/mcp/tools/run_linkedin_portfolio_report \
  -H "Content-Type: application/json" \
  -d '{"account_urn": "urn:li:sponsoredAccount:123456", "discovery_relative_range": "LAST_30_DAYS"}'
```

> Reports are cached in GCS by a SHA-256 hash of request parameters. Use `"force_refresh": true` to bypass the cache.

### Reddit Ads

| Endpoint | Description |
|---|---|
| `POST run_reddit_portfolio_report` | One-command: fetch performance + audience → summarize → Discord |
| `POST fetch_reddit_ad_analytics_report` | Fetch Reddit Ads performance report, store in GCS |
| `POST fetch_reddit_audience_report` | Audience breakdown by interests, communities, or geography |
| `POST summarize_reddit_ad_analytics` | Summarize an enriched report from GCS → Discord |
| `GET reddit_ads_health_check` | Verify API access and list ad accounts |

### Paid Ads & Cross-platform

| Endpoint | Description |
|---|---|
| `POST run_google_ads_portfolio_report` | Campaign/ad/audience performance report → Discord |
| `POST run_meta_portfolio_report` | Meta (Facebook/Instagram) campaign performance → Discord |
| `POST run_cross_platform_marketing_analysis` | LinkedIn + Reddit + Google Ads + Meta in one report → Discord |

All portfolio endpoints support async variants (`run_*_async`) that return a `job_id` immediately. Poll with `get_job_status` and retrieve results with `get_job_result`.

### Product & Engineering

| Endpoint | Description |
|---|---|
| `POST create_release_notes` | Jira Fix Version → release notes + blog draft + social copy |
| `POST progress_updates` | Issues moved to Done in last N days → team progress update → Discord |
| `POST review_and_comment_pr` | PR diff → AI code review comment posted to GitHub |

```bash
# Release notes
curl -X POST https://your-deployment-url.com/mcp/tools/create_release_notes \
  -H "Content-Type: application/json" \
  -d '{"fix_version": "1.2.0"}'
```

Release notes output includes `customer_markdown`, `blog_markdown`, and social drafts for X, LinkedIn, Facebook, and Instagram. Every Jira issue is included exactly once under New Features, Improvements, or Bug Fixes.

---

## Automating Reports with Cloud Scheduler

Set up scheduled jobs in [Google Cloud Scheduler](https://console.cloud.google.com/cloudscheduler):

| Job | Cron | URL |
|---|---|---|
| Weekly analytics | `0 9 * * 1` | `.../weekly_analytics_report` |
| Page analysis | `0 10 * * 2` | `.../analyze_underperforming_pages` |
| LinkedIn portfolio | `0 9 * * 1` | `.../run_linkedin_portfolio_report` |
| Cleanup old reports | `0 2 1 * *` | `.../cleanup_old_reports` |

All jobs use **HTTP POST** to your Cloud Run service URL.

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │            Clients / Triggers            │
                    │  MCP Client (SSE+JSON-RPC) · Scheduler · curl │
                    └─────────────────────────────────────────┘
                                         │
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │      Flask app (e.g. Cloud Run)          │
                    │  /mcp — SSE + JSON-RPC; /mcp/tools/* — HTTP │
                    └────────────────┬────────────────────────┘
                    ┌───────────────┼────────────────────────┐
                    ▼               ▼                        ▼
             Marketing          Product                  CTO
        (GA4 + Paid Ads)  (Jira → Notes & Updates)   (PR Review)
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
      GA4 API   Ads APIs    OpenAI/Gemini
                              │
                    ┌─────────┴──────────┐
                    ▼                    ▼
              GCS Storage           Discord
```

*(GCS Storage, Discord, and Google Secret Manager are optional integrations; see `env.example` and `docs/architecture.md` for details.)*

**Services:**
- **Marketing services**
  - `GA4Service` — Google Analytics 4 data fetching
  - `LinkedInAdsService` / `RedditAdsService` / `GoogleAdsService` / `MetaAdsService` — OAuth or ADC + ads API with GCS caching
  - `StorageService` — report persistence under `weekly_reports/` and `raw_ads/{platform}/{date}/`
  - `WebScrapingService` — scrapes actual page content for CRO recommendations
- **Product services**
  - `CreateReleaseNotesService` — Jira Fix Version → multi-channel customer release comms (release notes, blog, social)
  - `ProgressUpdatesService` — Jira issues moved to Done → team progress “coach” message
- **CTO services**
  - `CtoPrReviewService` — GitHub PR diff → AI code review and comment on the PR
- **Optional infrastructure**
  - Google Cloud Storage for report persistence
  - Google Secret Manager for loading env vars at startup when `SECRET_MANAGER=true`
- **Provider registry**
  - Provider registry in `bigas/registry.py` discovers ads, analytics, and notification providers under `bigas/providers/**` at startup; active providers are listed at `GET /mcp/providers`. See [CONTRIBUTING.md](CONTRIBUTING.md) and [DESIGN_SPEC.md](DESIGN_SPEC.md) to add new providers.

---

## Local Development

```bash
# Install dependencies
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp env.example .env

# Run locally
python run_core.py
# Server available at http://localhost:8080
```

**Tests:**
```bash
python tests/test_storage.py
python tests/test_domain_extraction.py
python tests/health_check.py
```

**API docs** (once deployed): `https://your-deployment-url.com/openapi.json`

---

## Security

- Never commit `.env` or service account JSON files — both are in `.gitignore`
- Service accounts use minimal required permissions
- All external communication is HTTPS
- API keys and webhook URLs are automatically redacted from error messages
- Rate limiting: 100 requests/hour per endpoint (HTTP 429 when exceeded)

---

## Contributing

1. Fork the repo and create a feature branch
2. Follow the existing service pattern for new integrations
3. Run tests before opening a PR
4. See [CONTRIBUTING.md](CONTRIBUTING.md) for details

For security issues, contact the maintainer directly rather than opening a public issue.

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see [LICENSE](LICENSE) for details.

---

<div align="center">
  <strong>Built for solo founders who need actionable insights without a full team</strong>
</div>
