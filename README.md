# 🚀 Bigas - AI-Powered Marketing + Product Platform

<div align="center">
  <img src="assets/images/bigas-ready-to-serve.png" alt="Bigas Logo" width="200"/>
  <br/>
  <strong>Automated marketing analytics (GA4 + paid ads), product release comms, and PR reviews with AI</strong>
</div>

## 📱 Stay Updated

Follow us on X for the latest updates, feature announcements, and marketing insights:
**[@bigasmyaiteam](https://x.com/bigasmyaiteam)**

## 📊 Overview

**Bigas** is an AI team concept designed to provide virtual specialists for different business functions.

Today it includes:
- **The Senior Marketing Analyst** – GA4 web analytics + **paid ads (Google Ads, Meta, LinkedIn, Reddit)** → insights, weekly reports, portfolio reports, cross-platform budget analysis
- **The Product Release Manager** (Jira → customer-facing release notes + social/blog drafts)
- **The CTO** (GitHub PR review → AI code review comment on pull requests)

### Marketing view of Bigas

Bigas runs in the cloud. You access it to run marketing specialist reports and create release notes (among other things). PR reviews are triggered automatically from GitHub when you open or update a pull request. Scheduled reports (e.g. weekly analytics or ad portfolio reports) are triggered by Google Cloud Scheduler.

```text
                    ┌─────────────────────────────────────────────────────────┐
                    │              BIGAS (runs in the cloud)                   │
                    │         Google Cloud Run · Your deployment URL           │
                    └─────────────────────────────────────────────────────────┘
                                              │
         ┌───────────────────────────────────┼───────────────────────────────────┐
         │                                   │                                   │
         ▼                                   ▼                                   ▼
┌─────────────────┐               ┌─────────────────────┐               ┌─────────────────┐
│  You (user)     │               │  GitHub             │               │  Google Cloud   │
│  access Bigas   │               │  (open/sync PR)     │               │  Scheduler      │
│  via HTTP/MCP   │               │                     │               │                 │
└────────┬────────┘               └──────────┬──────────┘               └────────┬────────┘
         │                                   │                                   │
         │  Trigger reports,                  │  PR review                       │  Scheduled
         │  create release notes             │  automatically                   │  reports
         │  (examples)                       │  triggered                       │  (e.g. weekly)
         │                                   │                                   │
         └───────────────────────────────────┼───────────────────────────────────┘
                                             │
                                             ▼
                    ┌─────────────────────────────────────────────────────────┐
                    │  Bigas produces: marketing specialist reports,           │
                    │  release notes (and social/blog drafts), PR review       │
                    │  comments, and scheduled analytics → e.g. Discord       │
                    └─────────────────────────────────────────────────────────┘
```
### 🎯 Our Goal
To build a comprehensive AI team that can handle various business functions, starting with marketing and expanding to other areas like sales, customer support, product development, and more.

### 🚀 Current Implementations

Bigas is a **modular AI platform** that exposes marketing analytics and product tooling over HTTP (MCP tools) and can post outputs to Discord.

**What our Senior Marketing Analyst does:**
- 📣 **Paid Ads Analytics (Google Ads, Meta, LinkedIn, Reddit)**: Portfolio reports per platform, cross-platform comparison and budget recommendations, optional GA4 Paid Social attribution; post to Discord
- 📈 **Weekly Analytics Reports**: Automated GA4 analysis with AI-powered insights and comprehensive marketing analysis
- 🔍 **Advanced Page Performance Analysis**: Identifies underperforming pages considering both conversions AND events, with web scraping for concrete recommendations
- 🎯 **Expert Marketing Recommendations**: Provides specific, actionable improvement suggestions based on actual page content and scraped data
- 📊 **Discord Integration**: Posts detailed reports with executive summaries and expert recommendations directly to your Discord channel
- 🗄️ **Data Storage**: Stores reports in Google Cloud Storage for historical analysis
- 🌐 **Enhanced Web Scraping**: Analyzes actual page content (titles, CTAs, H1 tags) for page-specific optimization suggestions
- 📊 **Event Analysis**: Captures and analyzes website events for comprehensive performance insights
- 🎯 **Structured Recommendations**: Generates exactly 7 AI-powered recommendations (one per question) with facts, actions, categories, priorities, and impact scores

**What our Product Release Manager does:**
- 🗂️ **Jira release notes**: Queries Jira issues by Fix Version and generates customer-facing release notes
- 📰 **Blog draft**: Produces a longer `blog_markdown` draft suitable for publishing
- 📣 **Social drafts**: Produces drafts for X, LinkedIn, Facebook, and Instagram
- ✅ **No missed items**: Ensures every Jira issue is included exactly once under **New features**, **Improvements**, **Bug Fixes**

**What our CTO does:**
- 🔍 **PR review**: Reviews pull request diffs with AI (configurable LLM: OpenAI or Gemini) and posts or updates a single comment on GitHub
- 🔐 **GitHub integration**: Uses a GitHub PAT (repo scope) from env or request; supports marker-based comment updates to avoid spam

**PR review – add to your repo (the repo you want reviewed):**

1. **Add the workflow** to your repo (e.g. the repo where you open PRs):
   - Create `.github/workflows/pr-review.yml` (see [docs/cto-pr-review.md](docs/cto-pr-review.md) or copy from `bigas/.github/workflows/pr-review.yml` in this repo).
2. **In that repo**, go to **Settings → Secrets and variables → Actions**:
   - **Variables**: add `BIGAS_URL` = your Bigas Cloud Run URL (e.g. `https://bigas-xxx.run.app`).
   - **Secrets**: add `BIGAS_API_KEY` = one of your Bigas access keys (same as `BIGAS_ACCESS_KEYS`; use header `X-Bigas-Access-Key` or `Authorization: Bearer`). Optionally add `GH_PAT_FOR_BIGAS` if you don’t store the GitHub PAT in Bigas Secret Manager. To use Gemini for PR review, ensure Bigas has `GEMINI_API_KEY` and `LLM_MODEL` (e.g. `gemini-2.5-pro`) in Secret Manager and in `SECRET_MANAGER_SECRET_NAMES`.

### 🌟 Key Features

#### 🤖 AI-Powered Analysis
- **Senior Marketing Analyst AI**: Get intelligent insights from expert AI, not just raw data
- **Executive Summaries**: Business-focused analysis with key performance trends and business impact
- **Concrete Recommendations**: Specific improvement suggestions with priority rankings, under 80 words each
- **Page-Content-Aware**: Uses actual scraped content (titles, CTAs, H1 tags) for specific optimization suggestions

#### 📊 Comprehensive Analytics
- **Automated Weekly Reports**: Set up once, get comprehensive reports every week or month
- **7 Core Questions**: Traffic sources, engagement, conversions, underperforming pages, and more
- **Event Performance Analysis**: Comprehensive analysis of website events and their impact on conversions
- **Event-Driven Engagement**: Considers events (page_view, scroll, clicks) as positive engagement signals, not just conversions

#### 🌐 Advanced Web Scraping
- **Real Page Content**: Scrapes actual website content for concrete recommendations
- **Element Analysis**: Analyzes titles, CTAs, forms, testimonials, and social proof
- **CTA Identification**: Finds and evaluates call-to-action elements
- **Content Optimization**: Specific content improvement suggestions based on scraped data

#### 🎯 Structured Recommendations
- **7 Focused Recommendations**: One specific recommendation per analytics question
- **Data-Driven Facts**: Each recommendation includes specific metrics from Google Analytics
- **Concrete Actions**: Specific instructions (e.g., "Add 'Contact Us' button in hero section")
- **Priority Ranking**: High/medium/low priority with impact scores
- **No Hallucinations**: All recommendations based on actual data and scraped content

#### 💾 Smart Storage & Integration
- **Google Cloud Storage**: Stores reports with enhanced metadata for historical analysis
- **Discord Integration**: Posts structured reports with summaries and recommendations
- **URL Extraction**: Automatically extracts actual page URLs from GA4 data
- **Cost-Effective**: Minimal storage and API costs (<$0.10/month)

## 🏗️ Architecture

Bigas is built as a **Modular Monolith** with a service-oriented architecture:

```text
+--------------------------+
|         Clients          |
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
| | API Gateway / Router                        | |
| +---------------------------------------------+ |
|   |                      |                      |
|   | (/marketing/*)       | (/product/*)         |
|   v                      v                      |
| +----------------------+ +--------------------+ |
| | Marketing Resource   | | Product Resource   | |
| | (GA4, LLM,          | | (Jira, LLM,        | |
| |  Discord, Storage)   | |  Discord)          | |
| +----------------------+ +--------------------+ |
|                                                 |
+-------------------------------------------------+
```

#### 📣 Ads Analytics (Google Ads, Meta, LinkedIn, Reddit)

Bigas also includes a multi-platform **Paid Ads Analytics Orchestrator** alongside GA4 web analytics. This layer standardizes how ads data is fetched, stored, and summarized across **Google Ads**, **Meta (Facebook/Instagram)**, **LinkedIn Ads**, and **Reddit Ads**.

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
- The **Paid Ads Analytics Orchestrator** endpoints handle date ranges, caching, and coordinating multi-step jobs (discovery → fetch → enrich → summarize).
- **Summaries** are generated via a shared `AD_SUMMARY_PROMPTS` registry, so each platform + report type gets a consistent, opinionated analysis:
  - Portfolio overview, key segments, underperformers, and concrete next steps
- **Output** is posted to Discord for easy consumption by marketing stakeholders.

### Service Layer

The marketing analytics functionality is organized into focused services:

- **`GA4Service`**: Handles Google Analytics 4 API interactions and data extraction
- **LLM-backed analysis**: Marketing and product features use the shared `bigas.llm` abstraction (OpenAI or Gemini; model set via `LLM_MODEL` or per-feature env such as `BIGAS_CTO_PR_REVIEW_MODEL`). See `bigas/llm/README.md`.
- **`OpenAIService`**: Legacy wrapper for marketing analysis; in practice uses the same LLM abstraction
- **`TemplateService`**: Provides template-driven analytics queries including event analysis templates
- **`TrendAnalysisService`**: Orchestrates trend analysis workflows and underperforming page identification
- **`StorageService`**: Manages weekly report storage in Google Cloud Storage with enhanced metadata
- **`WebScrapingService`**: Analyzes actual page content for concrete, page-specific recommendations

The product functionality is organized into focused services:

- **`CreateReleaseNotesService`**: Fetches Jira issues by Fix Version and generates customer-facing release notes + comms pack

### Data Flow

1. **Weekly Reports**: Generated with comprehensive analytics data and stored in Google Cloud Storage
2. **Page Performance Analysis**: Identifies underperforming pages considering both conversions and events
3. **Web Scraping**: Analyzes actual page content (titles, CTAs, H1 tags) for concrete recommendations
4. **AI Marketing Analysis**: Generates executive summary and 7 focused recommendations (one per question) using the configured LLM (OpenAI or Gemini)
5. **Discord Integration**: Posts structured reports with executive summaries and expert recommendations
6. **Enhanced Metadata**: Stores comprehensive timestamps and report structure for better organization
7. **Storage Management**: Automatic cleanup of old reports to manage costs

## 🚀 Quick Start

### Prerequisites

Before deploying, you need:
- **Google Cloud CLI** (`gcloud`) installed and authenticated
- **Google Cloud Project** created
- **Cloud Run and Analytics APIs** enabled
- **Service Account** created for Google Analytics 4 access

See the [Google Cloud documentation](https://cloud.google.com/docs) for detailed setup instructions.

### Environment Configuration

You can either provide all configuration in `.env` or use **Google Secret Manager** so the app loads secrets at startup (minimal `.env`).

**Option A – Full .env (all vars in one file):**

```bash
cp env.example .env
# Edit .env and replace ALL dummy values with real credentials
nano .env
```

**Option B – Secret Manager (recommended for production):** Set `SECRET_MANAGER=true` and `SECRET_MANAGER_SECRET_NAMES` to a comma-separated list of secret names. Create one secret per value in Google Secret Manager (secret name = env var name; payload = plain value). Keep only bootstrap vars in `.env`: `GOOGLE_PROJECT_ID`, `GOOGLE_SERVICE_ACCOUNT_EMAIL`, `SECRET_MANAGER`, `SECRET_MANAGER_SECRET_NAMES`, Docker and access-control vars. See `env.example` (top) and the [Secret Manager](#secret-manager-optional) section below. The Cloud Run service account must have `roles/secretmanager.secretAccessor` on the project or secrets.

⚠️ **IMPORTANT**: Never commit `.env` or any file containing real keys. The `env.example` file only contains placeholder values.

### Environment Variables

**When not using Secret Manager**, configure these in `.env` (see `env.example` for the full list):

```bash
# Required (at least one LLM provider):
GA4_PROPERTY_ID=your_actual_ga4_property_id
OPENAI_API_KEY=your_actual_openai_api_key   # if using OpenAI (gpt-* models)
# For Gemini (gemini-* models) also set:
# GEMINI_API_KEY=your_gemini_api_key
# LLM_MODEL=gemini-2.5-pro

GOOGLE_PROJECT_ID=your_actual_google_project_id
GOOGLE_SERVICE_ACCOUNT_EMAIL=your_actual_service_account_email

# Optional:
DISCORD_WEBHOOK_URL_MARKETING=your_actual_marketing_discord_webhook
DISCORD_WEBHOOK_URL_PRODUCT=your_actual_product_discord_webhook
STORAGE_BUCKET_NAME=your_actual_storage_bucket
TARGET_KEYWORDS=your_actual_keywords

# Google Ads (for run_google_ads_portfolio_report):
GOOGLE_ADS_DEVELOPER_TOKEN=your_google_ads_developer_token
GOOGLE_ADS_LOGIN_CUSTOMER_ID=optional_manager_account_id
GOOGLE_ADS_CUSTOMER_ID=optional_default_customer_id

# Meta Ads (for run_meta_portfolio_report and cross-platform):
META_ACCESS_TOKEN=your_long_lived_system_user_token
META_AD_ACCOUNT_ID=optional_ad_account_id_without_act_prefix
```

When **SECRET_MANAGER=true**, the app loads the vars listed in `SECRET_MANAGER_SECRET_NAMES` from Google Secret Manager at startup (include `GEMINI_API_KEY` and `LLM_MODEL` if you use Gemini). Only bootstrap and deploy-related vars need to stay in `.env`.

### Step-by-Step Setup

#### 1. Set Up Google Cloud Project

1. **Create a Google Cloud Project** (if you don't have one)
2. **Enable required APIs**:
   - Google Analytics Data API
   - Google Cloud Storage API
3. **Create a service account** with the following roles:
   - `roles/analyticsdata.reader` - Read GA4 data
   - `roles/storage.objectAdmin` - Manage storage objects
   - If using Secret Manager: `roles/secretmanager.secretAccessor` (so the app can load secrets at startup)
4. **Download the service account key file** (optional; for local runs)

#### 1b. Secret Manager (optional)

If you use **SECRET_MANAGER=true**, the app loads env vars from Google Secret Manager at startup (one secret per setting; secret name = env var name, payload = plain value).

1. **Enable the Secret Manager API**: `gcloud services enable secretmanager.googleapis.com`
2. **Create secrets** in the project (e.g. via Console or `gcloud secrets create SECRET_NAME` and add versions with your values). Use the same names as in `SECRET_MANAGER_SECRET_NAMES` (e.g. `OPENAI_API_KEY`, `GEMINI_API_KEY`, `LLM_MODEL`, `JIRA_BASE_URL`).
3. **Grant the Cloud Run service account** access: ensure the service account used by Cloud Run (e.g. `analytics@PROJECT_ID.iam.gserviceaccount.com`) has `roles/secretmanager.secretAccessor` on the project or on the secrets.
4. In `.env` set **SECRET_MANAGER=true**, **SECRET_MANAGER_SECRET_NAMES** (comma-separated list), **GOOGLE_PROJECT_ID**; keep **GOOGLE_SERVICE_ACCOUNT_EMAIL** and deploy/access-control vars. See `env.example` (top section).

You can populate Secret Manager from an existing `.env` once with `python scripts/sync_env_to_secret_manager.py` (run from repo root; requires `gcloud` and the secrets listed in the script).

#### 2. Set Up Google Analytics 4 Property Access

**⚠️ CRITICAL**: You must grant your service account access to your GA4 property for bigas to fetch data.

**Step 1: Get Your GA4 Property ID**
1. Go to [Google Analytics](https://analytics.google.com/)
2. Select your GA4 property
3. Go to **Admin** → **Property details**
4. Copy the **Property ID** (format: `123456789`)

**Step 2: Grant Service Account Access**
1. In Google Analytics, go to **Admin** → **Property Access Management**
2. Click **+** to add a new user
3. **Email**: Enter your service account email
4. **Role**: Select **"Marketer"** (minimum required role for full data access)
5. Click **Add**

**Troubleshooting**: If you get a 403 error, wait longer for permissions to propagate or verify the service account email is correct.

#### 3. Set Up Google Cloud Storage

**⚠️ IMPORTANT**: Google Cloud Storage must be activated and a bucket must be created for report storage.

1. **Activate Google Cloud Storage API** (if not already done in step 1)
2. **Create a storage bucket**:
   ```bash
   # Create a bucket (replace with your preferred name)
   gsutil mb gs://your-bucket-name
   
   # Or use the default name that will be created automatically
   gsutil mb gs://bigas-analytics-reports
   ```
3. **Note the bucket name** - you'll need it for the `STORAGE_BUCKET_NAME` environment variable

#### 4. Configure Environment Variables

Copy the example environment file and configure your variables:

```bash
# Copy the example environment file
cp env.example .env

# Edit the file with your actual values
nano .env
```

**Required environment variables** (see `env.example` for details):
- `GA4_PROPERTY_ID` - Your Google Analytics 4 property ID (found in GA4 Admin → Property Settings). Also used in cross-platform analysis when set.
- `OPENAI_API_KEY` - Your OpenAI API key (required if using gpt-* models). For Gemini, set `GEMINI_API_KEY` and `LLM_MODEL` (e.g. `gemini-2.5-pro`) instead or in addition; include both in `SECRET_MANAGER_SECRET_NAMES` if using Secret Manager.
- `DISCORD_WEBHOOK_URL_MARKETING` - Your Discord webhook URL (marketing channel)
- `DISCORD_WEBHOOK_URL_PRODUCT` - Your Discord webhook URL (product channel, for release notes)
- `STORAGE_BUCKET_NAME` - Your Google Cloud Storage bucket (optional, defaults to 'bigas-analytics-reports')
- `TARGET_KEYWORDS` - Colon-separated list of target keywords for SEO analysis (optional, e.g., "sustainable_swag:eco_friendly_clothing:green_promos")
- **Google Ads** (for `run_google_ads_portfolio_report`): `GOOGLE_ADS_DEVELOPER_TOKEN` (required); `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (optional, manager/MCC); `GOOGLE_ADS_CUSTOMER_ID` (optional default customer). Auth uses Application Default Credentials; the Cloud Run service account must have access to the Google Ads account.
- **Meta Ads** (for `run_meta_portfolio_report` and cross-platform): `META_ACCESS_TOKEN` (required, long-lived system user token with `ads_management` and `ads_read`); `META_AD_ACCOUNT_ID` (optional default ad account ID, numeric without `act_` prefix).
- **LinkedIn** (optional, for cross-platform): LinkedIn ad account currency is read from the API when building the cross-platform payload.

**LLM configuration (OpenAI and/or Gemini):** Set `OPENAI_API_KEY` for OpenAI (gpt-* models) or `GEMINI_API_KEY` and `LLM_MODEL` (e.g. `gemini-2.5-pro`) for Gemini. Per-feature overrides: `BIGAS_CTO_PR_REVIEW_MODEL`, `BIGAS_PROGRESS_UPDATES_MODEL`, `BIGAS_RELEASE_NOTES_MODEL`, `BIGAS_MARKETING_LLM_MODEL`. See `env.example` and `bigas/llm/README.md`.

**⚠️ IMPORTANT**: You must add your actual API keys and values to the `.env` file. The `env.example` file only contains placeholder values.

**Debugging GA4 attribution**: If cross-platform reports show "GA4 attribution data is missing due to technical issues", run the debug script locally (from repo root; requires credentials with GA4 access, e.g. service account key in `GOOGLE_APPLICATION_CREDENTIALS`): `python scripts/ga4_attribution_debug.py`

#### 5. Deploy to Google Cloud Run

```bash
# Clone the repository
git clone https://github.com/your-username/bigas.git
cd bigas

# ⚠️ IMPORTANT: Configure environment variables first!
# Make sure you've set up your .env file with all required variables

# Deploy using the provided script
./deploy.sh
```

#### 6. Get Your First Weekly Report

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report
```

That's it! You'll get a comprehensive AI-powered analysis posted to your Discord channel.

#### 7. Automate Weekly Reports (Recommended)

For automated weekly reports, set up Google Cloud Scheduler:

1. **Go to Google Cloud Console**
   - Navigate to [Cloud Scheduler](https://console.cloud.google.com/cloudscheduler)
   - Select your project

2. **Create a new job**
   - Click "Create Job"
   - **Name**: `weekly-analytics-report`
   - **Region**: Choose your preferred region
   - **Description**: `Automated weekly analytics reports for Bigas`

3. **Configure the schedule**
   - **Frequency**: `0 9 * * 1` (Every Monday at 9 AM)
   - **Timezone**: Choose your timezone (e.g., America/New_York)

4. **Configure the target**
   - **Target type**: HTTP
   - **URL**: `https://your-deployment-url.com/mcp/tools/weekly_analytics_report`
   - **HTTP method**: POST

5. **Save the job**

This will automatically post weekly analytics reports to your Discord channel every Monday at 9 AM.

You can also schedule **ad portfolio reports** with Cloud Scheduler (e.g. `run_google_ads_portfolio_report`, `run_meta_portfolio_report`, or `run_cross_platform_marketing_analysis`). Use the same HTTP target type and the corresponding MCP tool URL; for long-running cross-platform runs, consider the async endpoint and a job that polls for completion, or set the Cloud Run request timeout to 900s.

**Schedule Examples:**
- `0 9 * * 1` - Every Monday at 9 AM
- `0 9 * * 1,4` - Every Monday and Thursday at 9 AM  
- `0 9 1 * *` - First day of every month at 9 AM
- `0 */6 * * *` - Every 6 hours

## 🌐 HTTP API Usage

The core functionality is available via HTTP API, making it easy to integrate with web applications or trigger reports remotely.

### API Endpoints

After deploying to Google Cloud Run, your API will be available at:
```
https://your-service-name-<hash>.a.run.app
```

You can find your service URL by running:
```bash
gcloud run services describe mcp-marketing --region=your-region --format='value(status.url)'
```

## 🤝 MCP Integration (AI Agents)

Bigas exposes its capabilities as a **Model Context Protocol (MCP)** HTTP server, so AI agents and IDEs can auto-discover and call its tools.

### Discovery URLs

- **Server card (recommended entry point)**  
  `https://mcp-marketing-919623369853.europe-north1.run.app/.well-known/mcp.json`

  The server card tells MCP clients:

  - The base URL for the server
  - Where to fetch the tool manifest
  - Where to fetch the OpenAPI schema
  - How to authenticate (API key header)

- **Manifest (tool catalog)**  
  `https://mcp-marketing-919623369853.europe-north1.run.app/mcp/manifest`

  Returns a compact list of tools (name, description, HTTP path, method, and light parameter schema).  
  This is optimized for LLMs to browse and choose tools.

- **OpenAPI schema (full contract)**  
  `https://mcp-marketing-919623369853.europe-north1.run.app/openapi.json`

  Returns an OpenAPI 3.0 document describing all MCP HTTP endpoints in detail
  (request/response JSON schemas, error responses, and security).

Most MCP-aware clients only need the **server card URL**; they will follow the links inside.

### Authentication for MCP tools

If `BIGAS_ACCESS_MODE=restricted`, all tool requests (except health checks and the manifest/server card) must include an API key:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/create_release_notes \
  -H "X-Bigas-Access-Key: <YOUR_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"fix_version": "1.1.0"}'
```

#### Ads Analytics Endpoints (LinkedIn + Reddit + Google Ads + Meta)

These endpoints expose paid ads analytics over HTTP:

- **LinkedIn Ads**
  - `POST /mcp/tools/run_linkedin_portfolio_report` – One-command LinkedIn portfolio report (creative discovery → demographics → portfolio summary → Discord).
  - `POST /mcp/tools/run_linkedin_portfolio_report_async` – Async version for MCP clients with strict per-call time limits. Returns `job_id` immediately.
  - `POST /mcp/tools/fetch_linkedin_ad_analytics_report` – Fetch raw LinkedIn adAnalytics for an account and cache in GCS.
  - `POST /mcp/tools/fetch_linkedin_creative_demographics_portfolio` – Per-creative, per-pivot demographics (job title, function, country) with strong caching.
  - `POST /mcp/tools/summarize_linkedin_ad_analytics` – Summarize an enriched LinkedIn ad analytics report from GCS and post to Discord.
  - `POST /mcp/tools/summarize_linkedin_creative_portfolio` – Summarize a demographic portfolio built from multiple enriched blobs.

- **Reddit Ads**
  - `POST /mcp/tools/run_reddit_portfolio_report` – One-command Reddit portfolio report (performance + audience breakdowns → Discord).
  - `POST /mcp/tools/run_reddit_portfolio_report_async` – Async version for MCP clients with strict per-call time limits. Returns `job_id` immediately.
  - `POST /mcp/tools/fetch_reddit_ad_analytics_report` – Fetch Reddit Ads performance report, normalize, and store raw + enriched payloads in GCS.
  - `POST /mcp/tools/fetch_reddit_audience_report` – Fetch Reddit audience reports (interests, communities, geography) and optionally store in GCS.
  - `POST /mcp/tools/summarize_reddit_ad_analytics` – Summarize an enriched Reddit ad analytics report and post to Discord.

- **Google Ads**
  - `POST /mcp/tools/run_google_ads_portfolio_report` – One-command Google Ads portfolio. Supports `report_level: campaign | ad | audience_breakdown` and optional `breakdowns` (`device`, `network`, `day_of_week`) for more Meta-like alignment.
  - `POST /mcp/tools/run_google_ads_portfolio_report_async` – Async version for MCP clients with strict per-call time limits. Returns `job_id` immediately.

- **Meta (Facebook/Instagram) Ads**
  - `POST /mcp/tools/run_meta_portfolio_report` – One-command Meta Ads portfolio. Supports `report_level: campaign | ad | audience_breakdown`, includes spend totals/per-entity rows, reach/frequency, and optional targeting snapshot (`include_targeting: true`). Requires `META_ACCESS_TOKEN` and optionally `META_AD_ACCOUNT_ID`.
  - `POST /mcp/tools/run_meta_portfolio_report_async` – Async version for MCP clients with strict per-call time limits. Returns `job_id` immediately.

- **Shared async job tools**
  - `POST /mcp/tools/get_job_status` – Poll status for async jobs (`queued`, `running`, `succeeded`, `failed`).
  - `POST /mcp/tools/get_job_result` – Fetch the final result payload for a completed async job.

- **Cross-Platform (LinkedIn + Reddit + Google Ads + Meta)**
  - `POST /mcp/tools/run_cross_platform_marketing_analysis` – Run fresh LinkedIn, Reddit, Google Ads, and Meta portfolio reports in parallel (default last 30 days), then LLM comparison: summary, key data points, and budget recommendation. Posts progress to Discord as each platform completes, then one cross-platform summary. **Request timeout is 15 minutes** (900s); for longer runs or to avoid timeouts, use the async variant below.
  - `POST /mcp/tools/run_cross_platform_marketing_analysis_async` – Async cross-platform report. Returns `job_id` immediately; poll with `get_job_status` and `get_job_result`. Use when the sync endpoint would time out (e.g. LinkedIn + Reddit + Google Ads + Meta + LLM can take 10+ min).

### API Examples

**Note**: Replace `https://your-deployment-url.com` with your actual Cloud Run service URL in the examples below.

#### MCP timeout guidance (important for external LLM clients)

Some MCP clients enforce short call limits (for example ~30 seconds). For long-running tools, use async flow:

1. Call the async tool (`*_async`) and get `job_id`.
2. Poll `POST /mcp/tools/get_job_status` until `status` is `succeeded` or `failed`.
3. Fetch final output via `POST /mcp/tools/get_job_result`.

Portfolio tools support `timeout_seconds` (default `300`, min `10`, max `900`) and sync tools also support `async: true`.

#### Generate Weekly Report
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/weekly_analytics_report \
  -H "Content-Type: application/json" \
  -d '{}'
```

#### Get Stored Reports
```bash
curl https://your-deployment-url.com/mcp/tools/get_stored_reports
```

#### Get Latest Report
```bash
curl https://your-deployment-url.com/mcp/tools/get_latest_report
```

#### Analyze Underperforming Pages
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_underperforming_pages \
  -H "Content-Type: application/json" \
  -d '{"max_pages": 3}'
```

#### Cleanup Old Reports
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/cleanup_old_reports \
  -H "Content-Type: application/json" \
  -d '{"keep_days": 30}'
```

#### LinkedIn Ads API Health Check (Paid Ads - LinkedIn)

Lists accessible ad accounts (requires LinkedIn app approval + scopes `r_ads`).

```bash
curl https://your-deployment-url.com/mcp/tools/linkedin_ads_health_check
```

#### Fetch LinkedIn Ad Analytics Report (Paid Ads - LinkedIn)

Stores the raw response in GCS under `raw_ads/linkedin/{end_date}/ad_analytics_{accountId}_{pivot}_{hashPrefix}.json`.

Caching behavior:
- If the exact same request (same params → same hash) already exists in GCS, the API returns the cached report and does **not** refetch.
- Use `force_refresh: true` to bypass cache and refetch anyway.

Date range behavior:
- You can either pass explicit `start_date`/`end_date` **or** use a relative range via `relative_range`.
- If both are provided, `start_date`/`end_date` take precedence.
- Supported `relative_range` values:
  - `LAST_DAY`      → yesterday only
  - `LAST_7_DAYS`   → last 7 full days ending yesterday
  - `LAST_30_DAYS`  → last 30 full days ending yesterday

Tip: if a campaign was paused recently and 7 days looks empty, request a broader period explicitly with `start_date`/`end_date` or `relative_range: LAST_30_DAYS`.

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/fetch_linkedin_ad_analytics_report \
  -H "Content-Type: application/json" \
  -d '{
    "account_urn": "urn:li:sponsoredAccount:123456",
    "relative_range": "LAST_7_DAYS",
    "time_granularity": "DAILY",
    "store_raw": true,
    "force_refresh": false,
    "include_entity_names": true
  }'
```

##### How LinkedIn ad analytics caching works

When you call `fetch_linkedin_ad_analytics_report`, the backend:

1. Normalizes IDs into **LinkedIn URNs** (e.g. `123456` → `urn:li:sponsoredCampaign:123456`) using a shared helper.
2. Builds a generic **`AdsAnalyticsRequest`** object that captures:
   - `platform` (e.g. `linkedin`)
   - `endpoint` (e.g. `ad_analytics`)
   - `finder` (`analytics` or `statistics`)
   - account/campaign/creative URNs
   - date range, time granularity, pivots, fields
   - flags like `include_entity_names`
3. Serializes this request into a deterministic JSON signature and hashes it with **SHA‑256**.
4. Uses the hash to generate stable storage paths:
   - Raw: `raw_ads/{platform}/{end_date}/{endpoint}_{accountId}_{pivot}_{hashPrefix}.json`
   - Enriched: `raw_ads/{platform}/{end_date}/{endpoint}_{accountId}_{pivot}_{hashPrefix}.enriched.json`
5. Checks if the raw blob already exists:
   - If **yes** and `force_refresh` is `false`, it returns a cached response (`from_cache: true`) without hitting LinkedIn.
   - If **no** (or `force_refresh: true`), it calls LinkedIn, stores the response in GCS, and returns `from_cache: false`.

This pattern is implemented with:

- `AdsAnalyticsRequest` – the cross‑platform request schema used for hashing and logging.
- `build_ads_cache_keys(...)` – computes the hash + blob names from an `AdsAnalyticsRequest`.
- `StorageService.store_raw_ads_report_at_blob(...)` – writes the `{metadata, payload}` wrapper to GCS.

The same pattern can be reused for other platforms (e.g. Meta, Reddit) by:

1. Constructing an `AdsAnalyticsRequest(platform="meta" | "reddit", ...)`.
2. Calling `build_ads_cache_keys(...)` to get `request_hash`, `blob_name`, and `enriched_blob_name`.
3. Storing raw responses under `raw_ads/{platform}/...` via `StorageService`.

##### OpenAI analysis + Discord for LinkedIn ads

Once you have an **enriched** LinkedIn report, you can summarize it with:

- `/mcp/tools/summarize_linkedin_ad_analytics` – account‑level or pivot‑level performance (used by the one-command portfolio report).
- `/mcp/tools/summarize_linkedin_creative_portfolio` – per‑creative demographic portfolio summaries (separate flow).

The **one-command** `/mcp/tools/run_linkedin_portfolio_report` runs discovery, fetches CREATIVE-level ad analytics, and calls `summarize_linkedin_ad_analytics` so a single request produces a creative performance summary in Discord.

Both summarization endpoints:

1. Load enriched JSON from GCS (which includes `summary`, `context`, and `elements`).
2. Build a compact `analytics_payload` with only the fields needed for the LLM.
3. Look up a prompt configuration in `AD_SUMMARY_PROMPTS[(platform, report_type)]`, which defines:
   - a **system prompt** (role + expectations for the AI analyst), and
   - a **user template** that injects the JSON payload.
4. Call the configured LLM (OpenAI or Gemini via `bigas.llm`) with these prompts. Default model is from `LLM_MODEL` or the marketing override (e.g. `BIGAS_MARKETING_LLM_MODEL`).
5. Post the resulting markdown to Discord using `post_long_to_discord`, which:
   - splits long content into 2k‑character‑safe chunks, and
   - uses a short, configurable HTTP timeout via `DISCORD_HTTP_TIMEOUT`.

To add another paid ads provider (e.g. Google Ads or Meta) to this flow you:

1. Implement a platform‑specific service (`GoogleAdsService`, `MetaAdsService`, etc.) that fetches raw analytics.
2. Build an `AdsAnalyticsRequest` for that platform and pass it to `build_ads_cache_keys(...)`.
3. Store raw + enriched responses under `raw_ads/{platform}/...` using `StorageService`.
4. Add a new entry in `AD_SUMMARY_PROMPTS[(platform, report_type)]` with a tailored system prompt.
5. Create a summarization endpoint that:
   - reads the enriched blob,
   - builds a compact `analytics_payload`, and
   - uses the shared prompt config + Discord helpers, mirroring the LinkedIn endpoints.

**Demographic breakdowns (per-ad)**

To break down performance by professional demographics, use the statistics finder via `pivots` (up to 3).
Example: per-creative performance split by job title:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/fetch_linkedin_ad_analytics_report \
  -H "Content-Type: application/json" \
  -d '{
    "account_urn": "urn:li:sponsoredAccount:123456",
    "relative_range": "LAST_7_DAYS",
    "time_granularity": "DAILY",
    "pivots": ["CREATIVE","MEMBER_JOB_TITLE"],
    "fields": ["impressions","clicks","costInLocalCurrency","landingPageClicks"],
    "store_raw": true,
    "force_refresh": false
  }'
```

#### Exchange LinkedIn OAuth Code for Refresh Token (Paid Ads - LinkedIn)

This avoids putting your LinkedIn Client Secret into a terminal command history.
It uses `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET` from the server environment.

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/linkedin_exchange_code \
  -H "Content-Type: application/json" \
  -d '{
    "code": "PASTE_CODE_FROM_/linkedin/callback"
  }'
```

If LinkedIn is rate-limiting refresh-token minting, you can temporarily use a manual access token:
- Call `linkedin_exchange_code` with `"include_access_token": true`
- Set `LINKEDIN_ACCESS_TOKEN` in your environment (and redeploy)

#### Summarize LinkedIn Ad Analytics Report → OpenAI → Discord

Once you have generated an **enriched** LinkedIn report (with `include_entity_names: true`), you can ask an AI marketing specialist to summarize performance and post the results to your Discord marketing channel.

Requires:
- `OPENAI_API_KEY` or `GEMINI_API_KEY` (and `LLM_MODEL` if using Gemini)
- `DISCORD_WEBHOOK_URL_MARKETING` (or `DISCORD_WEBHOOK_URL`) – your Discord webhook URL for the marketing channel.

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/summarize_linkedin_ad_analytics \
  -H "Content-Type: application/json" \
  -d '{
    "enriched_storage_path": "raw_ads/linkedin/2026-02-09/ad_analytics_516183054_MEMBER_JOB_TITLE_abc123456789.enriched.json",
    "llm_model": "gpt-4"
  }'
```

Behavior:
- If the enriched report has **no elements** for the selected period, the tool posts a short **“no LinkedIn data for this period”** message to Discord so it’s clear nothing ran.
- Otherwise it:
  - Loads a compact view of the enriched report from GCS.
  - Sends it to the configured LLM (OpenAI or Gemini) with a “senior B2B performance marketer” prompt.
  - Posts a structured summary (high-level summary, key insights, recommendations, caveats) to your Discord marketing channel.

#### One-command LinkedIn portfolio report

A single endpoint runs discovery → fetch CREATIVE-level ad analytics → summarize with the same ad-analytics summarizer → post to Discord. No need to chain three calls or pass `enriched_storage_path` manually.

Requires `account_urn` (or `LINKEDIN_AD_ACCOUNT_URN` in env), and optionally `discovery_relative_range`, `min_impressions`, `store_raw`, `force_refresh`, `include_entity_names`, `llm_model`, `sample_limit`.

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_linkedin_portfolio_report \
  -H "Content-Type: application/json" \
  -d '{
    "account_urn": "urn:li:sponsoredAccount:516183054",
    "discovery_relative_range": "LAST_7_DAYS",
    "min_impressions": 1
  }'
```

Response includes `creatives_discovered`, `had_data`, `discord_posted`, `enriched_storage_path`, `used_model`. Uses the same report structure and summarization logic as `fetch_linkedin_ad_analytics_report` + `summarize_linkedin_ad_analytics` (CREATIVE pivot).

This endpoint is designed to be scheduled from **Google Cloud Scheduler**: a single job runs discovery, fetch, and summarize and posts the result to Discord.

#### One-command LinkedIn portfolio report (async for MCP clients)

Use this when your MCP client has a short request timeout.

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_linkedin_portfolio_report_async \
  -H "Content-Type: application/json" \
  -d '{
    "account_urn": "urn:li:sponsoredAccount:516183054",
    "discovery_relative_range": "LAST_30_DAYS",
    "timeout_seconds": 300
  }'
```

Then poll:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/get_job_status \
  -H "Content-Type: application/json" \
  -d '{"job_id":"job_abc123"}'
```

And fetch final result:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/get_job_result \
  -H "Content-Type: application/json" \
  -d '{"job_id":"job_abc123"}'
```

#### Cross-Platform Marketing Budget Analysis (LinkedIn + Reddit + Google Ads + Meta)

Runs LinkedIn, Reddit, Google Ads, and Meta portfolio reports in parallel (default last 30 days), then sends combined data to an LLM marketing analyst and posts a single Discord report with **summary**, **key data points**, and **recommendation** on where to spend more budget. Each platform’s spend and CPC are stated in that platform’s currency (e.g. SEK, EUR). When `GA4_PROPERTY_ID` is set, the run also fetches GA4 Paid Social attribution and Key Events so the analyst can connect ad spend to which channels drove users and conversions.

Requires `LINKEDIN_AD_ACCOUNT_URN`, `REDDIT_AD_ACCOUNT_ID`, and a Discord webhook. At least one LLM provider must be configured (`OPENAI_API_KEY` or `GEMINI_API_KEY` + `LLM_MODEL`). Optional: `GOOGLE_ADS_CUSTOMER_ID` (or request `customer_id`) to include Google Ads; `META_AD_ACCOUNT_ID` (or request `meta_account_id`) to include Meta; if a platform ID is missing, that platform is skipped. Other optional params: `relative_range` (default `LAST_30_DAYS`), `account_urn`, `account_id`, `llm_model`, `sample_limit`.

**Sync** (15 min request timeout; use async if you hit timeouts):

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_cross_platform_marketing_analysis \
  -H "Content-Type: application/json" \
  -d '{}'
```

Or with an explicit range:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_cross_platform_marketing_analysis \
  -H "Content-Type: application/json" \
  -d '{"relative_range": "LAST_30_DAYS"}'
```

**Async** (returns immediately; poll for result—recommended for MCP clients or when sync times out):

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_cross_platform_marketing_analysis_async \
  -H "Content-Type: application/json" \
  -d '{"relative_range": "LAST_30_DAYS", "timeout_seconds": 900}'
# Then poll: POST /mcp/tools/get_job_status and POST /mcp/tools/get_job_result with job_id
```

Flow: LinkedIn, Reddit, Google Ads, and Meta portfolio reports run in parallel (and GA4 Paid Social attribution is fetched when `GA4_PROPERTY_ID` is set) → progress posted to Discord as each completes → combined LLM analysis with spend and attribution → one cross-platform budget analysis post to Discord.

#### Reddit portfolio report (async for MCP clients)

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_reddit_portfolio_report_async \
  -H "Content-Type: application/json" \
  -d '{
    "relative_range": "LAST_30_DAYS",
    "include_audience": true,
    "timeout_seconds": 300
  }'
```

Then poll:

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/get_job_status \
  -H "Content-Type: application/json" \
  -d '{"job_id":"job_abc123"}'
```

#### Google Ads portfolio report

Requires **Google Ads API** access (developer token) and Application Default Credentials (ADC). Add to your `.env`:

- `GOOGLE_ADS_DEVELOPER_TOKEN` – required (from Google Ads API Center).
- `GOOGLE_ADS_LOGIN_CUSTOMER_ID` – optional (manager/MCC account ID).
- `GOOGLE_ADS_CUSTOMER_ID` – optional default customer ID (can also be sent in the request body).

The Cloud Run service account (or local gcloud user) must be granted access to the Google Ads account in the Google Ads UI.

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_google_ads_portfolio_report \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "1234567890",
    "start_date": "2026-02-01",
    "end_date": "2026-02-15",
    "store_raw": true,
    "store_enriched": true,
    "post_to_discord": true
  }'
```

If `customer_id` is omitted, `GOOGLE_ADS_CUSTOMER_ID` from `.env` is used. Default date range is last 30 days.

Async variant (recommended for MCP clients with short call time limits):

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_google_ads_portfolio_report_async \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "1234567890",
    "start_date": "2026-02-01",
    "end_date": "2026-02-15",
    "post_to_discord": true,
    "timeout_seconds": 300
  }'
```

Optional alignment parameters:
- `report_level`: `campaign` (default), `ad`, or `audience_breakdown`
- `breakdowns`: optional list for `audience_breakdown` (supported: `device`, `network`, `day_of_week`)

#### Meta Ads portfolio report

Requires a long-lived system user access token with `ads_management` and `ads_read`. Add to your `.env`: `META_ACCESS_TOKEN`, and optionally `META_AD_ACCOUNT_ID` (numeric, no `act_` prefix). Currency (e.g. SEK) is read from the ad account and included in the report.

Supported report levels:
- `campaign` (default): total + per-campaign spend, clicks, conversions, reach, frequency
- `ad`: individual ad/creative performance (`ad_id`, `ad_name`, plus campaign/adset context)
- `audience_breakdown`: campaign performance broken down by dimensions like `age`, `gender`, `country` (`breakdowns` list)

Optional:
- `include_targeting: true` to include ad set targeting configuration snapshot

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_meta_portfolio_report \
  -H "Content-Type: application/json" \
  -d '{
    "report_level": "ad",
    "start_date": "2026-02-01",
    "end_date": "2026-02-15",
    "include_targeting": true,
    "post_to_discord": true
  }'
