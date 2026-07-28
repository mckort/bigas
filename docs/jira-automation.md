# Jira status automation (BIG-1)

Automate AI work when humans move Jira issues into AI columns.

Endpoint: `POST /mcp/tools/jira_status_automation`

## Phases shipped

### Phase 1 — Research and describe (AI)
Enrich description (keep human Brief) → **Description approval (manual)** → Discord **bigas-pm**.

### Phase 2 — Design and plan (AI)
Write **AI Plan (Bigas)** → **Design approval (manual)** → Discord **bigas-cto**.

### Phase 3 — In Progress (AI)
Launch Cursor cloud agent on the mapped GitHub repo (`autoCreatePR=true`) → comment agent URL on the issue → leave in **In Progress (AI)** → Discord **bigas-cto**.

### Phase 4 — Ready to merge → Final approval
When `autofix_followup` reports **ready to merge**, Bigas finds the Jira key on the PR (`VFA-14:` title / `Jira: VFA-14` body) and moves the issue to **Final approval (manual)** → Discord **bigas-cto**.

## Env

| Variable | Purpose |
|---|---|
| `JIRA_AUTOMATION_WEBHOOK_SECRET` | Shared secret header |
| `BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS` | Default `VFA` |
| `BIGAS_JIRA_PROJECT_REPO_MAP` | `VFA:mckort/vcfieldassistant,...` |
| `BIGAS_JIRA_DEFAULT_BASE_BRANCH` | Default `main` (Cursor starting ref) |
| `BIGAS_JIRA_STATUS_*_APPROVAL` | Manual gate status names |
| `BIGAS_JIRA_AI_DAILY_QUOTA` | Default `20` |
| `CURSOR_API_KEY` | Required for Implement |
| `GITHUB_TOKEN` | Repo context + Phase 4 PR lookup |
| `DISCORD_WEBHOOK_URL_PRODUCT` / `_CTO` | Notifications |

## Jira Automation rules

One rule per AI status (same URL/headers/body; different “to” status):

1. `Research and describe (AI)`
2. `Design and plan (AI)`
3. `In Progress (AI)` (or exact board name `IN PROGRESS (AI)` — matching is case-insensitive)

```json
{
  "issue_key": "{{issue.key}}",
  "to_status": "{{issue.status.name}}",
  "from_status": "{{fieldChange.fromString}}",
  "idempotency_key": "{{issue.key}}:{{issue.status.name}}:{{fieldChange.fromString}}",
  "sync": true
}
```

Headers: `Content-Type: application/json`, `X-Bigas-Webhook-Secret`, and `X-Bigas-Access-Key` when restricted.

## Description + comments

```markdown
## Brief
## AI Research (Bigas)
## AI Plan (Bigas)
```

Human comments are included in Research / Design / Implement prompts. `[bigas-jira-ai]` system comments are ignored.

## Workstream prompts (label)

| Label | Workstream | Prompts |
|---|---|---|
| *(none / other)* | **product** (default) | Current product engineer Research / Design / Implement prompts |
| `marketing` (case-insensitive) | **marketing** | Website/content/SEO-oriented prompts (copy, pages, metadata, site patterns in-repo) |

Add the Jira label `marketing` on SEO/blog/landing-page issues. Leave unlabeled for normal product work.
