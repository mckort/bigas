"""Mechanical Monday OKR pulse — countable facts, optional LLM comment underneath."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bigas.chat.activity import post_to_agent_thread, resolve_chat_target_user_id
from bigas.okr.scoreboard import (
    DEFAULT_LOOKBACK_DAYS,
    format_health_counts,
    load_okr_scoreboard,
    pct_label,
)

logger = logging.getLogger(__name__)

_COMMENT_INSTRUCTIONS = (
    "You are commenting under a mechanical OKR pulse. Do not restate, round, "
    "or contradict the counts. Do not say the week is on track if any KR is "
    "off track, at risk, unmeasured, or if Done sample size is 0. Name at most "
    "one next action. Under 80 words. No preamble."
)


def format_okr_pulse(snapshot: Dict[str, Any]) -> str:
    """Numbers-only pulse. Never omits sample size."""
    n_obj = int(snapshot.get("objective_count") or 0)
    n_kr = int(snapshot.get("key_result_count") or 0)
    stats = snapshot.get("stats") or {}
    lookback = int(snapshot.get("lookback_days") or DEFAULT_LOOKBACK_DAYS)
    done_total = int(snapshot.get("done_total") or 0)
    cycle = snapshot.get("cycle") or "current cycle"
    lines = [
        "**Monday OKR pulse** (mechanical — these numbers cannot flatter)",
        "",
        f"Cycle: {cycle}",
        f"Sample: {n_obj} Objective(s) · {n_kr} Key Result(s).",
        f"KR health: {format_health_counts(stats)}.",
        (
            f"Pace: expected {pct_label(snapshot.get('expected_mean'))} through cycle · "
            f"actual {pct_label(snapshot.get('measurable_mean'))} "
            f"({int(snapshot.get('measurable_count') or 0)} measurable KR(s))."
        ),
    ]
    if n_obj == 0:
        lines.append(
            "No Objectives in sample. Cannot report on track. Open /objectives."
        )
    if int(snapshot.get("measurable_count") or 0) == 0 and n_kr:
        lines.append(
            "Zero measurable KRs: absence of a score is a failure, not a pass."
        )
    lines.append("")
    lines.append(
        f"Done last {lookback} days: {done_total} (sample size {done_total}) · "
        f"linked to a KR {int(snapshot.get('done_linked') or 0)} · "
        f"unlinked {int(snapshot.get('done_unlinked') or 0)}."
    )
    if done_total == 0:
        lines.append("Sample size 0 for Done — not a clean week.")
    lines.append(
        f"Unlinked Done (all-time): {int(snapshot.get('unlinked_done') or 0)} · "
        f"unlinked open: {int(snapshot.get('unlinked_open') or 0)}."
    )
    stale = snapshot.get("stale_krs") or []
    lines.append(
        f"Stale KR currents (>{snapshot.get('stale_days') or 7}d or never verified): "
        f"{len(stale)}."
    )
    for item in stale[:8]:
        lines.append(f"- {item}")
    if len(stale) > 8:
        lines.append(f"- … +{len(stale) - 8} more")
    gates = snapshot.get("pending_gates") or []
    lines.append(f"Pending human gates: {len(gates)}.")
    for gate in gates[:8]:
        lines.append(
            f"- {gate.get('key')}: {gate.get('status')} — {gate.get('title')}"
        )
    if len(gates) > 8:
        lines.append(f"- … +{len(gates) - 8} more")
    theater = int(stats.get("activity_without_outcome") or 0)
    lines.append(f"Activity without outcome (shipping while KR stuck): {theater}.")
    next_actions = (snapshot.get("briefing") or {}).get("this_week") or []
    if next_actions:
        lines.append("")
        lines.append(f"Next action: {next_actions[0]}")
    lines.append("")
    lines.append(
        "_Derived from the ticket store. Any comment below is optional and "
        "must not replace these counts._"
    )
    return "\n".join(lines)


def comment_on_okr_pulse(numbers: str) -> Optional[str]:
    """Best-effort LLM note under the counts. Never required for a valid pulse."""
    try:
        from bigas.llm.factory import get_llm_client

        llm, _model = get_llm_client(feature="okr_pulse")
        text = (
            llm.complete(
                [
                    {"role": "system", "content": _COMMENT_INSTRUCTIONS},
                    {"role": "user", "content": numbers},
                ],
                max_tokens=256,
                temperature=0.2,
            )
            or ""
        ).strip()
        return text or None
    except Exception:
        logger.warning("OKR pulse comment failed", exc_info=True)
        return None


def build_weekly_okr_pulse(
    *,
    user_id: Optional[str] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    include_comment: bool = True,
) -> Dict[str, Any]:
    uid = (user_id or "").strip() or (resolve_chat_target_user_id() or "")
    snapshot = load_okr_scoreboard(
        user_id=uid,
        lookback_days=lookback_days,
        use_cache=False,
    )
    numbers = format_okr_pulse(snapshot)
    comment = comment_on_okr_pulse(numbers) if include_comment else None
    message = numbers
    if comment:
        message = f"{numbers}\n\n**Comment (not a substitute for the counts)**\n{comment}"
    return {
        "ok": True,
        "user_id": uid,
        "numbers": numbers,
        "comment": comment,
        "message": message,
        "snapshot": {
            "objective_count": snapshot.get("objective_count"),
            "key_result_count": snapshot.get("key_result_count"),
            "stats": snapshot.get("stats"),
            "done_total": snapshot.get("done_total"),
            "done_linked": snapshot.get("done_linked"),
            "done_unlinked": snapshot.get("done_unlinked"),
            "stale_count": len(snapshot.get("stale_krs") or []),
            "pending_gates": len(snapshot.get("pending_gates") or []),
        },
    }


def publish_weekly_okr_pulse(message: str, *, post_to_discord: bool, post_to_chat: bool) -> Dict[str, bool]:
    posted_discord = False
    posted_chat = False
    if post_to_discord:
        from bigas.agents.proactive_engine import chief_discord_webhook_url
        from bigas.discord_webhook import post_long_to_discord

        webhook = chief_discord_webhook_url()
        post_long_to_discord(
            webhook,
            message,
            chat_agent_id="chief",
            chat_metadata={"source": "weekly_okr_pulse"},
            mirror_chat=True,
        )
        posted_discord = bool(webhook)
        posted_chat = True
    elif post_to_chat:
        chat_msg = post_to_agent_thread(
            "chief",
            message,
            metadata={"source": "weekly_okr_pulse"},
        )
        posted_chat = bool(chat_msg)
    return {"posted_to_discord": posted_discord, "posted_to_chat": posted_chat}