```

If `account_id` is omitted, `META_AD_ACCOUNT_ID` from `.env` is used.

Async variant (recommended for MCP clients with short call time limits):

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/run_meta_portfolio_report_async \
  -H "Content-Type: application/json" \
  -d '{
    "report_level": "ad",
    "start_date": "2026-02-01",
    "end_date": "2026-02-15",
    "include_targeting": true,
    "post_to_discord": true,
    "timeout_seconds": 300
  }'
```

#### Ask Analytics Question
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/ask_analytics_question \
  -H "Content-Type: application/json" \
  -d '{"question": "Which country had the most active users last week?"}'
```

#### Analyze Trends
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/analyze_trends \
  -H "Content-Type: application/json" \
  -d '{"metric": "active_users", "date_range": "last_30_days"}'
```

#### Custom Reports
```bash
curl -X POST https://your-deployment-url.com/mcp/tools/fetch_custom_report \
  -H "Content-Type: application/json" \
  -d '{
    "dimensions": ["country", "device_category"],
    "metrics": ["active_users", "sessions"],
    "date_ranges": [{"start_date": "2024-01-01", "end_date": "2024-01-31"}]
  }'
```

#### Create Release Notes (Jira Fix Version → multi-channel notes)

Requires Jira and an LLM provider (see `env.example`): set either `OPENAI_API_KEY` or `GEMINI_API_KEY` (and `LLM_MODEL` for Gemini). Also:
- `JIRA_BASE_URL`
- `JIRA_EMAIL`
- `JIRA_API_TOKEN`
- `JIRA_PROJECT_KEY`

