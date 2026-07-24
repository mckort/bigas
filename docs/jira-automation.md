# Jira status automation (BIG-1 Phase 1)

Automate AI work when humans move Jira issues into AI columns.

## Phase 1 (shipped)

**Research and describe (AI)** → enrich description (keep human Brief) → **Description approval (manual)** → Discord **bigas-pm** (`DISCORD_WEBHOOK_URL_PRODUCT`).

Endpoint: `POST /mcp/tools/jira_status_automation`

## Env

| Variable | Purpose |
|---|---|
| `JIRA_AUTOMATION_WEBHOOK_SECRET` | Shared secret (header `X-Bigas-Webhook-Secret` or `Authorization: Bearer …`) |
| `BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS` | Default `VFA` |
| `BIGAS_JIRA_PROJECT_REPO_MAP` | Optional override `VFA:mckort/vcfieldassistant,WAYW:mckort/roadpal,BIG:mckort/bigas` |
| `BIGAS_JIRA_STATUS_DESCRIPTION_APPROVAL` | Default `Description approval (manual)` |
| `BIGAS_JIRA_AI_DAILY_QUOTA` | Default `20` (global, UTC day, per instance) |
| `BIGAS_JIRA_RESEARCH_MODEL` | Optional model override |
| `GITHUB_TOKEN` | Repo README/tree/code search for research |
| Existing Jira + `DISCORD_WEBHOOK_URL_PRODUCT` | Write-back + PM notifications |

## Jira Automation rule (VFA)

1. Add board status/column **Description approval (manual)** if missing.
2. Rule: **When** issue transitioned **to** `Research and describe (AI)`.
3. **Then** Send web request:
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
  "idempotency_key": "{{issue.key}}-{{issue.status.name}}-{{now}}"
}
```

Adjust smart-value names to match your Automation version if needed. Prefer a stable idempotency key (e.g. changelog id) when available.

## Manual test

```bash
curl -sS -X POST "$BIGAS_URL/mcp/tools/jira_status_automation" \
  -H "Content-Type: application/json" \
  -H "X-Bigas-Webhook-Secret: $JIRA_AUTOMATION_WEBHOOK_SECRET" \
  -d '{"issue_key":"VFA-1","to_status":"Research and describe (AI)","sync":true}'
```

Without `sync`, response is `202` + `job_id`; poll `POST /mcp/tools/jira_status_automation_job` with `{"job_id":"..."}`.

## Description contract

```markdown
## Brief
(human text — preserved)

## AI Research (Bigas)
(AI overwrites this section)

## AI Plan (Bigas)
(phase 2 — preserved if present)
```

## Later phases

- Phase 2: Design and plan (AI) → Design approval + bigas-cto
- Phase 3: In progress (AI) → Cursor branch/PR + bigas-cto
- Phase 4: ready-to-merge → Final approval
