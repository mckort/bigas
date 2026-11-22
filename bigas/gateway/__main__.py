"""Manage outside agents from the command line.

    python -m bigas.gateway connect grok-growth --level propose
    python -m bigas.gateway list
    python -m bigas.gateway explain grok-growth
    python -m bigas.gateway level grok-growth operate
    python -m bigas.gateway limit grok-growth proposals_per_day 40
    python -m bigas.gateway pause grok-growth        # or: resume
    python -m bigas.gateway new-token grok-growth
    python -m bigas.gateway remove grok-growth
    python -m bigas.gateway pause-all                # or: resume-all
    python -m bigas.gateway approvals
    python -m bigas.gateway approve apr_xxx          # or: deny apr_xxx --note "why"

Uses the same storage as the server (Firestore when a Google project is set).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from dotenv import load_dotenv


def _when(ts) -> str:
    if not ts:
        return "never"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _app():
    from app import create_app

    return create_app()


def _tool_names(app) -> list:
    header = app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
    keys = sorted(app.config.get("BIGAS_ACCESS_KEYS") or [])
    with app.test_client() as client:
        data = client.get("/mcp/manifest", headers={header: keys[0]} if keys else {}).get_json() or {}
    return [t["name"] for t in data.get("tools", []) if isinstance(t, dict) and t.get("name")]


def main(argv=None) -> int:
    load_dotenv(override=False)
    from bigas.gateway import policy
    from bigas.gateway import service as gw
    from bigas.mcp_urls import mcp_public_base_url

    parser = argparse.ArgumentParser(prog="python -m bigas.gateway", description="Manage outside agents.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("connect", help="Create an agent and print its token and setup")
    p.add_argument("name")
    p.add_argument("--level", choices=policy.LEVEL_ORDER, default="look")
    sub.add_parser("list", help="List agents")
    p = sub.add_parser("explain", help="What an agent can do")
    p.add_argument("name")
    p = sub.add_parser("level", help="Change an agent's level")
    p.add_argument("name")
    p.add_argument("level", choices=policy.LEVEL_ORDER)
    p = sub.add_parser("limit", help="Override one limit for an agent (omit value to clear)")
    p.add_argument("name")
    p.add_argument("key", choices=sorted(policy.LIMIT_WINDOWS))
    p.add_argument("value", nargs="?", default=None, help="Whole number, or omit to use the level default")
    for cmd in ("pause", "resume", "remove", "new-token"):
        p = sub.add_parser(cmd)
        p.add_argument("name")
    sub.add_parser("pause-all", help="Stop every outside agent")
    sub.add_parser("resume-all")
    sub.add_parser("approvals", help="Pending approval requests")
    p = sub.add_parser("approve")
    p.add_argument("approval_id")
    p = sub.add_parser("deny")
    p.add_argument("approval_id")
    p.add_argument("--note", default="")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "connect":
            agent, token = gw.connect_agent(args.name, args.level, created_by="cli")
            print(f"Connected {agent['id']} with {policy.LEVELS[agent['level']]['label']} access.")
            print(policy.LEVELS[agent["level"]]["summary"])
            print("\nToken (shown once, store it in the agent's secrets):\n")
            print(f"  {token}\n")
            for item in gw.connect_instructions(token, mcp_public_base_url()):
                print(f"--- {item['label']}\n{item['code']}\n")
            if not gw.gateway_enabled():
                print("Note: BIGAS_AGENT_GATEWAY is off on this machine. Turn it on where Bigas runs.")
        elif args.cmd == "list":
            agents = gw.list_agents()
            if not agents:
                print("No outside agents yet. Connect one: python -m bigas.gateway connect NAME --level look")
            for a in agents:
                state = "paused" if a.get("suspended") else policy.LEVELS.get(a.get("level"), {}).get("label", "?")
                print(f"{a['id']:<24} {state:<10} last used {_when(a.get('last_used_at'))}")
            print(f"\nAll outside agents paused: {'yes' if gw.is_paused() else 'no'}")
        elif args.cmd == "explain":
            agent = gw.get_agent(args.name)
            if not agent:
                raise gw.GatewayError(f"No agent called {args.name}.")
            groups = policy.explain(agent, _tool_names(_app()))
            print(f"{agent['id']}: {policy.LEVELS[agent['level']]['label']}. {policy.LEVELS[agent['level']]['summary']}\n")
            print("Runs directly:\n  " + ("\n  ".join(groups["runs"]) or "(none)"))
            print("\nNeeds your approval:\n  " + ("\n  ".join(groups["needs_approval"]) or "(none)"))
            print(f"\nNot available: {len(groups['not_available'])} tools")
        elif args.cmd == "level":
            agent = gw.update_agent(args.name, level=args.level)
            print(f"{agent['id']} now has {policy.LEVELS[agent['level']]['label']} access.")
        elif args.cmd == "limit":
            agent = gw.get_agent(args.name)
            if not agent:
                raise gw.GatewayError(f"No agent called {args.name}.")
            limits = dict(agent.get("limits") or {})
            if args.value is None:
                limits.pop(args.key, None)
            else:
                limits[args.key] = int(args.value)
            agent = gw.update_agent(args.name, limits=limits)
            effective = policy.level_limits(agent)
            label = policy.LIMIT_LABELS[args.key]
            if args.value is None:
                print(f"{agent['id']}: {label} back to level default ({effective.get(args.key)}).")
            else:
                print(f"{agent['id']}: {label} set to {effective.get(args.key)}.")
        elif args.cmd in ("pause", "resume"):
            gw.update_agent(args.name, suspended=args.cmd == "pause")
            print(f"{args.name} {'paused' if args.cmd == 'pause' else 'resumed'}.")
        elif args.cmd == "remove":
            gw.remove_agent(args.name)
            print(f"Removed {args.name}. Its token no longer works.")
        elif args.cmd == "new-token":
            token = gw.new_token(args.name)
            print(f"New token for {args.name} (the old one stopped working):\n\n  {token}\n")
        elif args.cmd in ("pause-all", "resume-all"):
            gw.set_paused(args.cmd == "pause-all", by="cli")
            print("All outside agents paused." if args.cmd == "pause-all" else "Outside agents resumed.")
        elif args.cmd == "approvals":
            items = gw.pending_approvals()
            if not items:
                print("Nothing waiting for you.")
            for a in items:
                print(f"{a['id']}  {a['agent_name']} → {a['tool']}  ({_when(a['created_at'])})")
                if a.get("intent"):
                    print(f"    why: {a['intent']}")
                print(f"    args: {json.dumps(a.get('arguments') or {}, ensure_ascii=False)[:300]}")
        elif args.cmd == "approve":
            app = _app()
            with app.app_context():
                a = gw.approve_approval(args.approval_id, by="cli", dispatch=gw.dispatch_with_owner_key(app))
            print(f"{a['status']}: {(a.get('result') or '')[:500]}")
        elif args.cmd == "deny":
            gw.deny_approval(args.approval_id, by="cli", note=args.note)
            print("Denied.")
    except gw.GatewayError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