Optional request body parameter: `jql_extra` (e.g. `"AND statusCategory = Done"`) to narrow the Jira query.

**Output format (no missed items)**

The release notes are generated so **every Jira issue is included exactly once** under:
- **New features**
- **Improvements**
- **Bug Fixes**

The response includes:
- `sections.features|improvements|bug_fixes`: customer-friendly bullets (no Jira keys like `SCRUM-123`)
- `customer_markdown`: customer-ready markdown with H2 headings: **New features**, **Improvements**, **Bug Fixes**
- `blog_markdown`: longer blog-ready markdown draft
- `social`: draft updates for:
  - `x` (<= 280 chars)
  - `linkedin`
  - `facebook`
  - `instagram`

**HTTP**

```bash
curl -X POST https://your-deployment-url.com/mcp/tools/create_release_notes \
  -H "Content-Type: application/json" \
  -d '{"fix_version":"1.1.0"}'
```

**CLI** (from repository root):

```bash
./create_release_notes 1.1.0
```

### API Documentation

Once deployed, access your API documentation at:
- **OpenAPI spec**: `https://your-deployment-url.com/openapi.json`
- **Manifest**: `https://your-deployment-url.com/mcp/manifest`

### Local Development

For local testing, you can run the API server locally:
```bash
# Option 1: Direct Python execution
python run_core.py

# Option 2: Using Flask directly
python app.py

# Option 3: Using gunicorn
gunicorn -w 4 -b 0.0.0.0:8080 app:create_app()
```

