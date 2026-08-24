# Jira status automation (BIG-1)

Automate AI work when humans move Jira issues into AI columns.

Endpoint: `POST /mcp/tools/jira_status_automation`

## Phases shipped

### Phase 1 — Research and describe (AI)
Enrich description (keep human Brief) → **Description approval (manual)** → Discord **bigas-pm** and the Product Manager chat thread.

### Phase 2 — Design and plan (AI)
Write **AI Plan (Bigas)** → **Design approval (manual)** → Discord **bigas-cto** and the CTO chat thread.

### Phase 3 — In Progress (AI)
Launch Cursor cloud agent on the mapped GitHub repo (`autoCreatePR=true`) → comment agent URL on the issue → leave in **In Progress (AI)** → Discord **bigas-cto** and the CTO chat thread. The implement prompt tells the agent to remove dead/unused code created by its own change before it opens the PR (not a repo-wide cleanup).

Simple tickets may skip Phases 1–2. Drag from To Do with a title, short brief, and/or screenshot; Implement runs from that context (no AI Research / AI Plan required).

### Phase 4 — PR merged → Final approval
When the PR is **merged** (Bigas auto-merge, GitHub auto-merge after checks, or a human merge), Bigas finds the ticket key on the PR (`VFA-14:` title / `Jira: VFA-14` body) and moves the issue to **Final approval (manual)** → Discord **bigas-cto** and the Activity feed (not the CTO chat thread). Internal-board tickets are updated in the ticket store. Ready-to-merge does **not** move the card.

### Epics — Goal Engine (not implement)
If the issue is an **Epic**, Phases 1–3 above are skipped. The same webhook runs the Proactive Goal Engine for that Epic and leaves it in the column it was dragged to:

| Column | Goal Engine phase |
|---|---|
| `Research and describe (AI)` | Create research/discovery child Tasks |
| `Design and plan (AI)` | Create Todo-ready child Tasks |
| `In Progress (AI)` | Progress report + next-cycle child Tasks |

Weekly Cloud Scheduler (`/api/agents/evaluate-goals`) repeats the same evaluation for Epics still in those statuses. Child Tasks/Bugs still use Phases 1–4 as usual.

## Env

| Variable | Purpose |
|---|---|
| `JIRA_AUTOMATION_WEBHOOK_SECRET` | Shared secret header |
| `BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS` | Default `VFA,WAYW,BIG,REM,GPWW,FYDA,MYL` |
| `BIGAS_JIRA_PROJECT_REPO_MAP` | `VFA:mckort/vcfieldassistant,WAYW:mckort/roadpal,BIG:mckort/bigas,REM:mckort/remotebrief,GPWW:Green-Promo-Wear-Global/greenpromowear-website,FYDA:mckort/fulfillyourdreamadventure,MYL:mckort/mylifesdeed` |
| `BIGAS_JIRA_REPO_BASE_BRANCH_MAP` | Per-repo Cursor starting ref, e.g. `mckort/fulfillyourdreamadventure:master,mckort/vcfieldassistant:main`. Defaults include FYDA→`master`; others→`main`. |
| `BIGAS_JIRA_DEFAULT_BASE_BRANCH` | Fallback when repo is not in the map (default `main`) |
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
