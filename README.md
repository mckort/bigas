# Bigas — AI-Powered Marketing + Product Platform

<div align="center">
  <img src="assets/images/bigas-ready-to-serve.png" alt="Bigas Logo" width="200"/>
  <br/>
  <strong>Automated marketing analytics, paid ads reporting, and product release comms — delivered as an MCP server</strong>
</div>

Follow us on X: **[@bigasmyaiteam](https://x.com/bigasmyaiteam)**

---

## What is Bigas?

**Bigas** (Latin for *team*) is an MCP server that gives solo founders a team of virtual specialists. It typically runs on **Google Cloud Run**, but can also be deployed on **any cloud or infrastructure that can host a Python web service** (for example, other container platforms or bare VMs). It connects to your data sources and delivers AI-powered insights to Discord — or can be triggered directly from any MCP client (Claude, Cursor, etc.) or automated via Cloud Scheduler.

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
- A service account with `roles/analyticsdata.reader` and `roles/storage.objectAdmin`

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

## API Reference

All endpoints are available at `https://your-service-url.a.run.app/mcp/tools/`.

Find your service URL with:
```bash
gcloud run services describe bigas-core --region=your-region --format='value(status.url)'
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

### Google Ads & Meta

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
                    │  MCP Client · Cloud Scheduler · curl     │
                    └─────────────────────────────────────────┘
                                         │
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │         Google Cloud Run (Flask)         │
                    │  /mcp/tools/*  — MCP endpoint router     │
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
