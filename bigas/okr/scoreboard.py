"""Live OKR scoreboard for chat priming and the Monday pulse.

Counts are derived from the ticket store. A report cannot look clean unless
it states its sample size.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bigas.okr.dashboard import build_okr_dashboard
from bigas.okr.model import parse_iso

logger = logging.getLogger(__name__)

PRIMED_AGENT_IDS = frozenset({"chief", "product", "marketing"})
DEFAULT_STALE_DAYS = 7
DEFAULT_LOOKBACK_DAYS = 7
_CACHE_TTL_S = 45.0
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def stale_days_from_env() -> int:
    raw = (os.environ.get("BIGAS_OKR_STALE_DAYS") or "").strip()
    try:
        days = int(raw) if raw else DEFAULT_STALE_DAYS
    except ValueError:
        days = DEFAULT_STALE_DAYS
    return max(1, min(90, days))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_stale_current(kr: Dict[str, Any], *, stale_days: int, now: Optional[datetime] = None) -> bool:
    """True when the KR current has never been verified or the stamp is cold."""
    stamp = parse_iso(kr.get("updated_at") or kr.get("signed_off_at") or "")
    if stamp is None:
        return True
    current = now or _utcnow()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return current - stamp > timedelta(days=stale_days)


def _is_manual_gate(status: str) -> bool:
    return "(manual)" in (status or "").strip().lower()


def _is_done(status: str) -> bool:
    return (status or "").strip().lower() == "done"


def _within_lookback(iso_value: Any, *, days: int, now: datetime) -> bool:
    stamp = parse_iso(iso_value)
    if stamp is None:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return now - stamp <= timedelta(days=days)


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{round(float(value) * 100):d}%"


def clear_okr_scoreboard_cache() -> None:
    _cache.clear()


def build_okr_scoreboard(
    store,
    *,
    user_id: str,
    now: Optional[datetime] = None,
    stale_days: Optional[int] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Snapshot of Objectives, KR health, stale currents, gates, and Done sample."""
    uid = (user_id or "").strip()
    days_stale = stale_days if stale_days is not None else stale_days_from_env()
    lookback = max(1, min(365, int(lookback_days)))
    cache_key = f"{uid}:{days_stale}:{lookback}"
    if use_cache and uid:
        hit = _cache.get(cache_key)
        if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_S:
            return dict(hit[1])

    current = now or _utcnow()
    dashboard = build_okr_dashboard(store, user_id=uid) if uid else {
        "cycle": "",
        "stats": {
            "objectives": 0,
            "key_results": 0,
            "on_track": 0,
            "at_risk": 0,
            "off_track": 0,
            "unmeasured": 0,
            "activity_without_outcome": 0,
        },
        "briefing": {},
        "objectives": [],
        "boards": [],
    }
    objectives = list(dashboard.get("objectives") or [])
    stats = dict(dashboard.get("stats") or {})
    stale: List[str] = []
    gates: List[Dict[str, str]] = []
    done_total = 0
    done_linked = 0
    done_unlinked = 0
    unlinked_open = 0
    unlinked_done = 0
    progresses: List[float] = []
    expecteds: List[float] = []

    for obj in objectives:
        obj_key = str(obj.get("key") or "")
        expected = obj.get("expected_progress")
        if expected is not None:
            expecteds.append(float(expected))
        status = str(obj.get("status") or "")
        if _is_manual_gate(status):
            gates.append(
                {
                    "key": obj_key,
                    "title": str(obj.get("title") or ""),
                    "status": status,
                }
            )
        for kr in obj.get("key_results") or []:
            label = f"{obj_key}: {kr.get('title') or kr.get('id')}"
            progress = kr.get("progress")
            if progress is not None:
                progresses.append(float(progress))
            if is_stale_current(kr, stale_days=days_stale, now=current):
                stale.append(label)
        for child in obj.get("unlinked_tickets") or []:
            child_status = str(child.get("status") or "")
            if _is_done(child_status):
                unlinked_done += 1
            else:
                unlinked_open += 1
            if _is_manual_gate(child_status):
                gates.append(
                    {
                        "key": str(child.get("key") or ""),
                        "title": str(child.get("title") or ""),
                        "status": child_status,
                    }
                )
            stamp = child.get("done_at") or child.get("updated_at")
            if _is_done(child_status) and _within_lookback(stamp, days=lookback, now=current):
                done_total += 1
                done_unlinked += 1
        for kr in obj.get("key_results") or []:
            for child in kr.get("tickets") or []:
                child_status = str(child.get("status") or "")
                if _is_manual_gate(child_status):
                    gates.append(
                        {
                            "key": str(child.get("key") or ""),
                            "title": str(child.get("title") or ""),
                            "status": child_status,
                        }
                    )
                stamp = child.get("done_at") or child.get("updated_at")
                if _is_done(child_status) and _within_lookback(
                    stamp, days=lookback, now=current
                ):
                    done_total += 1
                    done_linked += 1

    snapshot = {
        "cycle": dashboard.get("cycle") or "",
        "stats": stats,
        "briefing": dashboard.get("briefing") or {},
        "objectives": objectives,
        "stale_krs": stale,
        "stale_days": days_stale,
        "pending_gates": gates,
        "lookback_days": lookback,
        "done_total": done_total,
        "done_linked": done_linked,
        "done_unlinked": done_unlinked,
        "unlinked_open": unlinked_open,
        "unlinked_done": unlinked_done,
        "measurable_mean": (sum(progresses) / len(progresses)) if progresses else None,
        "expected_mean": (sum(expecteds) / len(expecteds)) if expecteds else None,
        "measurable_count": len(progresses),
        "key_result_count": int(stats.get("key_results") or 0),
        "objective_count": int(stats.get("objectives") or 0),
        "sample_stated": True,
    }
    if use_cache and uid:
        _cache[cache_key] = (time.monotonic(), dict(snapshot))
    return snapshot


def load_okr_scoreboard(
    *,
    user_id: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    use_cache: bool = True,
) -> Dict[str, Any]:
    from bigas.tickets.store import get_ticket_store

    try:
        return build_okr_scoreboard(
            get_ticket_store(),
            user_id=user_id,
            lookback_days=lookback_days,
            use_cache=use_cache,
        )
    except Exception:
        logger.exception("Failed to load OKR scoreboard for %s", user_id)
        return {
            "cycle": "",
            "stats": {
                "objectives": 0,
                "key_results": 0,
                "on_track": 0,
                "at_risk": 0,
                "off_track": 0,
                "unmeasured": 0,
                "activity_without_outcome": 0,
            },
            "briefing": {},
            "objectives": [],
            "stale_krs": [],
            "stale_days": stale_days_from_env(),
            "pending_gates": [],
            "lookback_days": lookback_days,
            "done_total": 0,
            "done_linked": 0,
            "done_unlinked": 0,
            "unlinked_open": 0,
            "unlinked_done": 0,
            "measurable_mean": None,
            "expected_mean": None,
            "measurable_count": 0,
            "key_result_count": 0,
            "objective_count": 0,
            "sample_stated": True,
            "error": "scoreboard_unavailable",
        }


def format_health_counts(stats: Dict[str, Any]) -> str:
    return (
        f"{int(stats.get('on_track') or 0)} on track · "
        f"{int(stats.get('at_risk') or 0)} at risk · "
        f"{int(stats.get('off_track') or 0)} off track · "
        f"{int(stats.get('unmeasured') or 0)} unmeasured"
    )


def pct_label(value: Optional[float]) -> str:
    return _pct(value)
