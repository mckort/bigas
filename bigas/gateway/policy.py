"""Access levels and the policy decision for one tool call.

``decide`` is a pure function: it gets the agent, the tool, the gateway state
and today's counters, and returns allow, deny or hold with a plain-language
reason the agent can act on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Dict, Iterable, List, Optional

from bigas.gateway.tiers import ACT, LOOK, NEVER, PROPOSE, TIER_LABELS, tool_tier

ALLOW = "allow"
DENY = "deny"
HOLD = "hold"

OWNER = "owner"

# Level → which tiers run, which are held, and the default limits.
LEVELS: Dict[str, Dict[str, Any]] = {
    "look": {
        "label": "Look",
        "summary": "Reads reports, analytics, tickets and deploy status. Changes nothing.",
        "run": {LOOK},
        "hold": set(),
        "limits": {"calls_per_hour": 300},
    },
    "propose": {
        "label": "Propose",
        "summary": "Look, plus creates tickets, PRs and drafts you can review. Never deploys, sends or publishes.",
        "run": {LOOK, PROPOSE},
        "hold": set(),
        "limits": {"calls_per_hour": 300, "proposals_per_day": 20},
    },
    "operate": {
        "label": "Operate",
        "summary": "Propose, plus can ask to deploy, release or send. Each of those waits for your approval.",
        "run": {LOOK, PROPOSE},
        "hold": {ACT},
        "limits": {"calls_per_hour": 300, "proposals_per_day": 20, "approval_requests_per_day": 10},
    },
}

LEVEL_ORDER = ["look", "propose", "operate"]

LIMIT_LABELS = {
    "calls_per_hour": "calls per hour",
    "proposals_per_day": "proposals per day",
    "approval_requests_per_day": "approval requests per day",
}

LIMIT_WINDOWS = {
    "calls_per_hour": 3600,
    "proposals_per_day": 86400,
    "approval_requests_per_day": 86400,
}


@dataclass
class Decision:
    outcome: str
    tier: str
    reason: str = ""
    code: str = ""
    counters: List[str] = field(default_factory=list)  # limits this call counts against when it runs

    @property
    def allowed(self) -> bool:
        return self.outcome == ALLOW


def level_limits(agent: Dict[str, Any]) -> Dict[str, int]:
    level = LEVELS.get(agent.get("level") or "", {})
    limits = dict(level.get("limits") or {})
    for key, value in (agent.get("limits") or {}).items():
        if key in LIMIT_WINDOWS and isinstance(value, int) and value >= 0:
            limits[key] = value
    return limits


def _matches(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch(name, p) for p in patterns or [])


def tool_visible(agent: Dict[str, Any], tool: str) -> bool:
    """Whether the tool shows in this agent's tools/list (it may still be held)."""
    if agent.get("kind") == OWNER:
        return True
    level = LEVELS.get(agent.get("level") or "")
    if not level:
        return False
    tier = tool_tier(tool)
    if tier not in level["run"] and tier not in level["hold"]:
        return False
    if _matches(tool, agent.get("deny_tools") or []):
        return False
    allow = agent.get("allow_tools") or []
    if allow and not _matches(tool, allow):
        return False
    return True


def decide(
    agent: Dict[str, Any],
    tool: str,
    *,
    paused: bool = False,
    used: Optional[Dict[str, int]] = None,
) -> Decision:
    """Allow, deny or hold one call. ``used`` maps limit name → count in its current window."""
    tier = tool_tier(tool)
    name = agent.get("name") or agent.get("id") or "this agent"

    if agent.get("kind") == OWNER:
        return Decision(ALLOW, tier)

    if paused:
        return Decision(
            DENY, tier, f"Outside agents are paused by the owner. Try again later.", "paused"
        )
    if agent.get("suspended"):
        return Decision(DENY, tier, f"{name} is paused by the owner.", "agent_paused")

    level_key = agent.get("level") or ""
    level = LEVELS.get(level_key)
    if not level:
        return Decision(DENY, tier, f"{name} has no access level set.", "no_level")

    if tier == NEVER:
        return Decision(DENY, tier, f"{tool} is not available to outside agents.", "never")
    if _matches(tool, agent.get("deny_tools") or []):
        return Decision(DENY, tier, f"The owner has turned off {tool} for {name}.", "denied_tool")
    allow = agent.get("allow_tools") or []
    if allow and not _matches(tool, allow):
        return Decision(DENY, tier, f"{tool} is not on {name}'s tool list.", "not_allowed")

    if tier not in level["run"] and tier not in level["hold"]:
        needed = "operate" if tier == ACT else "propose"
        return Decision(
            DENY,
            tier,
            f"{tool} is a {TIER_LABELS[tier]} tool and {name} has {level['label']} access. "
            f"The owner can raise it to {LEVELS[needed]['label']} in the cockpit.",
            "level_too_low",
        )

    limits = level_limits(agent)
    used = used or {}
    counters = ["calls_per_hour"]
    if tier == PROPOSE:
        counters.append("proposals_per_day")
    if tier in level["hold"]:
        counters = ["calls_per_hour", "approval_requests_per_day"]
    for key in counters:
        cap = limits.get(key)
        if cap is not None and used.get(key, 0) >= cap:
            return Decision(
                DENY,
                tier,
                f"{name} has used all {cap} {LIMIT_LABELS[key]}. The limit resets automatically.",
                "limit_reached",
            )

    if tier in level["hold"]:
        return Decision(
            HOLD,
            tier,
            f"{tool} needs the owner's approval. It runs once they approve it; "
            "check with get_approval_status.",
            "needs_approval",
            counters,
        )
    return Decision(ALLOW, tier, counters=counters)


def explain(agent: Dict[str, Any], tool_names: Iterable[str]) -> Dict[str, List[str]]:
    """Group the tools an agent can see by what happens when it calls them."""
    out: Dict[str, List[str]] = {"runs": [], "needs_approval": [], "not_available": []}
    for tool in sorted(set(tool_names)):
        if not tool_visible(agent, tool):
            out["not_available"].append(tool)
            continue
        d = decide(agent, tool)
        if d.outcome == HOLD:
            out["needs_approval"].append(tool)
        elif d.outcome == ALLOW:
            out["runs"].append(tool)
        else:
            out["not_available"].append(tool)
    return out
