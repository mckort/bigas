# Friman investments board (FRI)

Bigas treats **Friman investments** as portfolio project **`FRI`**, with the internal board named **Friman investments** at `/board` (project switcher).

## What you get

- **Board:** AI workflow columns (Research → Design → In Progress (AI) → Final approval) on the FRI board.
- **Repo:** Default GitHub mapping `mckort/frimaninvestments` (override with `BIGAS_JIRA_PROJECT_REPO_MAP`).
- **Site:** `https://frimaninvestments.com`.
- **Auto-review / auto-fix:** Copy [`docs/pr-review.caller.yml`](pr-review.caller.yml) to `.github/workflows/pr-review.yml` in the product repo and set repository variables `BIGAS_URL`, optional `BIGAS_AUTO_FIX=true`, plus secrets `BIGAS_API_KEY` and `GH_PAT_FOR_BIGAS` (see [cto-pr-review.md](cto-pr-review.md)).
- **Implement:** Drag a card to **In Progress (AI)** with `CURSOR_API_KEY` + `GITHUB_TOKEN` on the Bigas instance.
- **Deploy:** DevOps `trigger_deployment` dispatches `deploy.yml` on `mckort/gcp-single-vm-webstack` with `site=frimaninvestments` (same VM pattern as MYL/REM). Override with `BIGAS_DEPLOY_REPO_MAP` / `BIGAS_DEPLOY_WORKFLOW_MAP` if needed.

## Env (Bigas instance)

Add **`FRI`** to your portfolio keys and sync maps:

```bash
JIRA_PROJECT_KEY=VFA,WAYW,BIG,REM,GPWW,FYDA,MYL,FRI
# Optional explicit override (defaults are baked into Bigas for FRI):
# BIGAS_JIRA_PROJECT_REPO_MAP=...,FRI:mckort/frimaninvestments
# BIGAS_DEPLOY_WORKFLOW_MAP=...|FRI:deploy.yml
# BIGAS_DEPLOY_REPO_MAP=...,FRI:mckort/gcp-single-vm-webstack
# Local checkout (your machine only — not used by Cloud Run agents):
# BIGAS_PROJECT_LOCAL_PATH_MAP=FRI:/Users/marcusfriman/Documents/Code/friman-investments
```

Restart Bigas after changing env. Existing users get the **Friman investments** board automatically on the next `/api/boards` load when `FRI` is in `JIRA_PROJECT_KEY`.

## GitHub (frimaninvestments repo)

1. Ensure `mckort/frimaninvestments` exists and `GITHUB_TOKEN` can read/write PRs and dispatch Actions.
2. Add the PR review caller workflow (above).
3. Deploys are dispatched on `mckort/gcp-single-vm-webstack` (`deploy.yml` + `site=frimaninvestments`). Do not add a product-repo `deploy.yml` unless you change the deploy map.
4. Register a GitHub webhook on the product repo for failed Actions if you want self-healing deploy fixes (`GITHUB_WEBHOOK_SECRET` on Bigas).

## Jira (optional)

Create a Jira Software project with key **`FRI`** if you want Jira sync on the board (**Sync from Jira** in settings). The internal board works without Jira.
