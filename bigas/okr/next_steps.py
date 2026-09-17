"""Heuristic next steps for red Key Results (Monday pulse and dashboard)."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

RED_KR_HEALTH = frozenset({"at_risk", "off_track", "unmeasured"})


def is_red_kr(kr: Dict[str, Any]) -> bool:
    return (kr.get("health") or "unmeasured") in RED_KR_HEALTH


def _is_manual_gate(status: str) -> bool:
    return "(manual)" in (status or "").strip().lower()


def _is_done(status: str) -> bool:
    return (status or "").strip().lower() == "done"


def reason_kr_next_steps(
    kr: Dict[str, Any],
    *,
    objective_key: str,
    stale_days: int | None = None,
) -> List[str]:
    """Up to three concrete next steps for one red KR."""
    if not is_red_kr(kr):
        return []
    from bigas.okr.scoreboard import is_stale_current, stale_days_from_env

    days = stale_days if stale_days is not None else stale_days_from_env()
    steps: List[str] = []
    tickets = list(kr.get("tickets") or [])
    open_items = [t for t in tickets if not _is_done(str(t.get("status") or ""))]
    done_items = [t for t in tickets if _is_done(str(t.get("status") or ""))]
    gates = [t for t in open_items if _is_manual_gate(str(t.get("status") or ""))]
    open_work = [t for t in open_items if not _is_manual_gate(str(t.get("status") or ""))]

    for gate in gates[:2]:
        key = str(gate.get("key") or "").strip()
        title = str(gate.get("title") or "").strip()
        label = f"{key} ({title})" if title else key or "pending gate"
        steps.append(f"Clear human gate {label} — do not open a duplicate ticket.")

    for item in open_work[:2]:
        key = str(item.get("key") or "").strip()
        title = str(item.get("title") or "").strip()
        label = f"{key} ({title})" if title else key or "open work"
        steps.append(f"Advance linked work {label} or drop it if it will not move the KR number.")

    if kr.get("activity_without_outcome"):
        steps.append(
            f"{len(done_items)} Done item(s) did not move this KR — change the lever, not the task count."
        )

    if is_stale_current(kr, stale_days=days) and kr.get("measurable"):
        source = str(kr.get("source") or "live sources").strip()
        steps.append(f"Re-verify KR current from {source} (stale or never signed off).")

    health = kr.get("health") or "unmeasured"
    if health == "unmeasured":
        gap = (kr.get("measurement_gap") or "").strip()
        if gap:
            steps.append(f"Close measurement gap: {gap}")
        elif not steps:
            steps.append("Define how to measure this KR before opening execution tasks.")

    if done_items and health in {"off_track", "at_risk"} and len(steps) < 3:
        keys = ", ".join(str(t.get("key") or "") for t in done_items[:3] if t.get("key"))
        suffix = f" (Done: {keys})" if keys else ""
        steps.append(
            "Done work is history, not a stop — propose 1–3 new levers if the number is still red"
            f"{suffix}."
        )

    if not open_items and not gates and health in {"off_track", "at_risk"}:
        current = kr.get("current")
        target = kr.get("target")
        if current is not None and target is not None:
            steps.append(
                f"KR at {current} vs target {target} with no open levers — open To Do work that moves the metric (not a KR clone)."
            )
        elif not steps:
            steps.append(
                "No open levers on this KR — open concrete To Do work toward the target (not a KR title clone)."
            )

    if health == "at_risk" and not steps:
        steps.append("KR is behind expected pace — pick one lever this week that moves current toward target.")

    deduped: List[str] = []
    seen: set[str] = set()
    for step in steps:
        key = step.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(step.strip())
        if len(deduped) >= 3:
            break
    return deduped


def collect_red_kr_next_steps(
    objectives: Sequence[Dict[str, Any]],
    *,
    stale_days: int | None = None,
) -> List[Dict[str, Any]]:
    """Structured next steps for every red KR across objectives."""
    out: List[Dict[str, Any]] = []
    for obj in objectives:
        obj_key = str(obj.get("key") or "").strip()
        for kr in obj.get("key_results") or []:
            if not is_red_kr(kr):
                continue
            steps = reason_kr_next_steps(
                kr,
                objective_key=obj_key,
                stale_days=stale_days,
            )
            if not steps:
                continue
            out.append(
                {
                    "objective_key": obj_key,
                    "kr_id": kr.get("id"),
                    "kr_title": kr.get("title") or kr.get("id") or "",
                    "health": kr.get("health"),
                    "steps": steps,
                }
            )
    return out


def flatten_red_kr_next_steps(entries: Sequence[Dict[str, Any]]) -> List[str]:
    """Flat lines for dashboard / priming lists."""
    lines: List[str] = []
    for item in entries:
        obj_key = str(item.get("objective_key") or "").strip()
        title = str(item.get("kr_title") or "").strip()
        health = str(item.get("health") or "red").replace("_", " ")
        head = f"{obj_key}: {title} ({health})" if title else obj_key
        for step in item.get("steps") or []:
            lines.append(f"{head} — {step}")
    return lines