Then use `http://localhost:8080` in place of your Cloud Run URL.

## 📊 Storage & Reports

### Storage Architecture

#### Google Cloud Storage Integration
- **Automatic Storage**: Weekly reports are automatically stored in Google Cloud Storage
- **Cost-Effective**: Only stores one report per week, overwriting previous reports
- **Organized Structure**: Reports are stored with date-based organization
- **Metadata Tracking**: Each report includes metadata for easy retrieval and analysis

#### Storage Structure
```
bigas-analytics-reports/
└── weekly_reports/
    ├── 2024-01-15/
    │   └── report.json
    ├── 2024-01-22/
    │   └── report.json
    └── 2024-01-29/
        └── report.json
```

### Enhanced Report Structure

Bigas generates comprehensive, structured reports with multiple components:

#### **Executive Summary**
- **Business-Focused Overview**: Clear summary of key performance trends and business impact
- **Critical Insights**: Top performing areas, biggest opportunities, and key bottlenecks
- **Immediate Actions**: Prioritized action items for the current week
- **Structured Format**: Bullet points and clear language for easy reading

#### **Detailed Q&A Analysis**
- **Comprehensive Coverage**: Answers to 7 key marketing questions
- **Data-Driven Insights**: Specific metrics and trends analysis
- **Traffic Source Analysis**: Organic search, social, referral, and direct traffic insights
- **Page Performance**: Conversion rates, engagement metrics, and underperforming pages
- **Event Analysis**: Website events, trends, and optimization opportunities

