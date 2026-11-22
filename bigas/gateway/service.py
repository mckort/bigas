"""Agent gateway: who is calling, may they, and what happens if they may not.

Owner credentials (``BIGAS_ACCESS_KEYS``, OAuth logins from the cockpit's
allowed accounts) work exactly as before. Outside agents get a ``bga_`` token
tied to one access level (look, propose, operate). Every agent call is
checked, counted and logged; Act tools wait in the approval queue.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from bigas.gateway import policy
from bigas.gateway.policy import ALLOW, DENY, LEVELS, OWNER, Decision
from bigas.gateway.store import get_gateway_store, window_key
from bigas.gateway.tiers import TIER_LABELS, tool_tier

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "bga_"
CACHE_SECONDS = 10
APPROVAL_TTL_SECONDS = 24 * 3600
SHORT_APPROVAL_TTL_SECONDS = 2 * 3600
SHORT_TTL_TOOLS = {"trigger_deployment", "cherry_pick_hotfix"}
MAX_RESULT_CHARS = 20000
INTENT_ARG = "_intent"

GATEWAY_TOOLS = [
    {
        "name": "gateway_whoami",
        "description": (
            "Who you are in Bigas: your access level, which tools run directly, which need the "
            "owner's approval, your limits used today, and your open approval requests. "
            "Call this first if you are unsure what you may do."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_approval_status",
        "description": (
            "Check an approval request you got from a tool that needs the owner's approval. "
            "Returns pending, approved, denied (with the owner's note), expired, or the tool's result once it ran."
        ),
        "parameters": {
            "type": "object",
            "properties": {"approval_id": {"type": "string"}},
            "required": ["approval_id"],
        },
    },
    {
        "name": "list_my_approvals",
        "description": "List your recent approval requests and their status (newest first).",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "succeeded", "failed", "denied", "expired", "cancelled"],
                }
            },
        },
    },
]
GATEWAY_TOOL_NAMES = {t["name"] for t in GATEWAY_TOOLS}


class GatewayError(ValueError):
    """A request the owner made that cannot be done (shown to them as is)."""


# ---------------------------------------------------------------- config


def gateway_enabled() -> bool:
    return (os.environ.get("BIGAS_AGENT_GATEWAY") or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_gateway_config(access_mode: str) -> None:
    """Refuse to start an enabled gateway that agents could walk around."""
    if gateway_enabled() and access_mode != "restricted":
        raise ValueError(
            "BIGAS_AGENT_GATEWAY is on but BIGAS_ACCESS_MODE is not 'restricted'. "
            "Set BIGAS_ACCESS_MODE=restricted (with BIGAS_ACCESS_KEYS) or turn the gateway off."
        )


def _owner_keys() -> set:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            keys = current_app.config.get("BIGAS_ACCESS_KEYS")
            if isinstance(keys, str):
                return {k.strip() for k in keys.split(",") if k.strip()}
            if keys:
                return set(keys)
    except RuntimeError:
        pass
    raw = os.environ.get("BIGAS_ACCESS_KEYS") or ""
    return {k.strip() for k in raw.split(",") if k.strip()}


# ---------------------------------------------------------------- cache

_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, load: Callable[[], Any]) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = load()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _forget(*keys: str) -> None:
    with _cache_lock:
        for key in keys:
            _cache.pop(key, None)


def reset_cache_for_tests() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------- identity


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_agent_token(credential: Optional[str]) -> bool:
    return bool(credential) and str(credential).startswith(TOKEN_PREFIX)


def owner_agent(agent_id: str, name: str) -> Dict[str, Any]:
    return {"id": agent_id, "name": name, "kind": OWNER}


def resolve_agent(credential: Optional[str]) -> Optional[Dict[str, Any]]:
    """Map a credential to the agent it belongs to. None means not a valid credential."""
    if not credential:
        return None
    if is_agent_token(credential):
        token_hash = _sha256(credential)
        agent_id = _cached(f"tok:{token_hash}", lambda: get_gateway_store().get_token(token_hash))
        if not agent_id:
            return None
        agent = get_agent(agent_id)
        if not agent or agent.get("token_hash") != token_hash:
            return None
        return agent
    if credential in _owner_keys():
        return owner_agent("owner-key", "You (access key)")
    try:
        from bigas.oauth.service import verify_access_token

        payload = verify_access_token(credential)
    except Exception:
        payload = None
    if payload:
        email = str(payload.get("email") or "").strip()
        return owner_agent(f"owner-oauth:{email or 'claude'}", email or "You (Claude)")
    return None


# ---------------------------------------------------------------- agents


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:40]


def _new_token(agent_id: str) -> str:
    return f"{TOKEN_PREFIX}{agent_id.replace('-', '')[:12]}_{secrets.token_urlsafe(32)}"


def get_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    return _cached(f"agent:{agent_id}", lambda: get_gateway_store().get_agent(agent_id))


def list_agents() -> List[Dict[str, Any]]:
    agents = get_gateway_store().list_agents()
    agents.sort(key=lambda a: a.get("created_at") or 0)
    return agents


def _save_agent(agent: Dict[str, Any]) -> None:
    get_gateway_store().put_agent(agent)
    _forget(f"agent:{agent['id']}")


def _check_level(level: str) -> str:
    level = (level or "").strip().lower()
    if level not in LEVELS:
        raise GatewayError(f"Level must be one of: {', '.join(policy.LEVEL_ORDER)}.")
    return level


def connect_agent(name: str, level: str, *, created_by: str = "") -> Tuple[Dict[str, Any], str]:
    """Create an agent and its token. The token is returned once and never stored."""
    name = (name or "").strip()
    agent_id = _slug(name)
    if not agent_id:
        raise GatewayError("Give the agent a name, for example grok-growth.")
    level = _check_level(level)
    store = get_gateway_store()
    if store.get_agent(agent_id):
        raise GatewayError(f"An agent called {agent_id} already exists. Pick another name.")
    token = _new_token(agent_id)
    token_hash = _sha256(token)
    now = time.time()
    agent = {
        "id": agent_id,
        "name": name,
        "kind": "agent",
        "level": level,
        "suspended": False,
        "allow_tools": [],
        "deny_tools": [],
        "limits": {},
        "token_hash": token_hash,
        "token_hint": token[-4:],
        "token_created_at": now,
        "created_at": now,
        "created_by": created_by,
        "last_used_at": None,
    }
    store.put_token(token_hash, agent_id)
    _save_agent(agent)
    return agent, token


def new_token(agent_id: str) -> str:
    """Replace an agent's token. The old one stops working at once."""
    agent = _require_agent(agent_id)
    store = get_gateway_store()
    old = agent.get("token_hash")
    token = _new_token(agent_id)
    agent["token_hash"] = _sha256(token)
    agent["token_hint"] = token[-4:]
    agent["token_created_at"] = time.time()
    store.put_token(agent["token_hash"], agent_id)
    _save_agent(agent)
    if old:
        store.delete_token(old)
        _forget(f"tok:{old}")
    return token


