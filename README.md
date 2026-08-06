# Bigas — Modular MCP Server for Your Virtual AI Team

<div align="center">
  <img src="assets/images/bigas-ready-to-serve.png" alt="Bigas Logo" width="200"/>
  <br/>
  <strong>Marketing analytics, Jira AI workflows, release notes, and CTO code review — with a pluggable provider architecture for future finance, support, and more.</strong>
</div>

Follow us on X: **[@bigasmyaiteam](https://x.com/bigasmyaiteam)**

---

## Table of contents

- [What is Bigas?](#what-is-bigas)
- [Why Google Cloud Run?](#why-google-cloud-run)
- [Tutorial: deploy your first Bigas server](#tutorial-deploy-your-first-bigas-server)
- [Environment variables](#environment-variables)
- [GA4 setup](#ga4-setup)
- [Walkthrough: from Jira card to merged PR](#walkthrough-from-jira-card-to-merged-pr)
- [MCP / SSE endpoint](#mcp--sse-endpoint)
- [API reference](#api-reference)
- [Automating reports with Cloud Scheduler](#automating-reports-with-cloud-scheduler)
- [Modular architecture: providers](#modular-architecture-providers)
- [Architecture](#architecture)
- [Local development](#local-development)
- [Security](#security)
- [Contributing](#contributing)
- [License](#license)

---

## What is Bigas?

**Bigas** (Latin for *team*) is an open-source MCP server that gives a solo founder or small team a virtual staff across **marketing, product, and engineering** — without hiring anyone.

It currently ships three specialists:

| Specialist | What it does |
|---|---|
| **Senior Marketing Analyst** | GA4 web analytics + paid ads (Google Ads, Meta, LinkedIn, Reddit) → weekly reports, portfolio reports, cross-platform budget analysis |
| **Product Manager** | Jira board automation — AI research and design when you drag a card, Fix Version → release notes + blog/social, Done issues → team progress updates |
| **CTO** | GitHub PR diff → AI code review comment posted directly to the PR (optional autofix via Cursor cloud agents); website uptime/SSL monitoring → Discord |

Two design decisions shape everything else in this document:

1. **It's opinionated, out of the box.** Bigas assumes Google Cloud (Cloud Run, GA4, GCS, Cloud Scheduler), Discord, and Jira/GitHub, so a new deployment has almost nothing to decide — just fill in `.env` and run `./deploy.sh`. Nothing here is required to use *those specific* products elsewhere: the Flask app is a normal container that runs anywhere Docker runs, and the [provider architecture](#modular-architecture-providers) lets you swap or add data sources without touching existing code.
2. **It's modular.** Marketing, Product, and CTO are independent resource packages. Ads/finance/analytics/notification integrations are *providers* discovered at startup — enable one by setting its env vars, add a new one (e.g. TikTok Ads, QuickBooks, Slack) by dropping in a file, no core changes required. See [Modular architecture: providers](#modular-architecture-providers).

Bigas talks to your data sources, does the analysis with an LLM (OpenAI or Gemini), and pushes results to Discord — or you can call any tool directly over HTTP, from any MCP client (Claude, Cursor, etc.), or on a schedule via Cloud Scheduler.

---

## Why Google Cloud Run?

Bigas is built to sit mostly idle and burst occasionally: a weekly analytics report, a Jira card being dragged across a board a few times a day, an occasional PR review. That usage pattern is exactly what **Cloud Run** is priced for:

- **Scale to zero.** `deploy.sh` doesn't set `--min-instances`, so Cloud Run defaults to zero — you pay nothing while no request is in flight. There's no server running 24/7 waiting for the next Jira webhook.
- **Pay only for actual request time**, billed per-request in fractions of a second of CPU/memory, not per hour of a reserved VM. A founder running weekly reports and a handful of Jira/PR events a day typically stays inside Cloud Run's free tier or a few dollars a month.
- **One container image, one `gcloud run deploy`.** No cluster, no VM patching, no load balancer to configure — `deploy.sh` builds the image, pushes it to Artifact Registry, and deploys it in one shot.
- **Fits natively with the rest of the stack**: Cloud Scheduler triggers HTTP endpoints on a cron, Secret Manager can feed env vars at startup (`SECRET_MANAGER=true`), and GCS stores reports — all billed the same pay-per-use way.

None of this is required — Bigas is a stateless Flask app, so it runs equally well on Fly.io, Render, a VPS, or your laptop via `python run_core.py`. Cloud Run is simply the path this project is opinionated and tested toward, because for the "one founder, spiky traffic" use case it tends to be the cheapest place to run it.

---

## Tutorial: deploy your first Bigas server

This walks through a real first deploy, end to end, using `acme-bigas` as a stand-in for your Google Cloud project ID — swap it for your own throughout.

### 1. Prerequisites

- [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated (`gcloud auth login`)
- Docker installed and running locally (`deploy.sh` builds the image on your machine and pushes it)
- A Google Cloud project (`gcloud projects create acme-bigas` if you don't have one yet)

### 2. Enable the APIs and create the plumbing

```bash
gcloud config set project acme-bigas

# APIs Bigas needs
gcloud services enable run.googleapis.com \
  analyticsdata.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com

# Artifact Registry repo that deploy.sh pushes images to (region must match deploy.sh: europe-north1)
gcloud artifacts repositories create bigas-repo \
  --repository-format=docker --location=europe-north1
gcloud auth configure-docker europe-north1-docker.pkg.dev

# Runtime service account
gcloud iam service-accounts create bigas-runner \
  --display-name="Bigas Cloud Run runtime"

# Minimal roles — see the table below for why each is needed
gcloud projects add-iam-policy-binding acme-bigas \
  --member="serviceAccount:bigas-runner@acme-bigas.iam.gserviceaccount.com" \
  --role="roles/analyticsdata.reader"
gcloud projects add-iam-policy-binding acme-bigas \
  --member="serviceAccount:bigas-runner@acme-bigas.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

| Role | Why the runtime service account needs it |
|---|---|
| `roles/analyticsdata.reader` | Read from the GA4 Data API |
| `roles/storage.objectAdmin` | Read/write/delete reports in the GCS bucket |
| `roles/secretmanager.secretAccessor` | **Only if** you set `SECRET_MANAGER=true` to load env vars from Secret Manager at startup |

You (or your CI account) also need permission to *deploy* — typically `roles/run.admin` and `roles/iam.serviceAccountUser` on `bigas-runner`, granted to your own user or CI service account.

### 3. Configure environment variables

```bash
git clone https://github.com/mckort/bigas.git
cd bigas
cp env.example .env.acme-bigas
ln -sfn .env.acme-bigas .env
```

Edit `.env` and fill in, at minimum:

```bash
GOOGLE_PROJECT_ID=acme-bigas
GOOGLE_SERVICE_ACCOUNT_EMAIL=bigas-runner@acme-bigas.iam.gserviceaccount.com
GA4_PROPERTY_ID=123456789
GEMINI_API_KEY=your_gemini_api_key           # or OPENAI_API_KEY
DOCKER_REPO=bigas-repo
IMAGE_NAME=bigas
IMAGE_TAG=latest
```

See [Environment variables](#environment-variables) for the full list — Discord, Jira, GitHub, and ads platforms are all optional and only needed for the specialists you want to use.

### 4. Deploy

```bash
./deploy.sh
```

This builds the Docker image, pushes it to Artifact Registry, and runs `gcloud run deploy`. On success it prints your service URL.

### 5. Run your first report

```bash
curl -X POST https://your-service-url.a.run.app/mcp/tools/weekly_analytics_report
```

If `DISCORD_WEBHOOK_URL_MARKETING` is set, the report is posted to your Discord marketing channel. Try a second one to see the LLM answer an ad-hoc question against your live GA4 data:

```bash
curl -X POST https://your-service-url.a.run.app/mcp/tools/ask_analytics_question \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country had the most active users last week?"}'
```

From here: wire up [Jira automation](#walkthrough-from-jira-card-to-merged-pr) for the Product/CTO specialists, or [Cloud Scheduler](#automating-reports-with-cloud-scheduler) to run reports on a cadence instead of by hand.

---

## Environment variables

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
| `LLM_MODEL` | Global default model; defaults to `gemini-3.1-pro-preview` if unset |

**Optional:**

| Variable | Description |
|---|---|
| `DISCORD_WEBHOOK_URL_MARKETING` | Discord webhook for marketing reports |
| `DISCORD_WEBHOOK_URL_PRODUCT` | Discord webhook for release notes, progress updates, and Jira AI research notifications |
| `DISCORD_WEBHOOK_URL_CTO` | Discord webhook for PR review / engineering notifications |
| `STORAGE_BUCKET_NAME` | GCS bucket for report storage (default: `bigas-analytics-reports`) |
| `TARGET_KEYWORDS` | Colon-separated keywords for SEO analysis (e.g. `sustainable_swag:eco_friendly_clothing`) |
| `JIRA_BASE_URL` | Jira instance URL (required for release notes, progress updates, and Jira AI automation) |
| `JIRA_EMAIL` | Jira account email |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_PROJECT_KEY` | Jira project key(s), comma-separated for multi-project (e.g. `VFA,WAYW`). Per-request override via `project_key` / `project_keys`. |
| `JIRA_AUTOMATION_WEBHOOK_SECRET` | Shared secret for `jira_status_automation` (header `X-Bigas-Webhook-Secret`). Full setup: [docs/jira-automation.md](docs/jira-automation.md) |
| `GITHUB_TOKEN` | GitHub token — PR review plus optional repo context for Jira AI research |
| `MONITOR_URLS` | Comma-separated list of URLs to monitor (e.g. `https://site1.com,https://site2.com`) |
| `LINKEDIN_AD_ACCOUNT_URN` | Default LinkedIn ad account URN |
| `REDDIT_AD_ACCOUNT_ID` | Default Reddit ad account ID |

Per-feature model overrides: `BIGAS_MARKETING_LLM_MODEL`, `BIGAS_RELEASE_NOTES_MODEL`, `BIGAS_PROGRESS_UPDATES_MODEL`, `BIGAS_CTO_PR_REVIEW_MODEL`, `BIGAS_JIRA_RESEARCH_MODEL`. See `env.example` and `bigas/llm/README.md`.

---

## GA4 setup

1. Go to **Google Analytics → Admin → Property Access Management**
2. Add your service account email with the **Marketer** role
3. Copy your **Property ID** (Admin → Property Details) into `GA4_PROPERTY_ID`

> If you get a 403 error, wait a few minutes for permissions to propagate.

---

## Walkthrough: from Jira card to merged PR

This is the flow that makes the **Product Manager** and **CTO** specialists work together: dragging a Jira card triggers AI research, then AI design, then an AI-implemented pull request — with a human approval gate between every AI step.

Say you write a card with just a **Brief** — a couple of sentences on what you want and why — and drag it into the first AI column.

1. **`Research and describe (AI)`** — Bigas reads the Brief, researches the codebase/context, and writes an **AI Research** section onto the issue (your Brief is left untouched). The card moves itself to **Description approval (manual)** and posts to your `bigas-pm` Discord channel. You read the research, edit if needed, and drag the card forward yourself.
2. **`Design and plan (AI)`** — Bigas reads Brief + Research + repo context and writes an **AI Plan**: the concrete implementation approach. Moves to **Design approval (manual)**, posts to `bigas-cto`. Again, a human reviews and approves by dragging the card.
3. **`In Progress (AI)`** — Bigas launches a Cursor cloud agent against the repo mapped to this Jira project, which implements the plan and opens a pull request. The PR link is commented on the issue; you get pinged in `bigas-cto`.
4. Once the CTO specialist's autofix loop reports the PR is **ready to merge**, Bigas finds the Jira key from the PR title/body and moves the card to **Final approval (manual)** automatically — your signal to review the PR and merge.

Every AI step lands in a column with **"(manual)"** in the name — nothing merges or ships without a human dragging a card or approving a PR.

**Prompt workstream:** by default Bigas uses **product** Research/Design/Implement prompts. Add the Jira label `marketing` on website/SEO/content issues to switch to marketing-oriented prompts (audience, copy, SEO, site files in the repo).

Under the hood, each column is wired the same way: a **Jira Automation** rule (not the admin "Webhook listener") does a **Send web request** to `jira_status_automation` whenever an issue enters that status. One rule per AI status, same URL and headers, different trigger:

```bash
curl -X POST https://your-service-url.a.run.app/mcp/tools/jira_status_automation \
  -H "Content-Type: application/json" \
  -H "X-Bigas-Webhook-Secret: $JIRA_AUTOMATION_WEBHOOK_SECRET" \
  -d '{
    "issue_key": "PROJ-123",
    "to_status": "Design and plan (AI)",
    "sync": true
  }'
```

For the exact column names, the project → repo mapping, the Jira Automation rule JSON, and the daily AI-run quota, see **[docs/jira-automation.md](docs/jira-automation.md)**.

Two more Product tools round out the flow once you're shipping regularly:

```bash
# Fix Version → release notes + blog draft + social copy (X, LinkedIn, Facebook, Instagram)
curl -X POST https://your-service-url.a.run.app/mcp/tools/create_release_notes \
  -H "Content-Type: application/json" \
  -d '{"fix_version": "1.2.0"}'
```

`progress_updates` does the same for issues moved to Done in the last N days, posting a team progress summary to Discord instead.

---

## MCP / SSE endpoint

MCP clients (Claude, Cursor, etc.) can connect using the standard MCP-over-SSE transport:

- **GET /mcp** — Opens a long-lived Server-Sent Events stream. The server sends an initial `server/ready` event and keep-alive comments so the client maintains the connection.
- **POST /mcp** — Accepts MCP JSON-RPC requests: `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`.

Tools are the same as in the HTTP API; they are listed via `tools/list` and invoked via `tools/call`. The initial GET /mcp connection is unauthenticated. When using restricted access (`BIGAS_ACCESS_MODE=restricted`), you must send your access key in the configured header (e.g. `X-Bigas-Access-Key`) or as `Authorization: Bearer <key>` on subsequent POST requests to `/mcp`.

---

## API reference

All endpoint names below are **relative to `/mcp/tools/`**. For example, `POST weekly_analytics_report` means `POST /mcp/tools/weekly_analytics_report`.

Find your service URL with:
```bash
gcloud run services describe <your-service-name> --region=your-region --format='value(status.url)'
```

### GA4 web analytics

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

```bash
# Analyze underperforming pages
curl -X POST https://your-service-url.a.run.app/mcp/tools/analyze_underperforming_pages \
  -H "Content-Type: application/json" \
  -d '{"max_pages": 3}'
```

### LinkedIn Ads

| Endpoint | Description |
|---|---|
| `POST run_linkedin_portfolio_report` | One-command: discover creatives → fetch analytics → summarize → Discord |
| `POST run_linkedin_portfolio_report_async` | Async variant of the above — returns a `job_id` immediately |
| `POST list_linkedin_creatives_for_period` | Discover creatives with activity in a date range (no hard-coded creative IDs) |
| `POST fetch_linkedin_ad_analytics_report` | Fetch raw LinkedIn adAnalytics, cache in GCS |
| `POST fetch_linkedin_creative_demographics_portfolio` | Fetch per-creative, per-dimension demographic analytics (job title, function, ...), cache in GCS |
| `POST summarize_linkedin_ad_analytics` | Summarize an enriched report from GCS → Discord |
| `POST summarize_linkedin_creative_portfolio` | Summarize per-ad top segments + recommendations across multiple creatives → Discord |
| `GET linkedin_ads_health_check` | Verify API access and list ad accounts |
| `POST linkedin_exchange_code` | Exchange OAuth authorization code for refresh token |

```bash
# One-command portfolio report
curl -X POST https://your-service-url.a.run.app/mcp/tools/run_linkedin_portfolio_report \
  -H "Content-Type: application/json" \
  -d '{"account_urn": "urn:li:sponsoredAccount:123456", "discovery_relative_range": "LAST_30_DAYS"}'
```

> Ads reports are cached in GCS by a SHA-256 hash of request parameters. Use `"force_refresh": true` to bypass the cache.

### Reddit Ads

| Endpoint | Description |
|---|---|
| `POST run_reddit_portfolio_report` | One-command: fetch performance + audience → summarize → Discord |
| `POST run_reddit_portfolio_report_async` | Async variant of the above — returns a `job_id` immediately |
| `POST fetch_reddit_ad_analytics_report` | Fetch Reddit Ads performance report, store in GCS |
| `POST fetch_reddit_audience_report` | Audience breakdown by interests, communities, or geography |
| `POST summarize_reddit_ad_analytics` | Summarize an enriched report from GCS → Discord |
| `GET reddit_ads_health_check` | Verify API access and list ad accounts |
| `POST reddit_exchange_code` | Exchange OAuth authorization code for refresh token |

### Paid ads & cross-platform

| Endpoint | Description |
|---|---|
| `POST run_google_ads_portfolio_report` | Campaign/ad/audience performance report → Discord |
| `POST run_google_ads_portfolio_report_async` | Async variant — returns a `job_id` immediately |
| `POST run_meta_portfolio_report` | Meta (Facebook/Instagram) campaign performance → Discord |
| `POST run_meta_portfolio_report_async` | Async variant — returns a `job_id` immediately |
| `POST run_cross_platform_marketing_analysis` | LinkedIn + Reddit + Google Ads + Meta in one report → Discord |
| `POST run_cross_platform_marketing_analysis_async` | Async variant — returns a `job_id` immediately (use for long runs instead of a 900s Cloud Run timeout) |
| `POST get_job_status` | Poll status of any async job by `job_id` |
| `POST get_job_result` | Fetch the result of a finished async job by `job_id` |

### Product & engineering

| Endpoint | Description |
|---|---|
| `POST jira_status_automation` | Jira Automation webhook: AI handlers when issues move into AI columns — see [walkthrough](#walkthrough-from-jira-card-to-merged-pr) |
| `POST jira_status_automation_job` | Poll a background `jira_status_automation` job by `job_id` |
| `POST create_release_notes` | Jira Fix Version → release notes + blog draft + social copy |
| `POST progress_updates` | Issues moved to Done in last N days → team progress update → Discord |
| `POST review_and_comment_pr` | PR diff → AI code review comment posted to GitHub. Details: [docs/cto-pr-review.md](docs/cto-pr-review.md) |
| `POST autofix_pr` | Launch a Cursor cloud agent to push fixes for the findings in the last Bigas review comment |
| `POST autofix_followup` | Poll the autofix agent; on completion, re-reviews the PR and posts the result to Discord. Details: [docs/cto-autofix.md](docs/cto-autofix.md) |
| `POST website_monitor` | CTO tool: check configured websites (`MONITOR_URLS`) for availability and SSL certificate health. Alerts via Discord (`DISCORD_WEBHOOK_URL_CTO`) on failures. |

---

## Automating reports with Cloud Scheduler

Set up scheduled jobs in [Google Cloud Scheduler](https://console.cloud.google.com/cloudscheduler):

| Job | Cron | URL |
|---|---|---|
| Weekly analytics | `0 9 * * 1` | `.../weekly_analytics_report` |
| Page analysis | `0 10 * * 2` | `.../analyze_underperforming_pages` |
| LinkedIn portfolio | `0 9 * * 1` | `.../run_linkedin_portfolio_report` |
| Cleanup old reports | `0 2 1 * *` | `.../cleanup_old_reports` |
| Website monitoring | `0 8 * * *` | `.../website_monitor` |

All jobs use **HTTP POST** to your Cloud Run service URL. Since Cloud Run scales to zero between runs, a scheduled job is also a scheduled cold-start — expect the first request after idle time to take a few seconds longer.

### Website monitoring with Cloud Scheduler

The `website_monitor` endpoint checks your websites for HTTP availability and SSL certificate health. Configure the URLs via the `MONITOR_URLS` environment variable (comma-separated), then set up a Cloud Scheduler job:

```bash
# Scheduler location must be a supported region (e.g. europe-west1, not europe-north1).
# With BIGAS_ACCESS_MODE=restricted, send X-Bigas-Access-Key. Cloud Run is currently
# --allow-unauthenticated, so OIDC is optional; add --oidc-service-account-email if you
# later require Cloud Run IAM invoker.
gcloud scheduler jobs create http bigas-website-monitor \
  --location=europe-west1 \
  --schedule="0 8 * * *" \
  --time-zone="Europe/Stockholm" \
  --uri="https://YOUR-SERVICE-URL.a.run.app/mcp/tools/website_monitor" \
  --http-method=POST \
  --headers="Content-Type=application/json,X-Bigas-Access-Key=YOUR_ACCESS_KEY" \
  --message-body="{}"
```

`0 8 * * *` = 08:00 every day. This is a CTO-resource tool (`bigas/resources/cto`); when a site is unreachable (HTTP error, timeout) or its SSL certificate expires in less than 14 days, an alert is posted to the configured Discord webhook (`DISCORD_WEBHOOK_URL_CTO`, falling back to `_PRODUCT` or `_MARKETING`).

---

## Modular architecture: providers

Marketing, Product, and CTO are independent **resources** — you could delete any one of them and the others keep working. Within Marketing, data sources (Google Ads, Meta, LinkedIn, Reddit Ads, GA4, and future finance/support integrations) are **providers**: small classes that implement a domain base class (`AdsProvider`, `AnalyticsProvider`, `FinanceProvider`, `NotificationChannel`) and a `is_configured()` check.

At startup, `bigas/registry.py` scans `bigas/providers/**`, instantiates every provider whose required env vars are set, and exposes the active set at:

```bash
curl https://your-service-url.a.run.app/mcp/providers
```

This is what makes the opinionated defaults optional in practice: don't set LinkedIn's env vars, and the LinkedIn provider simply doesn't load — no code path to disable, nothing to comment out. Adding a new provider (e.g. TikTok Ads, QuickBooks, a Slack notification channel) means adding one file under `bigas/providers/...`, not modifying existing services.

Each resource (`bigas/resources/{marketing,product,cto}/endpoints.py`) exposes its tools as normal Flask routes under `/mcp/tools/*` and lists them in a `get_manifest()` function; `app.py` combines all three into `GET /mcp/manifest`, and the same list is what `POST /mcp` serves for `tools/list`/`tools/call` — one manifest, both transports. `bigas/tools.py` additionally defines a `@register_tool` decorator for new provider tools to self-register into that manifest instead of hand-editing it, so adding a tool for a new provider doesn't mean touching `endpoints.py`.

For the provider base classes and a worked example (adding QuickBooks as a finance provider), see **[CONTRIBUTING.md](CONTRIBUTING.md)** and **[DESIGN_SPEC.md](DESIGN_SPEC.md)**.

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │            Clients / Triggers            │
                    │  MCP · Scheduler · Jira Automation · curl │
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
        (GA4 + Paid Ads)   (Jira AI + Notes)        (PR Review)
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
      GA4 API   Ads APIs    OpenAI/Gemini
                              │
                    ┌─────────┴──────────┐
                    ▼                    ▼
              GCS Storage           Discord
```

GCS Storage, Discord, and Google Secret Manager are optional integrations (see `env.example`). For the full service breakdown, the paid-ads orchestrator, the MCP/SSE bridge internals, and the provider registry implementation, see **[docs/architecture.md](docs/architecture.md)**.

---

## Local development

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

**API docs** (once deployed): `https://your-service-url.a.run.app/openapi.json`

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
2. Follow the existing service pattern for new integrations (see [Modular architecture: providers](#modular-architecture-providers))
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
