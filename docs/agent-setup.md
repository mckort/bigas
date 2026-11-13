# Set up Bigas with an AI agent

This is the procedure for an AI coding agent (Claude Code, Cursor, Codex, …) working in a fresh clone of this repo, together with the human who owns the accounts. A human can follow it too.

The goal: Bigas runs, and each of the human's GitHub repos is wired so that

- a card dragged to **In Progress (AI)** on `/board` becomes a pull request,
- every pull request gets a Bigas review and autofix, and is merged when clean,
- the merged card moves to **Final approval**,
- **Ship** on `/cockpit` can deploy the repo,
- a failed GitHub Actions run reaches Bigas (self-healing CI).

These scripts do the work, and each one is safe to rerun:

| Script | What it does |
|---|---|
| `python scripts/setup.py --noninteractive` | Writes a local `.env` from the environment. No prompts. One LLM key is required |
| `python scripts/discover_portfolio.py` | Drafts `bigas.portfolio.yaml` from a folder of cloned repos |
| `python scripts/apply_portfolio.py` | Plans, then with `--apply` connects every row in that file |
| `python scripts/connect_repo.py` | Same plan or apply for a single repo |
| `python scripts/setup_cloud.py` | Plans, then with `--apply` creates the Cloud Run plumbing. `--deploy` runs `./deploy.sh` |
| `python scripts/bigas_doctor.py` | Checks the instance and every connected repo; `--json` for agents; exit 1 on failure |

## Prerequisite: one folder of clones

Ask the human to put every product repo in one parent folder, each clone a subfolder. Folders that are not git repos are ignored, so notes and scratch directories can sit beside the clones. When this Bigas clone is a sibling of the product repos, the parent of this repo is the folder to scan.

```bash
python scripts/discover_portfolio.py              # parent of this repo
python scripts/discover_portfolio.py --root ~/Code
```

The draft lists, for each GitHub repo, a suggested project key, `owner/repo`, the default branch when `origin/HEAD` is set (otherwise the local checkout, marked as a guess), and any `workflow_dispatch` workflow whose file name contains `deploy`. Other `workflow_dispatch` files are named in a comment so the human can add them (for example an iOS preview workflow). An SSH host alias counts as GitHub when `ssh -G that-alias` prints `hostname github.com`. A second checkout of the same repo, including a git worktree, is left out and named in a comment. A repo with no deploy file gets a comment listing sibling clones that do have one, which is how a shared infra repo shows up. The script does not set `deploy_repo` by itself.

Show the file to the human. They delete rows they do not want on the board, shorten keys (`vcfieldassistant` is not a good card prefix), confirm any guessed branch, and fill `deploy_repo` when the workflow lives in another repo. `release: staging` is for a project that already ships versioned releases. A new project leaves `release` empty and uses **Go to production** later.

`bigas.portfolio.yaml` is gitignored. Do not commit it.

## Rules for the agent

1. **Never print, log or commit secret values.** The human creates and pastes secrets into `.env` themselves. `.env` is gitignored; keep it that way.
2. **Dry-run first.** Run `connect_repo.py` without `--apply`, show the human the plan, then apply.
3. **Ask, don't guess** on the decisions in step 0. A folder of clones may draft the repo list (`discover_portfolio.py`). Show that file to the human before apply. Do not invent a key, branch, or deploy workflow the file and the human did not confirm.
4. **Prefer the scripts to hand edits.** They keep the env maps in the format the server parses.
5. **Finish with the doctor.** The work is done when `python scripts/bigas_doctor.py --json` reports `"ok": true` and the warnings left over are ones the human chose to accept.

## 0. Decisions to ask the human for