def _require_agent(agent_id: str) -> Dict[str, Any]:
    agent = get_gateway_store().get_agent(agent_id)
    if not agent:
        raise GatewayError(f"No agent called {agent_id}.")
    return agent


def _tool_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value if str(v).strip()]


def update_agent(agent_id: str, **changes: Any) -> Dict[str, Any]:
    agent = _require_agent(agent_id)
    if changes.get("level") is not None:
        agent["level"] = _check_level(changes["level"])
    if changes.get("name") is not None and str(changes["name"]).strip():
        agent["name"] = str(changes["name"]).strip()
    if changes.get("allow_tools") is not None:
        agent["allow_tools"] = _tool_list(changes["allow_tools"])
    if changes.get("deny_tools") is not None:
        agent["deny_tools"] = _tool_list(changes["deny_tools"])
    if changes.get("suspended") is not None:
        agent["suspended"] = bool(changes["suspended"])
        if agent["suspended"]:
            _cancel_pending(agent_id=agent_id, note="Agent paused by the owner.")
    _save_agent(agent)
    return agent


def remove_agent(agent_id: str) -> None:
    agent = _require_agent(agent_id)
    store = get_gateway_store()
    _cancel_pending(agent_id=agent_id, note="Agent removed by the owner.")
    if agent.get("token_hash"):
        store.delete_token(agent["token_hash"])
        _forget(f"tok:{agent['token_hash']}")
    store.delete_agent(agent_id)
    _forget(f"agent:{agent_id}")


# ---------------------------------------------------------------- pause switch


