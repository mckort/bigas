# Jira status automation (BIG-1)

Automate AI work when humans move Jira issues into AI columns.

Endpoint: `POST /mcp/tools/jira_status_automation`

## Phases shipped

### Phase 1 — Research and describe (AI)
Enrich description (keep human Brief) → **Description approval (manual)** → Discord **bigas-pm** (`DISCORD_WEBHOOK_URL_PRODUCT`).

### Phase 2 — Design and plan (AI)
Write **AI Plan (Bigas)** from Brief + Research + repo context → **Design approval (manual)** → Discord **bigas-cto** (`DISCORD_WEBHOOK_URL_CTO`).

## Env

| Variable | Purpose |
|---|---|
| `JIRA_AUTOMATION_WEBHOOK_SECRET` | Shared secret (header `X-Bigas-Webhook-Secret` or `Authorization: Bearer …`) |
| `BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS` | Default `VFA` |
| `BIGAS_JIRA_PROJECT_REPO_MAP` | Optional override `VFA:mckort/vcfieldassistant,WAYW:mckort/roadpal,BIG:mckort/bigas` |
| `BIGAS_JIRA_STATUS_DESCRIPTION_APPROVAL` | Default `Description approval (manual)` |
| `BIGAS_JIRA_STATUS_DESIGN_APPROVAL` | Default `Design approval (manual)` |
| `BIGAS_JIRA_AI_DAILY_QUOTA` | Default `20` (global, UTC day, **per instance**) |
| `BIGAS_JIRA_RESEARCH_MODEL` | Optional research model override |
| `BIGAS_JIRA_DESIGN_MODEL` | Optional design model override |
| `BIGAS_JIRA_ALLOW_BODY_WEBHOOK_SECRET` | Set `1` to allow `webhook_secret` in JSON body (local curl only) |
| `GITHUB_TOKEN` | Repo README/tree/code search for research & design |
| `DISCORD_WEBHOOK_URL_PRODUCT` | PM notifications (research) |
| `DISCORD_WEBHOOK_URL_CTO` | CTO notifications (design) |

## Jira Automation rules (VFA)

Create **one rule per AI status** (same URL/headers/body; different trigger “to” status).

1. Ensure board columns exist: **Description approval (manual)**, **Design approval (manual)**.
2. Rule A: transitioned **to** `Research and describe (AI)`.
3. Rule B: transitioned **to** `Design and plan (AI)`.
4. **Then** Send web request:
   - URL: `https://<CLOUD_RUN>/mcp/tools/jira_status_automation`
   - Method: POST
   - Headers:
     - `Content-Type: application/json`
     - `X-Bigas-Webhook-Secret: <JIRA_AUTOMATION_WEBHOOK_SECRET>`
     - `X-Bigas-Access-Key: <BIGAS_ACCESS_KEYS>` (required when `BIGAS_ACCESS_MODE=restricted`)
   - Body:

```json
{
  "issue_key": "{{issue.key}}",
  "to_status": "{{issue.status.name}}",
  "from_status": "{{fieldChange.fromString}}",
  "idempotency_key": "{{issue.key}}:{{issue.status.name}}:{{fieldChange.fromString}}",
  "sync": true
}
```

Use a **stable** idempotency key (same value on Automation retries). Do **not** include `{{now}}` or other changing tokens — that defeats duplicate protection. Prefer a changelog / fieldChange id when your Automation version exposes one.

### Sync vs async

- **`sync: true` (recommended):** handler runs in the request; reliable on Cloud Run. Ensure the Automation web-request timeout is high enough for LLM + GitHub (often 30–120s).
- **`sync: false`:** returns `202` + `job_id` immediately. Background work is **process-local and best-effort** on Cloud Run unless you set CPU always allocated and `min-instances >= 1`. Poll with `POST /mcp/tools/jira_status_automation_job` (same webhook secret header required).

Quota and idempotency caches are also **per instance** (fine for a single-instance trial).

## Manual test

```bash
# Research
curl -sS -X POST "$BIGAS_URL/mcp/tools/jira_status_automation" \
  -H "Content-Type: application/json" \
  -H "X-Bigas-Webhook-Secret: $JIRA_AUTOMATION_WEBHOOK_SECRET" \
  -H "X-Bigas-Access-Key: $BIGAS_ACCESS_KEYS" \
  -d '{"issue_key":"VFA-1","to_status":"Research and describe (AI)","sync":true}'

# Design
curl -sS -X POST "$BIGAS_URL/mcp/tools/jira_status_automation" \
  -H "Content-Type: application/json" \
  -H "X-Bigas-Webhook-Secret: $JIRA_AUTOMATION_WEBHOOK_SECRET" \
  -H "X-Bigas-Access-Key: $BIGAS_ACCESS_KEYS" \
  -d '{"issue_key":"VFA-1","to_status":"Design and plan (AI)","sync":true}'
```

## Description contract

```markdown
## Brief
(human text — preserved)

## AI Research (Bigas)
(Research handler overwrites)

## AI Plan (Bigas)
(Design handler overwrites)
```

## Later phases

- Phase 3: In progress (AI) → Cursor branch/PR + bigas-cto
- Phase 4: ready-to-merge → Final approval
