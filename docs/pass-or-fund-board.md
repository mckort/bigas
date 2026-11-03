# Pass or Fund board (POF)

Bigas treats **Pass or Fund** as portfolio project **`POF`**, with the internal board named **POF Board** at `/board` (project switcher). There is no Jira project.

## What you get

- **Board:** AI workflow columns (Research → Design → In Progress (AI) → Final approval) on the POF board. Dragging a card runs the same handlers as the other portfolio boards when `POF` is in `BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS`.
- **Repo:** `mckort/pass-or-fund` (override with `BIGAS_JIRA_PROJECT_REPO_MAP`). Default branch `main`.
- **Site:** `passorfund.com` resolves to project `POF` for chat, monitoring, and deploy targeting.
- **Auto-review / auto-fix:** `.github/workflows/pr-review.yml` in the product repo calls the shared Bigas workflow. Repository variables `BIGAS_URL` and optional `BIGAS_AUTO_FIX=true`, plus secrets `BIGAS_API_KEY` and `GH_PAT_FOR_BIGAS` (see [cto-pr-review.md](cto-pr-review.md)).
- **Implement:** Drag a card to **In Progress (AI)** with `CURSOR_API_KEY` + `GITHUB_TOKEN` on the Bigas instance.
- **Deploy:** `deploy POF` dispatches `deploy.yml` on `mckort/gcp-single-vm-webstack` with `site=pass-or-fund` (the app directory under `/opt/web/apps`). The Compose service is `passorfund` on port 8030. The VM stack already lists that site; see that repo’s README for the first-time cert, secrets, and Firestore database.

## Env (Bigas instance)

Add **`POF`** to the portfolio keys. Defaults for the repo, workflow, and deploy repo are in code; set the env when a secret already overrides the whole list:

```bash
JIRA_PROJECT_KEY=VFA,WAYW,BIG,REM,GPWW,GPW-PROD,FYDA,MYL,FRI,AURA,POF
BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS=VFA,WAYW,BIG,REM,GPWW,GPW-PROD,FYDA,MYL,FRI,AURA,POF
# BIGAS_JIRA_PROJECT_REPO_MAP=...,POF:mckort/pass-or-fund
# BIGAS_DEPLOY_WORKFLOW_MAP=...|POF:deploy.yml
# BIGAS_DEPLOY_REPO_MAP=...,POF:mckort/gcp-single-vm-webstack
# BIGAS_PROJECT_LOCAL_PATH_MAP=POF:/Users/marcusfriman/Documents/Code/pass-or-fund
```

Restart Bigas after changing env. **POF Board** appears on the next `/api/boards` load when `POF` is in `JIRA_PROJECT_KEY`.