def is_paused() -> bool:
    return bool(_cached("settings", lambda: get_gateway_store().get_settings()).get("paused"))


def set_paused(paused: bool, *, by: str = "") -> Dict[str, Any]:
    settings = dict(get_gateway_store().get_settings())
    settings.update({"paused": bool(paused), "paused_by": by, "paused_at": time.time()})
    get_gateway_store().put_settings(settings)
    _forget("settings")
    if paused:
        _cancel_pending(note="Outside agents paused by the owner.")
    return settings


# ---------------------------------------------------------------- limits


def _used(agent: Dict[str, Any], now: Optional[float] = None) -> Dict[str, int]:
    store = get_gateway_store()
    out = {}
    for name, seconds in policy.LIMIT_WINDOWS.items():
        out[name] = store.get_counter(window_key(name, agent["id"], seconds, now))
    return out


def count_usage(agent: Dict[str, Any], counters: List[str]) -> None:
    store = get_gateway_store()
    for name in counters:
        store.incr_counter(window_key(name, agent["id"], policy.LIMIT_WINDOWS[name]))


def check(agent: Dict[str, Any], tool: str) -> Decision:
    if agent.get("kind") == OWNER:
        return Decision(ALLOW, tool_tier(tool))
    return policy.decide(agent, tool, paused=is_paused(), used=_used(agent))


# ---------------------------------------------------------------- audit


def audit(
    agent: Dict[str, Any],
    tool: str,
    decision: str,
    *,
    tier: str = "",
    code: str = "",
    status: Optional[int] = None,
    approval_id: str = "",
    ms: Optional[int] = None,
    arguments: Optional[Dict[str, Any]] = None,
) -> None:
    if agent.get("kind") == OWNER:
        return  # owner calls stay as today; the gateway logs outside agents
    now = time.time()
    entry = {
        "ts": now,
        "tool": tool,
        "tier": tier or tool_tier(tool),
        "decision": decision,
        "code": code,
        "status": status,
        "approval_id": approval_id,
        "ms": ms,
        "args": sorted((arguments or {}).keys()),
    }
    try:
        get_gateway_store().add_audit(agent["id"], entry)
        last = agent.get("last_used_at") or 0
        if now - last > 60:
            fresh = get_gateway_store().get_agent(agent["id"])
            if fresh:
                fresh["last_used_at"] = now
                fresh["last_tool"] = tool
                _save_agent(fresh)
    except Exception:
        logger.exception("Gateway audit write failed for %s", agent.get("id"))


def recent_activity(agent_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    return get_gateway_store().list_audit(agent_id, limit=limit)


# ---------------------------------------------------------------- approvals


def args_hash(arguments: Dict[str, Any]) -> str:
    return _sha256(json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, default=str))


