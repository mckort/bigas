# Outside agents (agent gateway)

Let Grok, a script, or any other MCP client use Bigas with limits you choose. Your own Cursor and Claude keep working exactly as before.

## Quick start (about 3 minutes)

1. Turn it on where Bigas runs and redeploy:

   ```bash
   BIGAS_AGENT_GATEWAY=on
   BIGAS_ACCESS_MODE=restricted     # required; Bigas refuses to start the gateway in open mode
   BIGAS_ACCESS_KEYS=...            # your own key, unchanged
   ```

2. In the cockpit, find **Outside agents** and press **Connect**. Name the agent (`grok-growth`), pick a level, press **Connect**.
3. Copy the token and the setup for your client (Grok API, Grok Build, Claude Code, Cursor or curl). Then ask the agent to call `gateway_whoami`. It answers with what it may do.

Prefer the terminal? `python -m bigas.gateway connect grok-growth --level propose` prints the same.

## Levels

Pick one level per agent. Start low; you can change the level later without a new token.

| Level | The agent can | Needs you for | Limits |
|---|---|---|---|
| **Look** | Read reports, analytics, tickets, deploy status | nothing | 300 calls/hour |
| **Propose** | Look, plus create tickets, PRs, drafts, PR reviews | nothing (all reviewable or reversible) | + 20 proposals/day |
| **Operate** | Propose, plus ask to deploy, release, cherry-pick | each of those, one by one | + 10 approval requests/day |

Every tool has a tier: **Look**, **Propose**, **Act** or **Never** (`bigas/gateway/tiers.py`). A new tool that is not classified counts as **Act**, so it waits for you until someone gives it a tier. **Never** tools (cleanup jobs, OAuth code exchange, webhooks) are off for every outside agent.

## Approvals

When an Operate agent calls an Act tool, Bigas stores the call exactly as sent and answers the agent at once with an `approval_id`. You see the request at the top of **Needs you**, with the agent's reason (agents can add an `_intent` argument), what it will send, and **Approve and run** or **Deny**. Discord gets a short message if `DISCORD_WEBHOOK_URL_GATEWAY` (or the Chief of Staff or Product webhook) is set.

- Approve runs the stored call, not a new one. If it was changed after it was made, Bigas refuses to run it.
- Pause, level and limits are checked again when you approve.
- Requests expire after 24 hours (2 hours for deploys and hotfixes).
- The agent checks with `get_approval_status` and sees the result, or your note if you denied it.

## What the agent sees

- `tools/list` shows only the tools its level allows, plus three gateway tools: `gateway_whoami`, `get_approval_status`, `list_my_approvals`.
- A refused call says why in one sentence (for example: "create_ticket is a Propose tool and grok-growth has Look access. The owner can raise it to Propose in the cockpit.").
- Agent tokens work only on `POST /mcp`, in the `Authorization` header. They are refused on `/mcp/tools/*`, the cockpit API and in the URL.

## Your own clients

Your access key (Cursor, Cloud Scheduler) and Claude logins through OAuth are owner credentials. They run every tool directly, nothing is held, no limits apply, and they keep working when outside agents are paused.

## Stop everything

**Pause all** in the Outside agents section (or `python -m bigas.gateway pause-all`) refuses every outside-agent call and cancels waiting requests. Pause one agent from its page, or **Remove** it to delete it and its token. **New token** replaces a leaked token; the old one stops working at once.

## Command line

```bash
python -m bigas.gateway connect NAME --level look|propose|operate
python -m bigas.gateway list
python -m bigas.gateway explain NAME          # what it runs directly and what needs you
python -m bigas.gateway level NAME operate
python -m bigas.gateway pause NAME | resume NAME | remove NAME | new-token NAME
python -m bigas.gateway pause-all | resume-all
python -m bigas.gateway approvals | approve ID | deny ID --note "why"
```

## Storage and retention

Firestore collections `gateway_agents` (with a `gateway_audit` subcollection per agent), `gateway_tokens` (SHA-256 of tokens only), `gateway_approvals`, `gateway_counters`, `gateway_settings`. Activity is kept for 30 days. Let Firestore delete old rows with TTL policies:

```bash
gcloud firestore fields ttls update expire_at --collection-group=gateway_audit --enable-ttl
gcloud firestore fields ttls update expire_at --collection-group=gateway_counters --enable-ttl
```

## Not in this version

Per-argument rules (for example "only this repo"), approve-for-24-hours grants, and proxying other MCP servers (Accounted, GitHub) through the same levels. Advanced: an agent's tool list can be narrowed with `allow_tools` / `deny_tools` through `PATCH /api/gateway/agents/<id>`.