#### **Structured Recommendations**
- **7 Focused Recommendations**: One specific recommendation per analytics question
- **Data-Driven Facts**: Each includes specific metrics from Google Analytics
- **Page-Specific Actions**: References exact page URLs and elements
- **Concrete Steps**: Specific instructions for what to change
- **Priority Ranking**: High, medium, and low priority with impact scores

### Structured Recommendations Format

The weekly report generates **one focused recommendation per question** (7 questions = up to 7 recommendations):

```json
{
  "recommendations": [
    {
      "fact": "Direct traffic is 62.5% while organic search is only 25% of total sessions",
      "recommendation": "Create 5 SEO-optimized blog posts targeting key product keywords",
      "category": "seo",
      "priority": "high",
      "impact_score": 8
    },
    {
      "fact": "Homepage has 0 CTA buttons and 48 sessions but 0% conversion rate",
      "recommendation": "Add 'Contact Us' button in hero section and contact form",
      "category": "conversion",
      "priority": "high",
      "impact_score": 9
    }
  ]
}
```

**Recommendation Features:**
- ✅ **One per question**: Each of the 7 analytics questions generates one focused recommendation
- ✅ **Specific numbers**: Every fact includes actual metrics (e.g., "62.5%", "48 sessions", "0 conversions")
- ✅ **Concrete actions**: Recommendations are specific (e.g., "Create 5 SEO-optimized blog posts", not "Improve SEO")
- ✅ **Page-content-aware**: Underperforming pages get recommendations based on actual scraped content

