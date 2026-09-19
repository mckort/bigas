# Friman investments board (FRI)

Bigas treats **Friman investments** as portfolio project **`FRI`**, with the internal board named **Friman investments** at `/board` (project switcher).

## What you get

- **Board:** AI workflow columns (Research → Design → In Progress (AI) → Final approval) on the FRI board.
- **Repo:** Default GitHub mapping `mckort/friman-investments` (override with `BIGAS_JIRA_PROJECT_REPO_MAP`).
- **Auto-review / auto-fix:** Copy [`docs/pr-review.caller.yml`](pr-review.caller.yml) to `.github/workflows/pr-review.yml` in the product repo and set repository variables `BIGAS_URL`, optional `BIGAS_AUTO_FIX=true`, plus secrets `BIGAS_API_KEY` and `GH_PAT_FOR_BIGAS` (see [cto-pr-review.md](cto-pr-review.md)).
- **Implement:** Drag a card to **In Progress (AI)** with `CURSOR_API_KEY` + `GITHUB_TOKEN` on the Bigas instance.
- **Deploy:** DevOps `trigger_deployment` dispatches `deploy.yml` on the product repo when `BIGAS_DEPLOY_WORKFLOW_MAP` includes `FRI:deploy.yml` (default in code when unset).

## Env (Bigas instance)

Add **`FRI`** to your portfolio keys and sync maps:

```bash
JIRA_PROJECT_KEY=VFA,WAYW,BIG,REM,GPWW,FYDA,MYL,FRI
# Optional explicit override (defaults are baked into Bigas for FRI):
# BIGAS_JIRA_PROJECT_REPO_MAP=...,FRI:mckort/friman-investments
# BIGAS_DEPLOY_WORKFLOW_MAP=...|FRI:deploy.yml
# Local checkout (your machine only — not used by Cloud Run agents):
# BIGAS_PROJECT_LOCAL_PATH_MAP=FRI:/Users/marcusfriman/Documents/Code/friman-investments
```

Restart Bigas after changing env. Existing users get the **Friman investments** board automatically on the next `/api/boards` load when `FRI` is in `JIRA_PROJECT_KEY`.

## GitHub (friman-investments repo)

1. Ensure the repo exists and `GITHUB_TOKEN` can read/write PRs and dispatch Actions.
2. Add the PR review caller workflow (above).
3. Add or reuse a `deploy.yml` with `workflow_dispatch` if DevOps should trigger deploys from chat.
4. Register a GitHub webhook on the repo for failed Actions if you want self-healing deploy fixes (`GITHUB_WEBHOOK_SECRET` on Bigas).

## Jira (optional)

Create a Jira Software project with key **`FRI`** if you want Jira sync on the board (**Sync from Jira** in settings). The internal board works without Jira.
