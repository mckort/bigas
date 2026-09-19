# Friman investments board (FRI)

Bigas treats **Friman investments** as portfolio project **`FRI`**, with the internal board named **FRI Board** at `/board` (project switcher), matching other portfolio boards (VFA Board, BIG Board, …).

## What you get

- **Board:** AI workflow columns (Research → Design → In Progress (AI) → Final approval) on the FRI board.
- **Repo:** Default GitHub mapping `mckort/frimaninvestments` (no hyphen — override with `BIGAS_JIRA_PROJECT_REPO_MAP`).
- **Site:** `frimaninvestments.com` resolves to project `FRI` for chat, monitoring, and deploy targeting.
- **Auto-review / auto-fix:** Copy [`docs/pr-review.caller.yml`](pr-review.caller.yml) to `.github/workflows/pr-review.yml` in the product repo and set repository variables `BIGAS_URL`, optional `BIGAS_AUTO_FIX=true`, plus secrets `BIGAS_API_KEY` and `GH_PAT_FOR_BIGAS` (see [cto-pr-review.md](cto-pr-review.md)).
- **Implement:** Drag a card to **In Progress (AI)** with `CURSOR_API_KEY` + `GITHUB_TOKEN` on the Bigas instance.
- **Deploy:** Like GPWW/FYDA/REM/MYL, DevOps dispatches `deploy.yml` on `mckort/gcp-single-vm-webstack` with `site=frimaninvestments` (default in code; extend `BIGAS_DEPLOY_REPO_MAP` in Secret Manager if you override other VM sites).

## Env (Bigas instance)

Add **`FRI`** to your portfolio keys and sync maps:

```bash
JIRA_PROJECT_KEY=VFA,WAYW,BIG,REM,GPWW,FYDA,MYL,FRI
# Optional explicit override (defaults are baked into Bigas for FRI):
# BIGAS_JIRA_PROJECT_REPO_MAP=...,FRI:mckort/frimaninvestments
# BIGAS_DEPLOY_WORKFLOW_MAP=...|FRI:deploy.yml
# BIGAS_DEPLOY_REPO_MAP=...,FRI:mckort/gcp-single-vm-webstack
# Local checkout (your machine only — not used by Cloud Run agents):
# BIGAS_PROJECT_LOCAL_PATH_MAP=FRI:/Users/marcusfriman/Documents/Code/frimaninvestments
```

Restart Bigas after changing env. New users get **FRI Board** when `FRI` is in `JIRA_PROJECT_KEY`. Boards still titled **Friman investments** are renamed to **FRI Board** on the next `/api/boards` load.

## GitHub (`mckort/frimaninvestments`)

1. Ensure the repo exists at **`mckort/frimaninvestments`** (not `friman-investments`) and `GITHUB_TOKEN` can read/write PRs and dispatch Actions on the VM infra repo when deploying.
2. Add the PR review caller workflow (above) in the **product** repo.
3. Register a GitHub webhook on the product repo for failed Actions if you want self-healing deploy fixes (`GITHUB_WEBHOOK_SECRET` on Bigas).

## Jira (optional)

Create a Jira Software project with key **`FRI`** if you want Jira sync on the board (**Sync from Jira** in settings). The internal board works without Jira.