| Decision | Options | Default |
|---|---|---|
| Where Bigas runs | Local (laptop) or Google Cloud Run | Local first, then Cloud Run. GitHub Actions cannot reach a laptop, so PR review and deploy callbacks need Cloud Run. |
| Board | Built-in `/board` or an existing Jira | Built-in. Jira: see [jira-automation.md](jira-automation.md). |
| Repos | For each: a project key (`ACME`), `owner/repo`, the branch work starts from | Key = short uppercase name. Branch = the repo's default branch. |
| Release mode per repo | **Development** (PRs merge to main; Deploy button), **production** (versioned releases; switched later with the Go to production button), or a staging rehearsal (see step 4) | Development. Switch when the project has users. |
| Deploy per repo | Workflow file name(s), or none | None. Add later. |
| Optional areas | `BIGAS_FEATURES`: `marketing`, `objectives`, `outreach`, `none`, `all` | Unset (all on). For the core loop only: `BIGAS_FEATURES=none`. |

## 1. Run Bigas locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# The human exports one key. The script writes .env and does not print the value.
GEMINI_API_KEY=... python scripts/setup.py --noninteractive
cd frontend && npm install && npm run build && cd ..
python run_core.py                    # http://localhost:8080, dev token: bigas-dev-token
python scripts/bigas_doctor.py --offline
```

`python scripts/setup.py` without `--noninteractive` is the interactive wizard, for a human at a keyboard. `--noninteractive` reads `GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY` from the environment (Gemini wins when more than one is set; `--provider` overrides). When `GITHUB_TOKEN`, `CURSOR_API_KEY`, `BIGAS_ACCESS_KEYS`, `GITHUB_WEBHOOK_SECRET`, `BIGAS_PUBLIC_URL`, or `BIGAS_FEATURES` are already exported, they are copied into `.env`. An existing `.env` is left alone unless `--force` is passed. If no LLM key is exported, the script exits and names the variable to set. It does not copy `env.example`.

After the server is up, draft and apply the portfolio. `--skip-github` writes the board maps before a public URL exists:

```bash
python scripts/discover_portfolio.py
python scripts/apply_portfolio.py
python scripts/apply_portfolio.py --apply --skip-github
```

## 2. Deploy to Cloud Run (needed for the GitHub loop)

Plan, then create the project plumbing. The long form of the same steps is the [README deploy tutorial](../README.md#tutorial-deploy-your-first-bigas-server).

```bash
python scripts/setup_cloud.py --project acme-bigas            # plan
python scripts/setup_cloud.py --project acme-bigas --apply    # APIs, Artifact Registry, runner service account, Firestore
```

`--apply` needs an active `gcloud` account and billing on the project. It stops when either is missing. It enables the Cloud Run, Artifact Registry, Storage, Secret Manager, Firestore, Identity Toolkit, and GA4 APIs, creates `bigas-repo` in `europe-north1` and the `bigas-runner` service account, grants the runtime roles, and creates a Firestore database in `eur3` when the project has none. Empty Cloud Run settings in `.env` are filled in (`GOOGLE_PROJECT_ID`, `GOOGLE_SERVICE_ACCOUNT_EMAIL`, `DOCKER_REPO`, `IMAGE_NAME`, `IMAGE_TAG`). Existing values are left as they are.

Set these in `.env` (the human supplies the values). `BIGAS_PUBLIC_URL` is the URL printed by the deploy below:

| Variable | Why |
|---|---|
| `CHAT_STORAGE_MODE=firestore`, `CHAT_AUTH_MODE=firebase` + Firebase vars | Persistent chat and real sign-in ([README: Chat setup](../README.md#setup)) |
| `BIGAS_ACCESS_KEYS` | Random string(s). GitHub workflows send one as `BIGAS_API_KEY`. `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `BIGAS_PUBLIC_URL` | The Cloud Run URL. Only read by the setup scripts (written to each repo as `BIGAS_URL`). |
| `GITHUB_TOKEN` | Fine-grained PAT on the product repos: **Contents**, **Pull requests**, **Actions**, **Workflows** read/write; **Variables**, **Secrets**, **Webhooks** read so the doctor can verify. |
| `CURSOR_API_KEY` | Cards → pull requests, and review autofix. Cursor dashboard → Integrations. |
| `GITHUB_WEBHOOK_SECRET` | Random string. Signs the `workflow_run` webhook (self-healing CI, deploy post-checks). |

