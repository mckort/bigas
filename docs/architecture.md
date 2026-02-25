# Bigas architecture (for contributors)

This document preserves architecture, service design, and API implementation details for contributors. The main [README](../README.md) is kept short for users; use this doc when working on the codebase.

## High-level architecture

Bigas is built as a **modular monolith** with a service-oriented architecture:

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

### CTO

- PR review flow: GitHub PR diff → LLM (OpenAI or Gemini) → single comment posted/updated on the PR via GitHub API (marker-based updates to avoid spam). See `docs/cto-pr-review.md`.

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
7. **Cleanup**: Old reports can be removed via `cleanup_old_reports` to manage cost.

## Secret Manager (optional)

When `SECRET_MANAGER=true`, the app loads env vars listed in `SECRET_MANAGER_SECRET_NAMES` from Google Secret Manager at startup (one secret per name; payload = plain value). Bootstrap vars (e.g. `GOOGLE_PROJECT_ID`, `GOOGLE_SERVICE_ACCOUNT_EMAIL`, `SECRET_MANAGER`, `SECRET_MANAGER_SECRET_NAMES`) stay in `.env`. The Cloud Run service account needs `roles/secretmanager.secretAccessor`. You can sync from `.env` once with `python scripts/sync_env_to_secret_manager.py`.