def split_intent(arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    args = dict(arguments or {})
    intent = str(args.pop(INTENT_ARG, "") or "").strip()[:500]
    return args, intent


def create_approval(
    agent: Dict[str, Any],
    tool_entry: Dict[str, Any],
    arguments: Dict[str, Any],
    *,
    intent: str = "",
    decision: Optional[Decision] = None,
) -> Dict[str, Any]:
    tool = tool_entry.get("name") or ""
    now = time.time()
    ttl = SHORT_APPROVAL_TTL_SECONDS if tool in SHORT_TTL_TOOLS else APPROVAL_TTL_SECONDS
    approval = {
        "id": f"apr_{secrets.token_urlsafe(9)}",
        "agent_id": agent["id"],
        "agent_name": agent.get("name") or agent["id"],
        "tool": tool,
        "tool_path": tool_entry.get("path") or "",
        "tool_method": (tool_entry.get("method") or "POST").upper(),
        "arguments": arguments or {},
        "args_sha256": args_hash(arguments or {}),
        "intent": intent,
        "tier": tool_tier(tool),
        "status": "pending",
        "created_at": now,
        "expires_at": now + ttl,
        "decided_at": None,
        "decided_by": "",
        "note": "",
        "result_status": None,
        "result": None,
    }
    get_gateway_store().put_approval(approval)
    if decision is not None:
        count_usage(agent, decision.counters)
    _notify_owner(approval)
    return approval


def _expire_if_due(approval: Dict[str, Any]) -> Dict[str, Any]:
    if approval.get("status") == "pending" and time.time() > (approval.get("expires_at") or 0):
        approval["status"] = "expired"
        approval["decided_at"] = approval.get("expires_at")
        get_gateway_store().put_approval(approval)
    return approval


def get_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    approval = get_gateway_store().get_approval(approval_id)
    return _expire_if_due(approval) if approval else None


def pending_approvals() -> List[Dict[str, Any]]:
    out = []
    for a in get_gateway_store().list_approvals(status="pending"):
        a = _expire_if_due(a)
        if a["status"] == "pending":
            out.append(a)
    out.sort(key=lambda a: a.get("created_at") or 0)
    return out


def _cancel_pending(*, agent_id: Optional[str] = None, note: str = "") -> None:
    for a in get_gateway_store().list_approvals(status="pending", agent_id=agent_id, limit=500):
        a["status"] = "cancelled"
        a["decided_at"] = time.time()
        a["note"] = note
        get_gateway_store().put_approval(a)


def deny_approval(approval_id: str, *, by: str = "", note: str = "") -> Dict[str, Any]:
    approval = get_approval(approval_id)
    if not approval:
        raise GatewayError("That request no longer exists.")
    if approval["status"] != "pending":
        raise GatewayError(f"That request is already {approval['status']}.")
    approval.update(
        {"status": "denied", "decided_at": time.time(), "decided_by": by, "note": (note or "").strip()[:500]}
    )
    get_gateway_store().put_approval(approval)
    agent = get_gateway_store().get_agent(approval["agent_id"])
    if agent:
        audit(agent, approval["tool"], "denied_by_owner", approval_id=approval_id)
    return approval


def approve_approval(
    approval_id: str,
    *,
    by: str = "",
    dispatch: Callable[[str, str, Dict[str, Any]], Tuple[int, Any, str]],
) -> Dict[str, Any]:
    """Approve and run the frozen call now. Limits are checked again first."""
    approval = get_approval(approval_id)
    if not approval:
        raise GatewayError("That request no longer exists.")
    if approval["status"] != "pending":
        raise GatewayError(f"That request is already {approval['status']}.")
    if args_hash(approval.get("arguments") or {}) != approval.get("args_sha256"):
        raise GatewayError("The stored request was changed after it was made. Deny it.")
    agent = get_gateway_store().get_agent(approval["agent_id"])
    if not agent:
        raise GatewayError("The agent behind this request was removed.")
    recheck = policy.decide(agent, approval["tool"], paused=is_paused(), used={})
    if recheck.outcome == DENY:
        raise GatewayError(f"Can't run it now: {recheck.reason}")

    approval.update({"status": "executing", "decided_at": time.time(), "decided_by": by})
    get_gateway_store().put_approval(approval)
    started = time.time()
    try:
        status_code, _json, text = dispatch(
            approval["tool_path"], approval["tool_method"], approval.get("arguments") or {}
        )
        ok = status_code < 400
        approval.update(
            {
                "status": "succeeded" if ok else "failed",
                "result_status": status_code,
                "result": (text or "")[:MAX_RESULT_CHARS],
            }
        )
    except Exception as exc:  # the tool blew up; keep the request honest
        logger.exception("Approved gateway call %s failed", approval_id)
        approval.update({"status": "failed", "result_status": 500, "result": f"Error: {exc}"[:2000]})
    get_gateway_store().put_approval(approval)
    audit(
        agent,
        approval["tool"],
        "approved",
        status=approval.get("result_status"),
        approval_id=approval_id,
        ms=int((time.time() - started) * 1000),
        arguments=approval.get("arguments"),
    )
    return approval


def approval_for_agent(approval: Dict[str, Any]) -> Dict[str, Any]:
    """What an agent sees about its own request."""
    out = {
        "approval_id": approval["id"],
        "tool": approval["tool"],
        "status": approval["status"],
        "created_at": approval.get("created_at"),
        "expires_at": approval.get("expires_at"),
    }
    if approval.get("note"):
        out["note"] = approval["note"]
    if approval["status"] in {"succeeded", "failed"}:
        out["result"] = approval.get("result")
    out["message"] = {
        "pending": "Waiting for the owner. Check again later.",
        "executing": "Approved and running now.",
        "succeeded": "Approved and done.",
        "failed": "Approved, but the tool failed. See result.",
        "denied": "The owner said no." + (f" Note: {approval['note']}" if approval.get("note") else ""),
        "expired": "Nobody approved it in time. Ask again if it still matters.",
        "cancelled": approval.get("note") or "Cancelled.",
    }.get(approval["status"], "")
    return out


def pending_reply(approval: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "pending_approval",
        "approval_id": approval["id"],
        "expires_at": approval["expires_at"],
        "poll_with": "get_approval_status",
        "message": (
            f"{approval['tool']} needs the owner's approval. It runs exactly as you sent it once they "
            f"approve. Check with get_approval_status(approval_id='{approval['id']}')."
        ),
    }


def _notify_owner(approval: Dict[str, Any]) -> None:
    url = (
        os.environ.get("DISCORD_WEBHOOK_URL_GATEWAY")
        or os.environ.get("DISCORD_WEBHOOK_URL_CHIEF")
        or os.environ.get("DISCORD_WEBHOOK_URL_PRODUCT")
        or ""
    ).strip()
    if not url:
        return
    try:
        from bigas.discord_webhook import post_to_discord
        from bigas.mcp_urls import mcp_public_base_url

        lines = [f"🔐 **{approval['agent_name']}** asks to run `{approval['tool']}`."]
        if approval.get("intent"):
            lines.append(f"Why: {approval['intent']}")
        lines.append(f"Approve or deny in the cockpit: {mcp_public_base_url()}/cockpit")
        post_to_discord(url, "\n".join(lines), mirror_chat=False)
    except Exception:
        logger.warning("Gateway approval notification failed", exc_info=True)


# ---------------------------------------------------------------- agent-facing tools


def whoami(agent: Dict[str, Any], tool_names: List[str]) -> Dict[str, Any]:
    if agent.get("kind") == OWNER:
        return {
            "agent": agent.get("name"),
            "level": "owner",
            "summary": "You are the owner. Every tool runs directly; nothing needs approval.",
        }
    level = LEVELS.get(agent.get("level") or "", {})
    groups = policy.explain(agent, tool_names)
    limits = policy.level_limits(agent)
    used = _used(agent)
    pending = [
        approval_for_agent(a)
        for a in get_gateway_store().list_approvals(agent_id=agent["id"], status="pending", limit=20)
    ]
    return {
        "agent": agent.get("name"),
        "level": agent.get("level"),
        "summary": level.get("summary", ""),
        "paused": bool(agent.get("suspended")) or is_paused(),
        "runs_directly": groups["runs"],
        "needs_owner_approval": groups["needs_approval"],
        "limits": {
            policy.LIMIT_LABELS[k]: {"used": used.get(k, 0), "max": v} for k, v in limits.items()
        },
        "open_approval_requests": pending,
        "tips": [
            f"Add an '{INTENT_ARG}' argument (one sentence) to any call to tell the owner why.",
            "Tools that need approval return an approval_id at once; poll get_approval_status.",
        ],
    }


def handle_gateway_tool(
    agent: Dict[str, Any], name: str, arguments: Dict[str, Any], tool_names: List[str]
) -> Tuple[Dict[str, Any], bool]:
    """Run one of the gateway's own tools. Returns (payload, is_error)."""
    if name == "gateway_whoami":
        return whoami(agent, tool_names), False
    if name == "get_approval_status":
        approval_id = str((arguments or {}).get("approval_id") or "").strip()
        approval = get_approval(approval_id) if approval_id else None
        if not approval or (agent.get("kind") != OWNER and approval.get("agent_id") != agent.get("id")):
            return {"error": "No approval request with that id for you."}, True
        return approval_for_agent(approval), False
    if name == "list_my_approvals":
        status = (arguments or {}).get("status") or None
        agent_id = None if agent.get("kind") == OWNER else agent["id"]
        items = get_gateway_store().list_approvals(agent_id=agent_id, status=status, limit=25)
        return {"approvals": [approval_for_agent(_expire_if_due(a)) for a in items]}, False
    return {"error": f"Unknown gateway tool {name}."}, True


# ---------------------------------------------------------------- cockpit views


def agent_view(agent: Dict[str, Any], tool_names: Optional[List[str]] = None) -> Dict[str, Any]:
    level = LEVELS.get(agent.get("level") or "", {})
    out = {
        "id": agent["id"],
        "name": agent.get("name") or agent["id"],
        "level": agent.get("level"),
        "level_label": level.get("label", ""),
        "level_summary": level.get("summary", ""),
        "paused": bool(agent.get("suspended")),
        "allow_tools": agent.get("allow_tools") or [],
        "deny_tools": agent.get("deny_tools") or [],
        "token_hint": agent.get("token_hint") or "",
        "created_at": agent.get("created_at"),
        "last_used_at": agent.get("last_used_at"),
        "last_tool": agent.get("last_tool") or "",
        "limits": [],
    }
    used = _used(agent)
    out["limits"] = [
        {"label": policy.LIMIT_LABELS[k], "used": used.get(k, 0), "max": v}
        for k, v in policy.level_limits(agent).items()
    ]
    if tool_names is not None:
        out["tools"] = policy.explain(agent, tool_names)
    return out


def approval_view(approval: Dict[str, Any]) -> Dict[str, Any]:
    tier = approval.get("tier") or tool_tier(approval.get("tool") or "")
    return {
        "id": approval["id"],
        "agent_id": approval["agent_id"],
        "agent_name": approval.get("agent_name") or approval["agent_id"],
        "tool": approval["tool"],
        "tier_label": TIER_LABELS.get(tier, tier),
        "intent": approval.get("intent") or "",
        "arguments": approval.get("arguments") or {},
        "status": approval["status"],
        "created_at": approval.get("created_at"),
        "expires_at": approval.get("expires_at"),
        "decided_at": approval.get("decided_at"),
        "note": approval.get("note") or "",
        "result": approval.get("result"),
    }


def connect_instructions(token: str, base_url: str) -> List[Dict[str, str]]:
    """Copy-paste setup for common MCP clients."""
    url = f"{base_url.rstrip('/')}/mcp"
    return [
        {
            "id": "grok-api",
            "label": "Grok (xAI API, Python)",
            "code": (
                "from xai_sdk import Client\n"
                "from xai_sdk.tools import mcp\n\n"
                "client = Client()  # reads XAI_API_KEY\n"
                "chat = client.chat.create(\n"
                '    model="grok-4",\n'
                f'    tools=[mcp(server_url="{url}", server_label="bigas",\n'
                f'               authorization="Bearer {token}")],\n'
                ")"
            ),
        },
        {
            "id": "grok-build",
            "label": "Grok Build (CLI)",
            "code": f'grok mcp add --transport http bigas {url} --header "Authorization: Bearer {token}"',
        },
        {
            "id": "claude-code",
            "label": "Claude Code",
            "code": f'claude mcp add --transport http bigas {url} --header "Authorization: Bearer {token}"',
        },
        {
            "id": "cursor",
            "label": "Cursor (mcp.json)",
            "code": json.dumps(
                {"mcpServers": {"bigas": {"url": url, "headers": {"Authorization": f"Bearer {token}"}}}},
                indent=2,
            ),
        },
        {
            "id": "curl",
            "label": "Test with curl",
            "code": (
                f"curl -s {url} -H 'Authorization: Bearer {token}' -H 'Content-Type: application/json' \\\n"
                "  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\","
                "\"params\":{\"name\":\"gateway_whoami\",\"arguments\":{}}}'"
            ),
        },
    ]


def dispatch_with_owner_key(app) -> Callable[[str, str, Dict[str, Any]], Tuple[int, Any, str]]:
    """Run a tool's HTTP handler inside this process with an owner credential."""

    def _dispatch(path: str, method: str, arguments: Dict[str, Any]) -> Tuple[int, Any, str]:
        header = app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
        keys = sorted(app.config.get("BIGAS_ACCESS_KEYS") or [])
        headers = {header: keys[0]} if keys else {}
        with app.test_client() as client:
            if (method or "POST").upper() == "GET":
                resp = client.open(path, method="GET", headers=headers, query_string=arguments)
            else:
                resp = client.open(path, method=method or "POST", headers=headers, json=arguments)
        text = resp.get_data(as_text=True)
        body = resp.get_json(silent=True) if resp.is_json else None
        if isinstance(body, dict) and isinstance(body.get("summary"), str) and body["summary"].strip():
            text = body["summary"].strip()
        elif body is not None:
            text = json.dumps(body, ensure_ascii=False)
        return resp.status_code, body, text

    return _dispatch
