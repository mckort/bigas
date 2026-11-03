# AuraLearn board (AURA)

Bigas treats **AuraLearn** as portfolio project **`AURA`**, with the internal board named **AURA Board** at `/board` (project switcher). Existing cards already use keys such as `AURA-10`.

## What you get

- **Board:** AI workflow columns (Research and describe → Design and plan → In Progress (AI) → Final approval) on the AURA board.
- **Repo:** `mckort/auralearn`. Default branch is **`master`** (not `main`).
- **Auto-review / auto-fix:** `.github/workflows/pr-review.yml` in the product repo calls [`docs/pr-review.caller.yml`](pr-review.caller.yml). Repository variable `BIGAS_URL`, optional `BIGAS_AUTO_FIX=true`, plus secrets `BIGAS_API_KEY` and `GH_PAT_FOR_BIGAS` (see [cto-pr-review.md](cto-pr-review.md)).
- **Implement:** Drag a card to **In Progress (AI)** with `CURSOR_API_KEY` + `GITHUB_TOKEN` on the Bigas instance. Cursor starts from `master`.
- **Deploy:** DevOps dispatches `deploy.yml` on `mckort/auralearn` (Cloud Run, same pattern as BIG). It runs `deploy-auralearn.sh`. When `mobile/` changed since the last preview build, it also dispatches `eas-preview-ios.yml`, which queues `eas build --profile preview --platform ios` and returns without waiting. The result is posted to the CTO Discord chat. It does not use `mckort/gcp-single-vm-webstack`.

There is no public marketing domain yet, so chat does not map a site hostname to `AURA`.

## Env (Bigas instance)

`AURA` is already in the portfolio key list. Deploy dispatch also needs the workflow name:

```bash
JIRA_PROJECT_KEY=VFA,WAYW,BIG,REM,GPWW,GPW-PROD,FYDA,MYL,FRI,AURA,POF
BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS=VFA,WAYW,BIG,REM,GPWW,GPW-PROD,FYDA,MYL,FRI,AURA,POF
# Optional explicit override (defaults are baked into Bigas for AURA):
# BIGAS_JIRA_PROJECT_REPO_MAP=...,AURA:mckort/auralearn
# BIGAS_JIRA_REPO_BASE_BRANCH_MAP=...,mckort/auralearn:master
# BIGAS_DEPLOY_WORKFLOW_MAP=...|AURA:deploy.yml,eas-preview-ios.yml
# Do not add AURA to BIGAS_DEPLOY_REPO_MAP. Dispatch stays on mckort/auralearn.
# BIGAS_PROJECT_LOCAL_PATH_MAP=AURA:/path/to/auralearn
```

With `SECRET_MANAGER=true`, sync `BIGAS_DEPLOY_WORKFLOW_MAP` and restart. Code defaults still resolve `AURA` → `deploy.yml,eas-preview-ios.yml` on `mckort/auralearn` when the env map omits the key, after this Bigas revision is deployed. An explicit `AURA:deploy.yml` entry in the secret overrides that default, so the secret has to list `eas-preview-ios.yml` too.

## GitHub (`mckort/auralearn`)

1. `pr-review.yml` and `deploy.yml` must be on **`master`**.
2. PR review: variable `BIGAS_URL`, secret `BIGAS_API_KEY`.
3. Deploy, once, from a machine with `gcloud` and `gh`, using the repo `.env`:

```bash
./scripts/setup-github-actions-deploy.sh
```

That creates a deploy service account, binds GitHub OIDC to `mckort/auralearn`, and stores `.env` as the Actions secret `AURALEARN_DEPLOY_ENV`. Variables: `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SERVICE_ACCOUNT`.
