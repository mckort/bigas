# AGENTS.md

Instructions for AI coding agents working in this repo (Claude Code reads it through `CLAUDE.md`).

## What Bigas is

A virtual HQ for a solo founder: a Flask API plus a React app (`frontend/`), deployed to Google Cloud Run. The daily loop:

- `/cockpit`: what needs the founder (approvals, deploy confirms), what the AI is doing, what can ship, the weekly update
- `/board`: Kanban cards with AI columns (research → plan → implement via Cursor → PR)
- chat: Chief of Staff plus Product, CTO, CFO, DevOps (and Marketing when enabled)
- GitHub: every product repo calls the reusable `.github/workflows/pr-review.yml` here

## If you were asked to set Bigas up, or to connect a repo

Follow **[docs/agent-setup.md](docs/agent-setup.md)** step by step. In short:

```bash
python scripts/connect_repo.py --key ACME --repo owner/repo            # plan (dry run)
python scripts/connect_repo.py --key ACME --repo owner/repo --apply    # env + GitHub wiring
python scripts/bigas_doctor.py --json                                  # verify; fix every "fail"
```

To add one more project to a running Bigas, use the **Add a project (checklist)** section of the playbook. It lists what each mode needs (tokens, repo workflows, settings), and when to use the cockpit's **Go to production** button instead of env.

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
