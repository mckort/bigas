# Test: Cross-Platform Marketing Analysis & Progress Updates

Run these after deploying (e.g. `./deploy.sh` from repo root with your `.env` set).

Replace `https://YOUR-SERVICE-URL` with your Cloud Run service URL (e.g. from `gcloud run services describe mcp-marketing --region=europe-north1 --format='value(status.url)'`).

**Long timeout for cross-platform:** The cross-platform run can take 10–30+ minutes (LinkedIn + Reddit + AI). Increase Cloud Run’s request timeout so the request isn’t cut off:

```bash
gcloud run services update mcp-marketing --timeout=3600 --region=europe-north1
```

(3600 = 1 hour. Use 900 for 15 min if you prefer.)

---

## 1. Cross-Platform Marketing Analysis

Runs LinkedIn + Reddit portfolio reports (default last 30 days), then posts a single Discord report with summary, key data points, and budget recommendation.

**Requires:** `LINKEDIN_AD_ACCOUNT_URN`, `REDDIT_AD_ACCOUNT_ID`, `OPENAI_API_KEY`, Discord webhook. Default range: last 30 days.

```bash
# Default (last 30 days)
curl -X POST https://YOUR-SERVICE-URL/mcp/tools/run_cross_platform_marketing_analysis \
  -H "Content-Type: application/json" \
  -d '{}'

# Optional: last 7 days
curl -X POST https://YOUR-SERVICE-URL/mcp/tools/run_cross_platform_marketing_analysis \
  -H "Content-Type: application/json" \
  -d '{"relative_range": "LAST_7_DAYS"}'
```

**Success:** HTTP 200, JSON with `"status": "success"`, `"had_data": true/false`, `"discord_posted": true`. Check your Discord channel for three posts: LinkedIn report, Reddit report, then the cross-platform budget analysis.

---

## 2. Progress Updates

Generates a team progress update from Jira issues moved to Done in the last N days (AI coach message, optional Discord post). Specify the period with `days` (default: last 7 days).

**Requires:** Jira env vars (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`), `OPENAI_API_KEY`. Optional: `DISCORD_WEBHOOK_URL_PRODUCT` to post to Discord.

```bash
# Default: last 7 days, post to Discord if webhook set
curl -X POST https://YOUR-SERVICE-URL/mcp/tools/progress_updates \
  -H "Content-Type: application/json" \
  -d '{}'

# Custom: last 14 days, do not post to Discord
curl -X POST https://YOUR-SERVICE-URL/mcp/tools/progress_updates \
  -H "Content-Type: application/json" \
  -d '{"days": 14, "post_to_discord": false}'

```

**Success:** HTTP 200, JSON with `"ok": true`, `"message": "..."` (AI coach text), `"posted_to_discord": true/false`.

---

## Quick health check

```bash
curl -s https://YOUR-SERVICE-URL/ | jq .
# Expect: {"status":"healthy","service":"bigas-core"}
```

## Get service URL (after deploy)

```bash
gcloud run services describe mcp-marketing --region=europe-north1 --format='value(status.url)'
```

---

## Cloud Scheduler: weekly progress report (every Friday, last 7 days)

Runs `progress_updates` every Friday and posts the coach message to Discord for **last week’s** work (7 days).

**Request body** (use this in Cloud Scheduler HTTP target):

```json
{"days":7,"post_to_discord":true}
```

**Create the job with gcloud:**

Cloud Scheduler does not support `europe-north1`; use a supported location such as `europe-west1`. The job only triggers an HTTP request to your Cloud Run URL.

```bash
# Scheduler location must be a supported region (e.g. europe-west1, not europe-north1)
gcloud scheduler jobs create http progress-report-weekly \
  --location=europe-west1 \
  --schedule="0 9 * * 5" \
  --uri="https://mcp-marketing-919623369853.europe-north1.run.app/mcp/tools/progress_updates" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"days":7,"post_to_discord":true}' \
  --time-zone="Europe/Stockholm"
```

- `0 9 * * 5` = 09:00 every Friday (cron: minute hour day-of-month month day-of-week; 5 = Friday).
- Change `--time-zone` to your timezone (e.g. `Europe/Helsinki`, `UTC`).
- Cloud Run is public (`--allow-unauthenticated`), so no OIDC token is required. If you later enforce IAM, add `--oidc-service-account-email=YOUR_SA@PROJECT_ID.iam.gserviceaccount.com`.
