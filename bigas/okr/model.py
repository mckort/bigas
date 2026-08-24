"""OKR data helpers — Objectives, Key Results, and progress health."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

GOAL_ISSUE_TYPES = frozenset({"Epic", "Objective"})
OBJECTIVE_LABEL = "objective"
KR_ID_RE = re.compile(r"^kr-[a-z0-9-]{4,}$")

KR_SOURCES = ("ga4", "github", "jira", "manual", "stripe", "ads", "unknown")
KR_DIRECTIONS = ("increase", "decrease", "maintain")
KR_HEALTHS = ("on_track", "at_risk", "off_track", "unmeasured")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _label_list(raw: Optional[Iterable[Any]]) -> List[str]:
    out: List[str] = []
    for item in raw or []:
        text = str(item or "").strip().lower().replace(" ", "-")
        if text:
            out.append(text)
    return out


def is_objective(ticket: Optional[Dict[str, Any]]) -> bool:
    """True for Objective issue type or an `objective` label. Epics stay Epics."""
    ticket = ticket or {}
    itype = str(ticket.get("issue_type") or "").strip().title()
    if itype == "Objective":
        return True
    if itype == "Epic":
        return False
    return OBJECTIVE_LABEL in _label_list(ticket.get("labels"))


def promote_objective_type(issue_type: str, labels: Optional[Iterable[Any]]) -> str:
    """Label `objective` on a Task becomes Objective. Epic is never rewritten."""
    itype = (issue_type or "Task").strip().title() or "Task"
    if itype == "Epic":
        return "Epic"
    if itype == "Objective":
        return "Objective"
    if OBJECTIVE_LABEL in _label_list(labels):
        return "Objective"
    return itype


def new_kr_id() -> str:
    return f"kr-{uuid.uuid4().hex[:10]}"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_key_result(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or raw.get("summary") or "").strip()
    if not title:
        return None
    kr_id = str(raw.get("id") or "").strip()
    if not kr_id or not KR_ID_RE.match(kr_id):
        kr_id = new_kr_id()
    source = str(raw.get("source") or "unknown").strip().lower()
    if source not in KR_SOURCES:
        source = "unknown"
    direction = str(raw.get("direction") or "increase").strip().lower()
    if direction not in KR_DIRECTIONS:
        direction = "increase"
    measurable = bool(raw.get("measurable", True))
    gap = str(raw.get("measurement_gap") or "").strip()
    if not measurable and not gap:
        gap = "Define a metric source, baseline, and target before this KR can be scored."
    return {
        "id": kr_id,
        "title": title[:240],
        "metric": str(raw.get("metric") or title).strip()[:240],
        "unit": str(raw.get("unit") or "").strip()[:40],
        "baseline": _as_float(raw.get("baseline")),
        "target": _as_float(raw.get("target")),
        "current": _as_float(raw.get("current"), _as_float(raw.get("baseline"))),
        "source": source,
        "measurable": measurable,
        "measurement_gap": gap,
        "direction": direction,
        "status": str(raw.get("status") or "proposed").strip() or "proposed",
        "updated_at": str(raw.get("updated_at") or "").strip(),
        "signed_off_at": str(raw.get("signed_off_at") or "").strip(),
        "signed_off_by": str(raw.get("signed_off_by") or "").strip(),
        "ai_note": str(raw.get("ai_note") or "").strip(),
    }


def normalize_key_results(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        kr = normalize_key_result(item)
        if not kr:
            continue
        if kr["id"] in seen:
            kr["id"] = new_kr_id()
        seen.add(kr["id"])
        out.append(kr)
    return out


def key_result_by_id(
    key_results: Sequence[Dict[str, Any]], kr_id: str
) -> Optional[Dict[str, Any]]:
    wanted = (kr_id or "").strip()
    if not wanted:
        return None
    for kr in key_results:
        if kr.get("id") == wanted:
            return kr
    return None


def kr_progress(kr: Dict[str, Any]) -> Optional[float]:
    """Fraction 0–1 toward target, or None if unmeasurable."""
    if not kr.get("measurable"):
        return None
    baseline = _as_float(kr.get("baseline"))
    target = _as_float(kr.get("target"))
    current = _as_float(kr.get("current"), baseline)
    direction = kr.get("direction") or "increase"
    if direction == "maintain":
        if target == 0:
            return 1.0 if current == 0 else 0.0
        delta = abs(current - target) / abs(target)
        return max(0.0, min(1.0, 1.0 - delta))
    span = target - baseline
    if abs(span) < 1e-9:
        return None
    if direction == "decrease":
        span = baseline - target
        moved = baseline - current
        if span <= 0:
            return None
        return max(0.0, min(1.0, moved / span))
    return max(0.0, min(1.0, (current - baseline) / span))


def expected_progress(
    *,
    created_at: Any,
    cycle_end: Any = None,
    now: Optional[datetime] = None,
) -> float:
    """Linear expected progress through the cycle (default 90 days)."""
    start = parse_iso(created_at) or _utcnow()
    end = parse_iso(cycle_end)
    if end is None:
        from datetime import timedelta

        end = start + timedelta(days=90)
    current = now or _utcnow()
    total = (end - start).total_seconds()
    if total <= 0:
        return 1.0
    elapsed = (current - start).total_seconds()
    return max(0.0, min(1.0, elapsed / total))


def kr_health(
    kr: Dict[str, Any],
    *,
    expected: float,
) -> str:
    progress = kr_progress(kr)
    if progress is None:
        return "unmeasured"
    if progress + 0.02 >= expected:
        return "on_track"
    if progress + 0.18 >= expected:
        return "at_risk"
    return "off_track"


def objective_progress(key_results: Sequence[Dict[str, Any]]) -> Optional[float]:
    """Unweighted mean of measurable KRs. None if nothing can be scored."""
    scores = [kr_progress(kr) for kr in key_results]
    measured = [s for s in scores if s is not None]
    if not measured:
        return None
    return sum(measured) / len(measured)


def cycle_end_for(cycle: str, *, created_at: Any = None) -> Optional[str]:
    text = (cycle or "").strip().upper()
    match = re.match(r"^(\d{4})-Q([1-4])$", text)
    if match:
        year = int(match.group(1))
        quarter = int(match.group(2))
        month = quarter * 3
        last_day = 30 if month in (6, 9) else 31 if month != 12 else 31
        if month == 6:
            last_day = 30
        elif month == 3:
            last_day = 31
        return f"{year}-{month:02d}-{last_day:02d}T23:59:59+00:00"
    start = parse_iso(created_at)
    if start:
        from datetime import timedelta

        return (start + timedelta(days=90)).isoformat()
    return None


def annotate_key_result(
    kr: Dict[str, Any],
    *,
    expected: float,
    child_tickets: Sequence[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    item = dict(kr)
    progress = kr_progress(item)
    health = kr_health(item, expected=expected)
    done = [
        t
        for t in child_tickets
        if str(t.get("status") or "").strip().lower() == "done"
    ]
    open_items = [
        t
        for t in child_tickets
        if str(t.get("status") or "").strip().lower() != "done"
    ]
    activity_without_outcome = (
        health in {"off_track", "at_risk", "unmeasured"}
        and (progress or 0) < 0.12
        and len(done) >= 3
    )
    item["progress"] = None if progress is None else round(progress, 3)
    item["expected_progress"] = round(expected, 3)
    item["health"] = health
    item["linked_open"] = len(open_items)
    item["linked_done"] = len(done)
    item["activity_without_outcome"] = activity_without_outcome
    return item
