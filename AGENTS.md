# AGENTS.md

Instructions for AI coding agents working in this repo (Claude Code reads it through `CLAUDE.md`).

## What Bigas is

A virtual HQ for a solo founder: a Flask API plus a React app (`frontend/`), deployed to Google Cloud Run. The daily loop:

- `/cockpit`: what needs the founder (approvals, deploy confirms), what the AI is doing, what can ship, the weekly update
- `/board`: Kanban cards with AI columns (research → plan → implement via Cursor → PR)
- chat: Chief of Staff plus Product, CTO, CFO, DevOps (and Marketing when enabled)
- GitHub: every product repo calls the reusable `.github/workflows/pr-review.yml` here

## If you were asked to set Bigas up, or to connect a repo

Follow **[docs/agent-setup.md](docs/agent-setup.md)** step by step. The human should clone each product repo as a subfolder of one parent (this repo's parent is the default when they are siblings). Non-repo folders are ignored.

```bash
# LLM key in the environment. The script does not print it.
GEMINI_API_KEY=... python scripts/setup.py --noninteractive
python scripts/discover_portfolio.py                 # writes bigas.portfolio.yaml
# show that file to the human; they edit keys and delete rows
python scripts/apply_portfolio.py                    # plan every project
python scripts/apply_portfolio.py --apply --skip-github   # env maps, before a public URL
python scripts/setup_cloud.py --project PROJECT_ID   # plan Cloud Run
python scripts/setup_cloud.py --project PROJECT_ID --apply --deploy
python scripts/apply_portfolio.py --webhook --apply  # GitHub, after BIGAS_PUBLIC_URL
python scripts/bigas_doctor.py --json                # verify; fix every "fail"
```

One repo by hand is still `scripts/connect_repo.py` (dry run, then `--apply`). To add one more project to a running Bigas, use the **Add a project (checklist)** section of the playbook. It lists what each mode needs (tokens, repo workflows, settings), and when to use the cockpit's **Go to production** button instead of env.

Pass `--branch` as that repo's real default branch (`gh repo view owner/repo --json defaultBranchRef`). `connect_repo.py` writes `PROJECT_BRANCH_MAPPING=KEY:<branch>` whenever that branch is not `main`. `DEFAULT:main` only covers projects you left unnamed. It does not mean "each repo's default branch". A `master` repo left on `DEFAULT` makes **In Progress (AI)** ask Cursor to start from `main`, and no agent starts. AuraLearn and Fulfil Your Dream Adventure are `AURA:master` and `FYDA:master`.

Never print or commit secret values. Ask the human for credentials and for the decisions listed in step 0 of the playbook.

## Layout

| Path | What |
|---|---|
| `app.py` | Flask app factory; registers blueprints; access-key bypass list (`_is_public_path`) |
| `bigas/resources/<area>/endpoints.py` | HTTP + MCP tools per area: `product`, `cto`, `devops`, `marketing`, `chat`, `tickets`, `email` |
| `bigas/agents/chief_of_staff.py` | Chat agents: prompts, tool lists, delegation |
| `bigas/tickets/` | Built-in board: store, columns (`constants.py`), releases |
| `bigas/resources/product/jira_automation/` | AI column automation (research, plan, implement) |
| `bigas/resources/devops/` | Deploys: `prepare.py` (versioned), `gpw_pipeline.py` (staging rehearsal), `config.py` (deploy targets) |
| `bigas/portfolio.py` | Project key → repo, name, site; env maps |
| `bigas/cockpit.py` | `GET /api/cockpit` read model |
| `bigas/features.py` | `BIGAS_FEATURES` optional areas |
| `bigas/go_live.py`, `bigas/project_settings.py` | "Go to production": development → versioned releases per project; app-side release mode merged into `project_branch_mapping_from_env()` |
| `bigas/setup_check.py`, `bigas/setup_repo.py` | Logic behind the doctor and connect scripts |
| `bigas/setup_portfolio.py`, `bigas/setup_cloud.py` | Portfolio draft/apply, and the Cloud Run setup script |
| `frontend/src/components/` | `CockpitLayout`, `BoardLayout`, `ChatLayout`, `ObjectivesLayout` |
| `docs/pr-review.caller.yml` | The workflow product repos copy |
| `deploy.sh` | Cloud Run deploy; only variables listed in it reach Cloud Run |

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q                       # all tests
python -m pytest tests/test_cockpit.py -q        # one file
cd frontend && npm install && npm run build      # frontend (Flask serves frontend/dist)
python run_core.py                               # local server on :8080
```

Some `tests/test_chat.py` tests fail on `main` because their fake LLM clients predate the `max_tokens` argument. Compare against `main` before assuming you broke them.

## Conventions

- Commit messages and PR titles start with the board key when there is one: `BIG-123: Short imperative summary`.
- A new env variable that must reach Cloud Run needs a line in `deploy.sh` (and in `env.example`). Secrets go in `SECRET_MANAGER_SECRET_NAMES` and `scripts/sync_env_to_secret_manager.py`.
- A new SPA route or `/api/*` path used by the browser goes in `_is_public_path` in `app.py`, or restricted mode blocks it before chat auth runs.
- Env maps keep their historical `JIRA_` names; they also drive the built-in board.
- Keep user-facing copy plain and short. Sentence case for buttons and headings.