**Page Scraping for Underperforming Pages:**
When the weekly report detects underperforming pages (high traffic, low conversions):
1. **Automatically scrapes** the most visited underperforming page
2. **Analyzes content**: CTAs, forms, testimonials, social proof, H1 tags
3. **Generates specific recommendation**: Based on what's actually missing (e.g., "Homepage has 0 CTA buttons")

**Fact Requirements:**
- ✅ **GOOD**: "Direct traffic is 62.5% while organic search is only 25% of total sessions"
- ✅ **GOOD**: "Homepage has 48 sessions but 0 conversions (0% conversion rate)"
- ✅ **GOOD**: "Homepage has 0 CTA buttons and no testimonials found"
- ❌ **BAD**: "Traffic has been declining" (no specific numbers)
- ❌ **BAD**: "Homepage needs improvement" (too generic)

**Event-Aware Analysis:**
- Events (page_view, scroll, user_engagement, click) are considered **positive engagement signals**
- A page with "zero conversions" but high events may actually have good engagement
- The AI highlights event numbers to show the complete engagement picture

### Automated Workflow Setup

Set up automated jobs for complete weekly analytics workflow:

**Weekly Report Job (Monday 9 AM)**
```bash
# Cloud Scheduler Configuration
Name: weekly-analytics-report
Frequency: 0 9 * * 1
URL: https://your-deployment-url.com/mcp/tools/weekly_analytics_report
Method: POST
```

**Page Analysis Job (Tuesday 10 AM)**
```bash
# Cloud Scheduler Configuration
Name: analyze-underperforming-pages
Frequency: 0 10 * * 2
URL: https://your-deployment-url.com/mcp/tools/analyze_underperforming_pages
Method: POST
```

**Cleanup Job (Monthly)**
```bash
# Cloud Scheduler Configuration
Name: cleanup-old-reports
Frequency: 0 2 1 * *
URL: https://your-deployment-url.com/mcp/tools/cleanup_old_reports
Method: POST
Body: {"keep_days": 30}
```

### Cost Management

#### Storage Costs
- **Google Cloud Storage**: ~$0.02 per GB per month
- **Typical Report Size**: ~15KB per report
- **Monthly Cost**: ~$0.0003 for 30 reports

#### API Costs
- **LLM (OpenAI or Gemini)**: Cost depends on model and usage (e.g. OpenAI GPT-4 ~$0.03 per analysis; Gemini has different pricing—see `env.example` for `LLM_MODEL`).
- **Monthly Cost**: Varies by report frequency and model choice.

#### Total Estimated Cost
- **Storage**: <$0.01/month
- **Analysis**: ~$0.06/month
- **Total**: <$0.10/month

## 🔍 Advanced Features

### Web Scraping & Page Analysis

Bigas includes sophisticated web scraping capabilities for concrete, page-specific recommendations:

#### **Page Content Analysis**
- **Real-Time Scraping**: Analyzes actual website content during report generation
- **Element Extraction**: Captures titles, H1 tags, CTA buttons, and main content
- **Content Optimization**: Provides specific suggestions based on actual page elements
- **Performance Correlation**: Links page content to conversion performance

#### **Scraping Features**
- **Automatic URL Construction**: Builds full URLs from GA4 page paths
- **Content Preview**: Extracts first 500 characters of main content for analysis
- **CTA Identification**: Finds and analyzes call-to-action elements
- **Error Handling**: Graceful fallback if scraping fails

#### **Content Elements Analyzed**
The web scraper extracts and analyzes:
- **Page Title & Meta Description**: SEO and messaging effectiveness
- **Headings Structure**: Content hierarchy and messaging flow
- **Call-to-Action Buttons**: Number, placement, and effectiveness
- **Contact Forms**: Form fields, complexity, and conversion barriers
- **Images & Media**: Visual content and alt text optimization
- **Contact Information**: Phone numbers, emails, and accessibility
- **Social Proof Elements**: Testimonials, reviews, trust signals
- **Page Structure**: Navigation, footer, responsiveness

#### **Use Cases**
- **Button Optimization**: Identify weak CTAs and suggest improvements
- **Content Gaps**: Find missing elements that could improve conversions
- **Page Structure**: Analyze H1 tags, titles, and content hierarchy
- **A/B Test Ideas**: Generate specific test hypotheses based on page content

### URL Extraction & Domain Detection

#### **How It Works**

**1. Weekly Report Generation**
When the weekly report runs, it stores:
- **AI-generated answers** (human-readable insights)
- **Raw GA4 data** (structured data with URLs)

**2. URL Extraction Process**
The system automatically:
1. **Identifies** the underperforming pages question
2. **Extracts** page paths from the raw GA4 data
3. **Converts** paths to full URLs using actual domain from GA4
4. **Calculates** conversion rates and metrics
5. **Flags** underperforming pages

**3. Analysis Enhancement**
The analysis endpoint receives:
- **Specific page URLs** instead of just page names
- **Detailed metrics** for each page
- **Conversion rates** for context

#### **Example Output**

**Before (Page Names Only)**
```
"Are there underperforming pages with high traffic but low conversions?"
Answer: "Yes, the Home page and About Us page have high traffic but no conversions."
```

**After (With URLs and Metrics)**
```json
{
  "underperforming_pages": [
    {
      "question": "Are there underperforming pages with high traffic but low conversions?",
      "answer": "Yes, the Home page and About Us page have high traffic but no conversions."
    }
  ],
  "page_urls": [
    {
      "page_path": "/",
      "hostname": "bigas.com",
      "page_url": "https://bigas.com/",
      "sessions": 39,
      "conversions": 0,
      "conversion_rate": 0.0,
      "is_underperforming": true
    }
  ]
}
```

### Event Analysis

Bigas includes comprehensive website event analysis for better performance insights:

#### **Event Tracking & Analysis**
- **Website Events**: Captures clicks, form submissions, video plays, and custom interactions
- **Performance Trends**: Identifies increasing, decreasing, or stable event patterns
- **Conversion Correlation**: Analyzes relationship between events and conversions
- **Optimization Opportunities**: Flags events that need attention or improvement

#### **Event Metrics**
- **Event Count**: Total number of times each event occurred
- **Unique Users**: Number of distinct users who triggered each event
- **Trend Analysis**: Whether events are increasing, decreasing, or stable
- **Performance Classification**: High, medium, or low priority for optimization

#### **Use Cases**
- **Button Performance**: Track CTA button clicks and identify underperforming elements
- **Form Analytics**: Monitor form submissions and identify drop-off points
- **Content Engagement**: Measure video plays, downloads, and content interactions
- **User Journey**: Understand how users interact with your website

### Underperforming Pages Analysis

The system can analyze underperforming pages from weekly reports and provide expert-level recommendations for improvement.

#### **Features**
- **Expert Digital Marketing Analysis**: Uses an expert Digital Marketing Strategist specializing in CRO, SEO, and UX
- **Comprehensive Page Analysis**: Analyzes actual page content, SEO elements, UX components, and performance indicators
- **Actionable Recommendations**: Provides specific, implementable improvements across three key areas:
  - **Conversion Rate Optimization (CRO)**: Critical issues, CTA optimization, trust building, value proposition, user journey
  - **Search Engine Optimization (SEO)**: On-page SEO, keyword strategy, technical SEO, content quality, internal linking
  - **User Experience (UX)**: Visual hierarchy, mobile experience, page speed, accessibility, user intent alignment
- **Priority Action Plan**: Categorized by High/Medium/Low priority with expected impact and timeline
- **Discord Integration**: Posts detailed analysis to Discord with comprehensive page metrics
- **Error Handling**: Clear guidance when page analysis fails due to technical issues
- **Report Date Tracking**: Shows when the analyzed report was generated

#### **Analysis Includes**
- **Page Content**: Title, meta description, headings, CTAs, forms, text content
- **SEO Elements**: Title/meta length, heading structure, internal/external links, canonical URLs, Open Graph, schema markup
- **UX Elements**: Hero sections, testimonials, pricing, FAQ, newsletter signup, live chat
- **Performance**: Image optimization, link structure, inline styles, external scripts
- **Page Structure**: Navigation, footer, responsiveness, paragraphs, lists, breadcrumbs, search functionality

#### **Target Keywords Feature**
When you configure target keywords in the `TARGET_KEYWORDS` environment variable, the analysis includes:
- **Keyword Presence Analysis**: Checks if keywords appear in title, meta description, and content
- **Keyword Position Analysis**: Identifies where keywords appear in title and meta description
- **Specific SEO Recommendations**: Tailored suggestions for optimizing for your target keywords
- **Content Gap Analysis**: Identifies missing content opportunities for your keywords
- **Competitive Insights**: How well your page targets specific search terms

**Configuration**: Add to your `.env` file:
```bash
TARGET_KEYWORDS=sustainable_swag:eco_friendly_clothing:green_promos
```

#### **Usage**
```bash
# Analyze underperforming pages from the latest report
curl -X POST https://your-server.com/mcp/tools/analyze_underperforming_pages \
  -H "Content-Type: application/json" \
  -d '{"max_pages": 3}'

# Analyze pages from a specific report date
curl -X POST https://your-server.com/mcp/tools/analyze_underperforming_pages \
  -H "Content-Type: application/json" \
  -d '{"report_date": "2024-01-15", "max_pages": 5}'
```

#### **Response**
```json
{
  "status": "success",
  "report_date": "2024-01-15",
  "pages_analyzed": 3,
  "max_pages_limit": 3,
  "discord_posted": true,
  "discord_messages_sent": 4
}
```