Then deploy. `./deploy.sh` needs `GA4_PROPERTY_ID` and one of `GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`. With `SECRET_MANAGER=true`, the script syncs Secret Manager first (those values win over Cloud Run env at startup).

```bash
python scripts/setup_cloud.py --project acme-bigas --apply --deploy
```

Three things stay with the human: billing and `gcloud auth login`; the Firebase web app (add this GCP project in the Firebase console, copy the web config into `FIREBASE_*` and `VITE_FIREBASE_*`, set `CHAT_AUTH_MODE=firebase` and `CHAT_STORAGE_MODE=firestore` — [README: Chat setup](../README.md#setup)); and copying the Cloud Run URL into `BIGAS_PUBLIC_URL`.

## 3. Connect a repo

When the portfolio file from the prerequisite is filled in, one command covers every row. It calls `connect_repo.py` once per project. Dry run first:

```bash
python scripts/apply_portfolio.py                         # plan
python scripts/apply_portfolio.py --webhook --apply       # env + GitHub, after BIGAS_PUBLIC_URL is set
```

One repo, or a row the file does not describe yet: ask for the key, repo, branch, release model and deploy workflow, then:

```bash
# 1. plan
python scripts/connect_repo.py --key ACME --repo acme/shop --branch main \
  --name "Acme Shop" --deploy deploy.yml --webhook
# 2. show the plan to the human, then apply
python scripts/connect_repo.py --key ACME --repo acme/shop --branch main \
  --name "Acme Shop" --deploy deploy.yml --webhook --apply
```

`--apply` needs `gh` signed in as someone with admin on the repo (`gh auth status`). It:

| Where | Change | Purpose |
|---|---|---|
| `.env` | `JIRA_PROJECT_KEY(S)` += `ACME` | Creates the ACME board; allows AI steps for it |
| `.env` | `BIGAS_JIRA_PROJECT_REPO_MAP` += `ACME:acme/shop` | Card → repo for Cursor, PRs, releases |
| `.env` | `BIGAS_JIRA_REPO_BASE_BRANCH_MAP` += `acme/shop:main` | Production branch for deploys and hotfixes |
| `.env` | `PROJECT_BRANCH_MAPPING` += `ACME:master` when `--branch` is not `main` | Branch Cursor starts from. Required for every repo whose default is not `main`. `DEFAULT:main` does not fill this in |
| `.env` | `BIGAS_PROJECT_NAME_MAP` += `ACME:Acme Shop` | Display name in the cockpit and chat |
| `.env` | `BIGAS_DEPLOY_WORKFLOW_MAP` += `ACME:deploy.yml` | Ship card and DevOps deploy |
| `.env` | `PROJECT_BRANCH_MAPPING` += `ACME:staging` (with `--versioned staging`) | Versioned release branches |
| repo | Actions variable `BIGAS_URL`, secret `BIGAS_API_KEY` | Workflows call back to Bigas |
| repo | PR from `bigas-setup`: `.github/workflows/pr-review.yml` | Review → autofix → merge → Final approval on every PR |
| repo | same PR, with `--versioned`: `cherry_pick.yml`, `rebase_release.yml` | Hotfix cherry-picks and release-branch rebase |
| repo | with `--webhook`: `workflow_run` webhook to `/mcp/tools/github_workflow_run` | Self-healing CI |

Then:

1. The human merges the `bigas-setup` pull request (workflows only run from the base branch).
2. Restart Bigas locally, or sync secrets and `./deploy.sh`.
3. `python scripts/bigas_doctor.py` and fix what it reports.

**Forks.** `pr-review.yml` calls the reusable workflow in `mckort/bigas` by default. To run your fork's copy, set `BIGAS_WORKFLOW_SOURCE=you/bigas@main` in `.env` or pass `--workflow-source`.

**Deploy workflow.** DevOps only needs a workflow with a `workflow_dispatch` trigger that fails when the deploy fails. If the repo has none, start from [workflows/deploy.example.yml](workflows/deploy.example.yml). When one infra repo deploys several sites, pass `--deploy-repo owner/infra`; Bigas then sends a `site` input.

## 4. Release models

**Development** (default). PRs merge to the production branch; **Deploy** on the Ship card dispatches the deploy workflow on it. That branch has to be the one in `PROJECT_BRANCH_MAPPING`. Pass the GitHub default as `--branch` (`master` for AuraLearn and Fulfil Your Dream Adventure). `connect_repo.py` writes `KEY:master` when the branch is not `main`. A project left only on `DEFAULT:main` makes **In Progress (AI)** start Cursor from `main`. If the repo has no `main`, no agent starts.

**Production (versioned).** The easy way: **Go to production** on the project's Ship card in the cockpit. It previews and then runs every step below, and the project shows *Production* from then on. New work targets `staging-x.y.z` and **Prepare deploy** merges that branch to the production branch, the same path as `PROJECT_BRANCH_MAPPING=KEY:staging` (VC Field Assistant). **Release mode → Back to development** undoes the switch (the workflows, branches and versions stay):

1. a pull request in the repo adding `pr-review.yml`, `cherry_pick.yml` and `rebase_release.yml` (merge it);
2. version `0.1.0` on the board (or the open version, if there is one);
3. branch `staging-0.1.0` from the production branch;
4. the project setting that sends new AI work to that branch (stored in the app; no redeploy).

The setup-script way is `--versioned staging`, which writes the env instead.

Work for version `0.3.0` lands on `staging-0.3.0`. Create the version under **Releases** on the board. **Prepare deploy** in the cockpit reviews the cut, merges it to the production branch, asks for your yes, deploys, tags `v0.3.0` and rebases the next release branch. Needs `cherry_pick.yml` and `rebase_release.yml` in the repo (added by `--versioned`).

**Staging rehearsal.** Bigas builds a real staging stack from production, rehearses the deploy, then deploys to production. The repo needs five `workflow_dispatch` workflows, and Bigas needs a JSON entry in `BIGAS_STAGING_ENV_MAP` (one line in `.env`):

```bash
BIGAS_STAGING_ENV_MAP={"ACME":{"repo":"acme/shop","candidate_branch":"develop","production_branch":"production","staging_url":"https://staging.acme.com","production_url":"https://acme.com","workflows":{"prepare_staging":"prepare-staging.yml","update_staging":"update-staging.yml","teardown":"teardown-staging.yml","backup":"backup-production.yml","deploy_production":"deploy-production.yml"}}}
```

All five workflow keys are required. The cockpit then shows **Prepare staging**, **Update staging**, **Teardown staging** and **Prepare deploy** for ACME. Keep the project key on the board list (`--key ACME` via `connect_repo.py`).

## 5. Verify

```bash
python scripts/bigas_doctor.py --json
```

Each entry in `checks` has `status` (`ok`, `warn`, `fail`, `info`), `message` and `fix`. Fix every `fail`; ask the human about each `warn`.

Then a smoke test with the human:

1. Open `/cockpit`. ACME appears under **Ship** if it has a deploy workflow.
2. On `/board`, ACME board: create a card with a one-line brief and drag it to **In Progress (AI)**. Within minutes it shows under **AI working** with a PR link.
3. On that PR, the **Bigas PR review** check runs and posts a review.
4. After merge the card is in **Final approval**, and shows under **Needs you** in the cockpit.

## Add a project (checklist)

Use this when the human says "add my repo X" or "set up project Y". Steps 1 and 2 (Bigas running on Cloud Run) only happen once.

**Ask the human:**

1. A project key: 2–8 uppercase letters (`ACME`), and a display name.
2. The GitHub repo (`owner/repo`) and the branch work starts from (look it up: `gh repo view owner/repo --json defaultBranchRef`). Pass that exact name as `--branch`. If it is not `main`, the plan must show `PROJECT_BRANCH_MAPPING=KEY:<that branch>` (`AURA:master`, `FYDA:master`). Do not leave it to `DEFAULT:main`.
3. How it deploys: a workflow file in the repo (`ls .github/workflows`), or none yet.
4. Development or production. Default: development. Switching later is one button.

**What has to exist:**

| Where | Development | Production adds |
|---|---|---|
| Bigas env (`.env` / Secret Manager) | `GITHUB_TOKEN` (Contents, Pull requests, Actions, Workflows: read/write), `CURSOR_API_KEY`, `BIGAS_ACCESS_KEYS`, `BIGAS_PUBLIC_URL`, `GITHUB_WEBHOOK_SECRET`; the project in `JIRA_PROJECT_KEY(S)` and the repo maps; `PROJECT_BRANCH_MAPPING=KEY:<default branch>` whenever that branch is not `main` | Same token. The switch is stored in the app |
| Repo workflows | `.github/workflows/pr-review.yml` (calls the Bigas review); a deploy workflow with `workflow_dispatch` for the Ship card | `cherry_pick.yml`, `rebase_release.yml` |
| Repo settings | Actions variable `BIGAS_URL`, secret `BIGAS_API_KEY`; `workflow_run` webhook (self-healing CI) | Nothing further. `main` stays open; new pull requests target `staging-x.y.z` |
| Board | The project board (created from the key) | A version (`0.1.0`) and its `staging-0.1.0` branch |

**Do it:**

```bash
# Development: everything in the left column
python scripts/connect_repo.py --key ACME --repo owner/repo --branch main \
  --name "Acme Shop" --deploy deploy.yml --webhook            # plan; show the human
python scripts/connect_repo.py ... --apply                    # same flags + --apply
# then: merge the bigas-setup PR, sync secrets if SECRET_MANAGER=true, ./deploy.sh
python scripts/bigas_doctor.py --json                         # fix every "fail"
```

Production, now or later: the human opens the cockpit, clicks **Go to production** on the project's Ship card, checks the preview and applies it, then merges the `bigas-production` PR it opens. This is the right path for a project that already runs on Bigas. Do not do these steps by hand.

Prefer the button even for a project that should be in production from day one: connect it as development, deploy, then click **Go to production**. The `--versioned staging` flag on `connect_repo.py` is the env-only route. It writes `PROJECT_BRANCH_MAPPING` and adds the two release workflows to the setup PR, but it does not create the first version. The cockpit then shows the project as production from env, so the button is not offered.

If the repo has no deploy workflow, start from [workflows/deploy.example.yml](workflows/deploy.example.yml). Without one the project works fine but has no Ship card.

Finish with the smoke test in step 5.

## Remove a repo

1. Delete the key from `JIRA_PROJECT_KEY(S)` and its entries in the maps listed in step 3.
2. In the repo: delete `.github/workflows/pr-review.yml` (and `cherry_pick.yml`, `rebase_release.yml`), the `BIGAS_URL` variable, the `BIGAS_API_KEY` secret and the webhook.
3. Restart or redeploy. The board and its cards stay in storage; delete the board in `/board` if you want it gone.

## Troubleshooting

| Symptom | Check |
|---|---|
| Dragging to an AI column does nothing | Doctor `ai_allowed`; `CURSOR_API_KEY`; Bigas logs for `jira_status_automation` |
| PR gets no review | Doctor `gh_pr_review`, `gh_var`, `gh_secret`; the workflow must be on the base branch; the Actions run log shows the HTTP call to `BIGAS_URL` |
| Ship card missing | Doctor `deploy_mapped`; the project must be on the board list; the workflow needs `workflow_dispatch` |
| Env change has no effect on Cloud Run | `SECRET_MANAGER=true`: sync the secret. Otherwise check the variable is passed in `deploy.sh` |
| `gh: HTTP 403` from `connect_repo.py` | `gh auth status`; the account needs admin on the repo to set secrets and webhooks |
