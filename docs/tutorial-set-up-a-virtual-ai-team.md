# Set up a virtual AI team in 5 minutes

You're the founder, the PM, and the on-call engineer. Bigas gives you a **virtual staff** — Chief of Staff, Marketing, Product, CTO, CFO, and DevOps — in one open-source repo you can fork and run locally with **only an LLM API key**.

No Google Cloud project. No Firebase. No Discord webhook. Fork, configure, and chat in under five minutes.

---

## What you'll get

After this tutorial you will have:

- A local Bigas server at **http://localhost:8080**
- **Web chat** with six AI specialists (powered by Gemini or OpenAI)
- A native **Kanban board** at `/board` for tasks and AI-assisted columns
- An **Objectives** dashboard at `/objectives` for quarterly OKRs and Key Results

Everything runs on your laptop. Chat history stays in memory until you restart — perfect for evaluation before you wire up production services.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Git** | To clone your fork |
| **Python 3.10+** | For the setup wizard (`python scripts/setup.py`) |
| **Docker** | Recommended — builds the UI and runs the server in one command |
| **LLM API key** | [Gemini](https://aistudio.google.com/apikey) or [OpenAI](https://platform.openai.com/api-keys) |

**Without Docker:** you can run `pip install -r requirements.txt && python run_core.py` after building the frontend once (`cd frontend && npm install && npm run build`). Docker is the fastest path for a first run.

---

## Step 1: Fork the repo

1. Open **[github.com/mckort/bigas](https://github.com/mckort/bigas)**.
2. Click **Fork** (top right) and create the fork under your GitHub account or organization.

Forking keeps your `.env` (API keys) out of the upstream repo and gives you a place to customize agents, add integrations, and open PRs back to the project.

---

## Step 2: Clone your fork

Replace `YOUR_GITHUB_USER` with your username:

```bash
git clone https://github.com/YOUR_GITHUB_USER/bigas.git
cd bigas
```

Optional: add the upstream remote if you want to pull future Bigas releases:

```bash
git remote add upstream https://github.com/mckort/bigas.git
```

---

## Step 3: Run the setup wizard

The wizard writes a minimal `.env` with **zero-config local defaults** — in-memory chat, dev login, no GCP or Firebase:

```bash
python scripts/setup.py
```

You will be prompted for:

1. **LLM provider** — `gemini` (default) or `openai`
2. **API key** — the only required secret for the MVP
3. **Optional specialists** — GitHub, Jira, GA4, Discord, Cursor autofix (say **no** to all of these for the fastest first run; you can re-run the wizard anytime)

The wizard sets these automatically:

| Setting | Value | Why |
|---|---|---|
| `CHAT_STORAGE_MODE` | `memory` | No Firestore — chat stays in-process |
| `CHAT_AUTH_MODE` | `dev` | No Firebase — use the dev token below |
| `CHAT_ENABLED` | `true` | Web chat UI at `/` |
| `CHAT_DEV_TOKEN` | `bigas-dev-token` | Sign-in token for local dev |
| `PORT` | `8080` | Local server port |

If `.env` already exists, the wizard asks before overwriting.

---

## Step 4: Start Bigas

From the repo root:

```bash
docker compose up --build
```

The first build compiles the React frontend and installs Python dependencies. When you see the server listening on port 8080, open your browser.

**Alternative (no Docker):**

```bash
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
python run_core.py
```

---

## Step 5: Sign in and meet your team

1. Open **http://localhost:8080**
2. Click **Sign in** (or go to `/login`)
3. Enter **any email address** and token **`bigas-dev-token`**

You land in chat with the **Chief of Staff** — the default agent that coordinates the rest of the team. Use the specialist tabs (or the mobile bottom bar) to talk directly to Marketing, Product, CTO, CFO, or DevOps.

### Three surfaces to explore

| Surface | URL | What to try |
|---|---|---|
| **Chat** | `/` | Ask: *"What can this team help me with this week?"* |
| **Board** | `/board` | Create a card, write a short Brief, drag it through columns |
| **Objectives** | `/objectives` | Add a quarterly Objective and Key Results; link board work to a KR |

Starter prompts appear in empty threads so you can try specialists without reading the API docs.

---

## What works with zero extra config

With only an LLM key, you already have:

- **Reasoning agents** — each specialist thinks step by step and can use tools when you add integrations later
- **Native Kanban** — no Jira required; the internal board is the default work surface
- **OKR scoreboard** — Objectives and Key Results feed context into Product and Chief of Staff sessions
- **MCP + HTTP API** — same tools as chat, callable from Cursor, Claude, or `curl` at `http://localhost:8080/mcp`

Specialists that need external data (GA4 reports, PR review, Jira automation) stay available in chat but need the corresponding env vars — add them when you're ready.

---

## Optional: enable one specialist at a time

Re-run the wizard whenever you want to add capabilities:

```bash
python scripts/setup.py
```

Or paste keys into `.env` by hand. Suggested order after the MVP:

1. **CTO** — `GITHUB_TOKEN` for PR summaries and review ([details](../README.md#1-development-workflow))
2. **Product** — board + Objectives already work; add Jira only if you already use it
3. **Marketing** — GA4 property + Google service account
4. **Production** — deploy to Google Cloud Run ([deploy tutorial](../README.md#tutorial-deploy-your-first-bigas-server))

After each change, restart: `docker compose up --build`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Port 8080 in use | Stop the other process or set `PORT=8081` in `.env` and map the port in `docker-compose.yml` |
| Chat returns errors | Confirm `GEMINI_API_KEY` or `OPENAI_API_KEY` is set in `.env` and restart the container |
| Blank UI / 404 on `/` | Use Docker (builds the frontend) or run `npm run build` in `frontend/` before `python run_core.py` |
| Setup wizard cancelled | Re-run `python scripts/setup.py` — nothing is written until the wizard finishes |

For production Firebase auth, Firestore persistence, and Cloud Run deploy, see the [README](../README.md).

---

## What's next

- **Star and watch** your fork on GitHub to track releases
- **Open an issue** or PR if you extend agents or add providers — see [CONTRIBUTING.md](../CONTRIBUTING.md)
- **Deploy** when you're ready for persistent chat and webhooks: [Tutorial: deploy your first Bigas server](../README.md#tutorial-deploy-your-first-bigas-server)

Bigas is Latin for *team*. Fork it, run it locally in five minutes, and put the virtual staff to work on your next quarter.