#### **Important Notes**
- **Data-Driven Recommendations**: Analysis is only provided when page content can be successfully scraped
- **No Generic Advice**: If page scraping fails, the system provides error guidance instead of generic recommendations
- **Quality Over Quantity**: Ensures all recommendations are based on actual page content for maximum relevance

## 🧪 Testing

### Test Storage Functionality
```bash
python tests/test_storage.py
```

### Test Domain Extraction
```bash
python tests/test_domain_extraction.py
```

### Test Health Check
```bash
python tests/health_check.py
```

## 🔒 Security

### Critical Security Requirements

#### 1. Environment Variables
**NEVER commit sensitive data to version control!**

All sensitive information must be stored as environment variables:

- `GA4_PROPERTY_ID` - Google Analytics 4 Property ID
- `OPENAI_API_KEY` - OpenAI API key (if using gpt-* models)
- `GEMINI_API_KEY` - Gemini API key (if using gemini-* models); set `LLM_MODEL` (e.g. `gemini-2.5-pro`) for default
- `DISCORD_WEBHOOK_URL` - Discord webhook URL (optional)
- `GOOGLE_PROJECT_ID` - Google Cloud Project ID
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` - Service account email
- `STORAGE_BUCKET_NAME` - Google Cloud Storage bucket (optional)

#### 2. File Security
- ✅ `.env` files are in `.gitignore` (do not commit `.env` or any file containing real keys)
- ✅ No hardcoded secrets in scripts
- ✅ Service account JSON files are excluded
- ✅ API keys are never logged

#### 3. Access Control
- ✅ Service accounts have minimal required permissions
- ✅ API keys have appropriate scopes
- ✅ HTTPS for all external communications
- ✅ Regular key rotation

### Security Features

#### Input Validation ✅
- All API endpoints validate input parameters
- Date ranges are validated for logical consistency (start before end, not in future, max 2 years)
- Metric/dimension combinations are verified for GA4 compatibility
- Request data size limits (max 10KB per request)
- URL format validation for web scraping
- Content size limits (max 5MB for page analysis)

#### Error Handling ✅
- Sensitive information is never exposed in error messages
- API keys and webhook URLs are automatically redacted
- Graceful degradation when services are unavailable
- Proper HTTP status codes for different error types (400, 404, 429, 500)

#### Rate Limiting ✅
- Simple in-memory rate limiting (100 requests per hour per endpoint)
- Automatic cleanup of old rate limit entries
- HTTP 429 status code for rate limit exceeded
- Per-endpoint rate limiting to prevent abuse

#### Request Validation ✅
- JSON request validation
- Required field checking
- Input sanitization
- Size and length limits
- URL security checks

### Security Checklist

#### Before Committing Code
- [ ] No API keys in code
- [ ] No hardcoded credentials
- [ ] No sensitive URLs in comments
- [ ] `.env` file is not tracked
- [ ] Service account files are excluded

#### Before Deployment
- [ ] Environment variables are set
- [ ] Service account has correct permissions
- [ ] API keys are valid and active
- [ ] HTTPS is used for webhooks
- [ ] Logging excludes sensitive data

#### Regular Maintenance
- [ ] Rotate API keys quarterly
- [ ] Review service account permissions
- [ ] Monitor API usage and costs
- [ ] Update dependencies for security patches
- [ ] Audit access logs

### Incident Response

#### If API Keys are Compromised
1. **Immediately rotate the compromised key**
2. **Check for unauthorized usage**
3. **Review access logs**
4. **Update all environment variables**
5. **Notify relevant stakeholders**

#### If Service Account is Compromised
1. **Disable the service account**
2. **Create a new service account**
3. **Update permissions and environment variables**
4. **Review all access logs**
5. **Audit all resources accessed**

### Security Tools

#### Recommended Tools
- **GitGuardian** - Detect secrets in code
- **Snyk** - Dependency vulnerability scanning
- **Google Cloud Security Command Center** - Cloud security monitoring

#### Code Scanning
```bash
# Check for secrets in code
grep -r "sk-" . --exclude-dir=venv
grep -r "AIza" . --exclude-dir=venv
grep -r "discord.com/api/webhooks" . --exclude-dir=venv
```

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

### Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-username/bigas.git
   cd bigas
   ```

2. **Set up virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

3. **Configure environment**
   ```bash
   cp env.example .env
   # Edit .env with your actual values
   ```

4. **Run tests**
   ```bash
   python tests/test_storage.py
   python tests/test_domain_extraction.py
   ```

## 📞 Support

