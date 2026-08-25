"""Inject live Objectives into Chief / Product / Marketing chat sessions."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bigas.chat.activity import resolve_chat_target_user_id
from bigas.okr.scoreboard import (
    PRIMED_AGENT_IDS,
    format_health_counts,
    is_stale_current,
    load_okr_scoreboard,
    pct_label,
)

logger = logging.getLogger(__name__)

_MAX_OBJECTIVE_LINES = 8
_MAX_LIST = 6

_RULES = (
    "This block is the live scoreboard. Do not invent KR currents or treat stale "
    "values as verified fact. Closed tickets are evidence of activity, not progress — "
    "only a moving Key Result counts. Unlinked Done is debt. Do not auto-start work "
    "because a KR is off track; surface it and wait for a human drag. Prefer linking "
    "new tasks to a KR. A report with sample size 0 is a failure, not a pass."
)


def _trim(items: List[str], limit: int = _MAX_LIST) -> List[str]:
    if len(items) <= limit:
        return items
    extra = len(items) - limit
    return items[:limit] + [f"… +{extra} more"]


def format_okr_priming_block(snapshot: Dict[str, Any]) -> str:
    """Compact, always-stated-sample priming text for a chat system prompt."""
    n_obj = int(snapshot.get("objective_count") or 0)
    n_kr = int(snapshot.get("key_result_count") or 0)
    stats = snapshot.get("stats") or {}
    cycle = snapshot.get("cycle") or "current cycle"
    lines = [
        "## Live Objectives (injected; required reading)",
        _RULES,
        "",
        f"Sample: {n_obj} Objective(s) · {n_kr} Key Result(s) · cycle {cycle}.",
        f"KR health: {format_health_counts(stats)}.",
        (
            f"Pace: expected {pct_label(snapshot.get('expected_mean'))} of cycle · "
            f"actual {pct_label(snapshot.get('measurable_mean'))} "
            f"({int(snapshot.get('measurable_count') or 0)} measurable KR(s))."
        ),
    ]
    if n_obj == 0:
        lines.append(
            "No live Objectives. Do not claim the quarter is on track. "
            "Point the human to /objectives."
        )
        return "\n".join(lines)

    obj_lines: List[str] = []
    for obj in (snapshot.get("objectives") or [])[:_MAX_OBJECTIVE_LINES]:
        key = obj.get("key") or "?"
        title = (obj.get("title") or "").strip() or "Untitled"
        health = obj.get("health") or "draft"
        obj_lines.append(
            f"- {key} {title} [{health}] expected {pct_label(obj.get('expected_progress'))} "
            f"actual {pct_label(obj.get('progress'))}"
        )
        stale_days = int(snapshot.get("stale_days") or 7)
        for kr in obj.get("key_results") or []:
            mark = (
                " stale"
                if is_stale_current(kr, stale_days=stale_days)
                else ""
            )
            current = kr.get("current")
            target = kr.get("target")
            unit = (kr.get("unit") or "").strip()
            obj_lines.append(
                f"  · {kr.get('title') or kr.get('id')} [{kr.get('health') or 'unmeasured'}] "
                f"{current}→{target} {unit}{mark}".rstrip()
            )
    extra_obj = n_obj - min(n_obj, _MAX_OBJECTIVE_LINES)
    if extra_obj > 0:
        obj_lines.append(f"- … +{extra_obj} more Objectives")
    lines.append("Objectives:")
    lines.extend(obj_lines)

    stale = snapshot.get("stale_krs") or []
    lines.append(
        f"Stale KR currents (>{snapshot.get('stale_days') or 7}d or never verified): "
        f"{len(stale)}."
    )
    if stale:
        lines.extend(f"- {item}" for item in _trim([str(s) for s in stale]))

    gates = snapshot.get("pending_gates") or []
    lines.append(f"Pending human gates: {len(gates)}.")
    if gates:
        lines.extend(
            f"- {g.get('key')}: {g.get('status')} — {g.get('title')}"
            for g in gates[:_MAX_LIST]
        )
        if len(gates) > _MAX_LIST:
            lines.append(f"- … +{len(gates) - _MAX_LIST} more")

    lookback = int(snapshot.get("lookback_days") or 7)
    done_total = int(snapshot.get("done_total") or 0)
    done_unlinked = int(snapshot.get("done_unlinked") or 0)
    lines.append(
        f"Done last {lookback}d: {done_total} (sample size {done_total}) · "
        f"linked to a KR {int(snapshot.get('done_linked') or 0)} · "
        f"unlinked {done_unlinked}."
    )
    if done_total == 0:
        lines.append("Zero Done in the lookback is a sample of 0, not a clean week.")
    if done_unlinked or int(snapshot.get("unlinked_done") or 0):
        lines.append(
            f"Unlinked Done (all-time): {int(snapshot.get('unlinked_done') or 0)}. "
            "Do not celebrate those as KR progress."
        )
    next_actions = (snapshot.get("briefing") or {}).get("this_week") or []
    if next_actions:
        lines.append(f"Suggested next: {next_actions[0]}")
    return "\n".join(lines)


def okr_priming_block_for_agent(
    agent_id: str,
    *,
    user_id: Optional[str] = None,
) -> str:
    """Return priming text for primed agents, or empty for others / failures."""
    aid = (agent_id or "").strip().lower()
    if aid not in PRIMED_AGENT_IDS:
        return ""
    uid = (user_id or "").strip() or (resolve_chat_target_user_id() or "")
    if not uid:
        return ""
    try:
        snapshot = load_okr_scoreboard(user_id=uid)
        return format_okr_priming_block(snapshot)
    except Exception:
        logger.exception("OKR priming failed for agent=%s user=%s", aid, uid)
        return ""