For support:
1. Check the [Issues](https://github.com/your-username/bigas/issues) page
2. Create a new issue with detailed information
3. Include your environment setup and error messages

### Security Issues

For security issues:
1. **Do not create public issues** for security problems
2. **Contact the maintainer directly** with security concerns
3. **Include detailed information** about the security issue
4. **Provide steps to reproduce** if applicable

## 📄 License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0) - see the [LICENSE](LICENSE) file for details.

## 🔄 Changelog

### v1.3.0
- ✅ **LLM abstraction**: Provider-agnostic LLM layer (`bigas.llm`) for OpenAI and Gemini. Model resolution: request/body → per-feature env (e.g. `BIGAS_CTO_PR_REVIEW_MODEL`) → `LLM_MODEL` → default `gemini-2.5-pro`. See `bigas/llm/README.md`.
- ✅ **Gemini support**: Use `GEMINI_API_KEY` and `LLM_MODEL` (e.g. `gemini-2.5-pro`) for PR review, release notes, marketing, and progress updates.
- ✅ **Cross-platform async**: `run_cross_platform_marketing_analysis_async` for long-running cross-platform reports; poll `get_job_status` / `get_job_result`.
- ✅ **Longer timeouts**: Gunicorn and Cloud Run request timeout set to 900s (15 min) for sync cross-platform and LinkedIn portfolio runs.

### v1.2.0
- ✅ Added web scraping functionality for page content analysis
- ✅ Enhanced underperforming pages analysis with actual page content
- ✅ Improved Discord messaging with one message per page
- ✅ Added specific, actionable suggestions based on real page structure
- ✅ Implemented page content analysis (CTAs, forms, headings, etc.)

### v1.1.0
- ✅ Added Google Cloud Storage integration
- ✅ Implemented URL extraction from GA4 data
- ✅ Added underperforming pages analysis
- ✅ Enhanced AI-powered improvement suggestions
- ✅ Added automatic domain detection from GA4

### v1.0.0
- ✅ Initial release with weekly analytics reports
- ✅ Discord integration
- ✅ Google Analytics 4 integration
- ✅ OpenAI-powered insights

## 🤖 AI Prompts Reference

This section documents all the AI prompts used in Bigas for generating analytics insights and recommendations.

### 📊 Initial Analytics Questions

The weekly report asks these 8 core questions to generate comprehensive insights:

```python
questions = [
  "What are the key trends in our website performance over the last 30 days, including user growth, session patterns, and any significant changes?",
  "What are the primary traffic sources (e.g., organic search, direct, referral, paid search, social, email) contributing to total sessions, and what is their respective share?",
  "What is the average session duration and pages per session across all users?",
  "Which pages are the most visited, and how do they contribute to conversions (e.g., product pages, category pages, blog posts)?",
  "Which pages or sections (e.g., blog, product pages, landing pages) drive the most engagement (e.g., time on page, low bounce rate)?",
  "Are there underperforming pages with high traffic but low conversions?",
  "How do blog posts or content pages contribute to conversions (e.g., assisted conversions, last-click conversions)?",
  "Where are new visitors coming from?"
]
```

### 🔄 Question Processing Flow

**IMPORTANT**: Weekly reports use **template-based queries** for consistent, comparable data across all report runs. This ensures week-over-week reliability and eliminates AI variability in metric selection.

#### **Architecture: Template-Based System**

Weekly reports use **predefined query templates** that map each question to specific GA4 metrics and dimensions:

```python
# Question-to-Template Mapping
question_to_template = {
    "What are the key trends...": "trend_analysis",
    "What are the primary traffic sources...": "traffic_sources",
    "What is the average session duration...": "session_quality",
    "Which pages are the most visited...": "top_pages_conversions",
    "Which pages drive the most engagement...": "engagement_pages",
    "Are there underperforming pages...": "underperforming_pages",
    "How do blog posts contribute...": "blog_conversion",
    "Where are new visitors coming from?": "new_visitor_sources"
}

# Predefined Templates (Always Use the Same Metrics)
QUESTION_TEMPLATES = {
    "traffic_sources": {
        "dimensions": ["sessionDefaultChannelGroup"],
        "metrics": ["sessions"],
        "postprocess": "calculate_session_share"
    },
    "session_quality": {
        "dimensions": [],
        "metrics": ["averageSessionDuration", "screenPageViewsPerSession"]
    },
    "top_pages_conversions": {
        "dimensions": ["pagePath", "hostName", "eventName"],
        "metrics": ["sessions", "conversions"],
        "order_by": [{"field": "sessions", "direction": "DESCENDING"}]
    },
    "engagement_pages": {
        "dimensions": ["pagePath", "hostName"],
        "metrics": ["averageSessionDuration", "bounceRate"],
        "order_by": [{"field": "averageSessionDuration", "direction": "DESCENDING"}]
    },
    "underperforming_pages": {
        "dimensions": ["pagePath", "hostName"],
        "metrics": ["sessions", "conversions"],
        "postprocess": "find_high_traffic_low_conversion"
    },
    "blog_conversion": {
        "dimensions": ["pagePath", "hostName", "sessionDefaultChannelGroup"],
        "metrics": ["conversions", "sessions"],
        "filters": [{"field": "pagePath", "operator": "contains", "value": "blog"}]
    },
    "new_visitor_sources": {
        "dimensions": ["firstUserSource", "firstUserMedium", "firstUserDefaultChannelGroup"],
        "metrics": ["newUsers", "sessions"],
        "order_by": [{"field": "newUsers", "direction": "DESCENDING"}]
    }
}
```

#### **Benefits of Template-Based Approach**

✅ **100% Consistent Queries**: Same metrics and dimensions every week  
✅ **Week-over-Week Comparability**: Guaranteed data consistency for trend analysis  
✅ **No AI Variability**: Query structure is fixed, not AI-generated  
✅ **Reliable Scheduling**: Perfect for automated weekly/monthly reports  
✅ **Predictable Costs**: No variation in query complexity  

#### **Three-Step Process for Each Question**

##### Step 1: Template Lookup (Question → Fixed Query Structure)

Each question is mapped to a predefined template with fixed metrics:

```python
# Example: Traffic sources question
question = "What are the primary traffic sources..."
template_key = question_to_template[question]  # → "traffic_sources"

# Retrieve fixed template
template = QUESTION_TEMPLATES["traffic_sources"]
# {
#   "dimensions": ["sessionDefaultChannelGroup"],
#   "metrics": ["sessions"],
#   "postprocess": "calculate_session_share"
# }
```

**Result**: Always uses the **exact same GA4 query parameters** - no AI involved in query generation!

##### Step 2: Fetch Data from Google Analytics 4

The predefined query structure is sent to GA4 API to retrieve real analytics data:

```python
# Execute template query (always consistent)
raw_data = template_service.run_template_query(template_key, property_id)

# Example response:
# {
#   "rows": [
#     {"dimensions": ["Organic Search"], "metrics": [15234]},
#     {"dimensions": ["Direct"], "metrics": [9876]},
#     {"dimensions": ["Referral"], "metrics": [2145]}
#   ]
# }
```

##### Step 3: AI Format Response (GA4 Data → Natural Language Answer)

Finally, AI converts the **consistently structured data** into a natural language answer:

```python
analytics_answer_prompt = """You are an expert at explaining Google Analytics data in a clear and concise way.
Given the raw analytics data and the original question, provide a natural language response that:
1. Directly answers the question
2. Highlights key insights and trends
3. Provides relevant context and comparisons
4. Uses simple, non-technical language

Format numbers appropriately (e.g., "1.2M" instead of "1,200,000").
If the data shows no results or empty rows, explain what this means and suggest alternative approaches."""

# Model: gpt-4
# Max tokens: 2000
# Input: JSON containing {question: "...", analytics_data: {...}}
# Output: Natural language answer with insights and trends
```

**Example Input to GPT-4**:
```json
{
  "question": "What are the primary traffic sources contributing to total sessions?",
  "analytics_data": {
    "dimension_headers": ["Channel Group"],
    "metric_headers": ["Sessions", "Total Users"],
    "rows": [
      ["Organic Search", "15234", "8432"],
      ["Direct", "9876", "6543"],
      ["Referral", "2145", "1234"]
    ]
  }
}
```

**Example Generated Answer**:
> "Your website's primary traffic sources are Organic Search (15.2K sessions, 55%), Direct traffic (9.9K sessions, 36%), and Referral (2.1K sessions, 8%). Organic search is your strongest channel, indicating good SEO performance. Consider increasing efforts in referral marketing to diversify your traffic sources."

#### **What's Deterministic vs. Non-Deterministic?**

| Component | Behavior | Consistency |
|-----------|----------|-------------|
| **GA4 Query** | ✅ Template-based (fixed metrics/dimensions) | 100% consistent |
| **GA4 Data** | ✅ Real data from Google Analytics | Varies by actual performance |
| **AI Formatting** | ⚠️ Natural language generation | ~95% consistent (wording may vary slightly) |

**Key Point**: The **data you get is always identical** (same metrics, same dimensions), but the **AI's wording** may vary slightly between runs. For example:
- Week 1: "Organic Search is your top source with 15.2K sessions..."
- Week 2: "Your primary traffic driver is Organic Search, contributing 15.2K sessions..."

Both answers contain the **exact same data** (15.2K sessions from Organic Search), just phrased differently.

#### **Why Not Use AI Query Parser?**

The `OpenAIService.parse_query()` method exists for **ad-hoc custom questions** but is **intentionally not used in weekly reports** because:

❌ **Non-Deterministic Queries**: AI might choose different metrics each time  
❌ **Incomparable Results**: Week-over-week trends become unreliable  
❌ **Unpredictable Costs**: Query complexity varies  
❌ **Quality Control Issues**: Hard to validate if metrics are appropriate  

**Use Cases**:
- ✅ **Weekly Reports**: Template-based (current implementation)
- ⚠️ **Custom Ad-Hoc Questions**: AI parser (not currently exposed in weekly reports)

### 🎯 Recommendation Generation Prompts

#### 1. Page-Content-Aware Recommendation Prompt

Used when analyzing underperforming pages with actual scraped content:

```python
page_analysis_prompt = f"""
You are an expert Digital Marketing Strategist. Analyze this underperforming page and provide ONE specific recommendation.

Page: {page_path} ({page_name})
Full URL: {underperforming_page_url}
Analytics: {max_sessions} sessions, 0 conversions (0% conversion rate)

Page Content Analysis:
- Title: {page_content_analysis.get('title', 'No title')}
- Meta Description: {page_content_analysis.get('meta_description', 'None')}
- H1 Tags: {page_content_analysis.get('seo_elements', {}).get('h1_count', 0)}
- Forms: {len(page_content_analysis.get('forms', []))}
- Contact Info: {page_content_analysis.get('has_contact_info', False)}
- Testimonials: {page_content_analysis.get('ux_elements', {}).get('has_testimonials', False)}
- Social Proof: {page_content_analysis.get('has_social_proof', False)}

Generate ONE recommendation in this EXACT JSON format:
{{
  "fact": "Page path/name + what is wrong (include numbers: sessions, CTAs, forms, etc.)",
  "recommendation": "Concrete action to fix it (specific and implementable)",
  "category": "conversion",
  "priority": "high"
}}

CRITICAL: The fact MUST start with the page identifier (e.g., "Homepage" or the page path)

Return ONLY the JSON, no explanation.
"""
```

#### 2. General Recommendation Prompt

Used for questions that don't involve specific page analysis:

```python
recommendation_prompt = f"""
You are a marketing analyst. Based on this analytics question and answer, generate ONE specific, actionable recommendation.

Question: {q}

Answer: {answer[:500]}

Raw Data Summary: {str(raw_data)[:300] if raw_data else 'No raw data'}

Generate ONE recommendation in this EXACT JSON format:
{{
  "fact": "Specific finding from the data with actual numbers",
  "recommendation": "Concrete, implementable action (max 80 chars)",
  "category": "traffic|content|conversion|seo|engagement",
  "priority": "high|medium|low"
}}

CRITICAL REQUIREMENTS:
- Include SPECIFIC NUMBERS from the data in the fact (percentages, counts, durations)
- If discussing specific pages, ALWAYS include the page name/path at the start of the fact
- Make recommendation ACTIONABLE and CONCRETE (not generic)
- Keep recommendation under 80 characters

Return ONLY the JSON, no explanation.
"""
```

### 🔍 Comprehensive Page Analysis Prompt

Used for detailed underperforming page analysis with full content scraping:

```python
page_analysis_prompt = f"""
You are an expert Digital Marketing Strategist specializing in Conversion Rate Optimization (CRO), SEO, and User Experience (UX). Your goal is to analyze the provided webpage data and generate actionable recommendations to significantly increase both conversion rates and organic page visits.

Page Details:
- URL: {page.get('page_url', 'Unknown')}
- Sessions: {page.get('sessions', 0)}
- Conversions: {page.get('conversions', 0)}
- Conversion Rate: {page.get('conversion_rate', 0):.1%}
{f"- Target Keywords: {', '.join(target_keywords)}" if target_keywords else ""}

[Full page content analysis data...]

Based on this comprehensive analysis of the actual page content, please provide:

## 🎯 CONVERSION RATE OPTIMIZATION (CRO)
1. **Critical Issues**: What are the top 3-5 conversion killers on this page?
2. **CTA Optimization**: Specific improvements for calls-to-action (placement, copy, design)
3. **Trust & Credibility**: How to build trust and reduce friction
4. **Value Proposition**: How to make the value clearer and more compelling
5. **User Journey**: Optimize the path from visitor to conversion

## 🔍 SEARCH ENGINE OPTIMIZATION (SEO)
1. **On-Page SEO**: Title, meta description, headings, and content optimization
2. **Keyword Strategy**: Target keywords and content gaps
3. **Technical SEO**: Page speed, mobile-friendliness, and technical issues
4. **Content Quality**: How to improve content depth and relevance
5. **Internal Linking**: Opportunities for better site structure

## 👥 USER EXPERIENCE (UX)
1. **Visual Hierarchy**: How to improve content flow and readability
2. **Mobile Experience**: Mobile-specific optimizations
3. **Page Speed**: Performance improvements
4. **Accessibility**: Making the page more accessible
5. **User Intent**: Aligning content with user expectations

## 📊 PRIORITY ACTION PLAN
- **High Priority** (Quick wins with high impact)
- **Medium Priority** (Moderate effort, good impact)
- **Low Priority** (Long-term improvements)

## 🎯 EXPECTED IMPACT
- Specific metrics improvements to expect
- Timeline for implementation
- Resource requirements

Focus on practical, implementable recommendations based on the actual page content.
"""
```

### 🎯 Prompt Design Principles

All prompts follow these key principles:

1. **Specificity**: Always include actual numbers and metrics from the data
2. **Actionability**: Recommendations must be concrete and implementable
3. **Page Context**: When discussing pages, always include the page name/path
4. **Structured Output**: Consistent JSON format for recommendations
5. **Character Limits**: Recommendations kept under 80 characters for conciseness
6. **Category Classification**: Clear categorization (traffic, content, conversion, seo, engagement)
7. **Priority Ranking**: High/medium/low priority classification
8. **Real Content Analysis**: Based on actual scraped page content, not assumptions

### 🔧 Customization

These prompts can be customized by:

- **Target Keywords**: Add specific keywords for SEO analysis
- **Industry Context**: Modify prompts for specific industries
- **Company Size**: Adjust recommendations for solo founders vs. teams
- **Timeline**: Modify implementation timelines based on resources
- **Metrics Focus**: Emphasize different KPIs based on business goals

---

<div align="center">
  <strong>Built with ❤️ for solo founders who need actionable marketing insights</strong>
</div>
