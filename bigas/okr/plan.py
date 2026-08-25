"""OKR Design and plan: live status + concrete work items toward Key Results."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from bigas.okr.context import format_evidence_pack, gather_okr_evidence
from bigas.okr.model import normalize_key_results
from bigas.okr.research import _extract_json_object

logger = logging.getLogger(__name__)

DEFAULT_THINKING_BUDGET = 4_096
MAX_TASKS_PER_KR = 3
MAX_TASKS_TOTAL = 10

_WIRE_TITLE_RE = re.compile(r"^wire weekly snapshot for\b", re.I)
_INSTRUMENT_TITLE_RE = re.compile(r"^instrument:\s*", re.I)
_DONE_STATUSES = frozenset({"done"})

OKR_PLAN_SYSTEM = """You are a Chief of Staff planning work toward committed Key Results.

The Objective and its Key Results already exist. KRs are the scoreboard — never
turn a KR into a ticket, and never create tickets whose only job is to pull
analytics (GA4 / GitHub / Jira snapshots, “wire weekly”, instrumentation of a
source that already has a number). Design and plan itself reads live evidence
and updates KR current values.

How to work:
1. Read the evidence pack (especially GA4) and the committed KRs.
2. Set each measurable KR's current to a number that is in the evidence.
   If the evidence has no number, omit that KR from key_result_updates.
   Do not invent currents.
3. Propose concrete work that would move each KR's number. Tasks are actions
   (publish a page, launch a campaign, fix a funnel step, write outreach,
   change the site, run ads) — not restatements of the KR title.
4. 1–3 tasks per KR, at most 10 total. Prefer fewer, sharper tickets.
5. Same language as the Objective. Scoped to one person or AI agent.
6. ai_doable=true only when an AI agent can do the first pass in the mapped
   repo or with existing tools (copy, page, tracking snippet, small site change).
   Human-only work (partnerships, pricing calls, budget) is ai_doable=false.
7. Do not duplicate open_tasks. Do not create Tasks named after a KR.

Return JSON only — no markdown fences, no preamble.

JSON shape:
{
  "briefing": "2-4 sentences for the human reviewing Design approval.",
  "plan_markdown": "Markdown: current status from evidence, then the work you are opening and why it should move each KR.",
  "key_result_updates": [
    {"id": "kr-abc", "current": 0}
  ],
  "tasks_to_create": [
    {
      "title": "Short action title",
      "description": "What to do, done when, and which KR it should move.",
      "kr_id": "kr-abc",
      "ai_doable": false
    }
  ]
}
"""


@dataclass
class OkrPlanResult:
    tasks: List[Dict[str, Any]]
    plan_markdown: str
    briefing: str
    current_updates: List[Dict[str, Any]] = field(default_factory=list)
    model: str = ""
    used_llm: bool = False
    evidence: Dict[str, str] = field(default_factory=dict)


def _thinking_budget() -> Optional[int]:
    raw = (os.environ.get("BIGAS_OKR_PLAN_THINKING_BUDGET") or "").strip()
    if raw.lower() in {"0", "none", "off", "false"}:
        return None
    if raw.lower() in {"", "default"}:
        return DEFAULT_THINKING_BUDGET
    try:
        value = int(raw)
        return value if value > 0 else None
    except ValueError:
        return DEFAULT_THINKING_BUDGET


def is_mechanical_okr_task(
    ticket: Dict[str, Any],
    key_results: Sequence[Dict[str, Any]],
) -> bool:
    """True for KR-title clones and analytics-wiring tickets from the old plan step."""
    title = str(ticket.get("title") or "").strip()
    if not title:
        return False
    if _WIRE_TITLE_RE.match(title) or _INSTRUMENT_TITLE_RE.match(title):
        return True
    lowered = title.lower()
    for kr in key_results:
        kr_title = str(kr.get("title") or "").strip().lower()
        if kr_title and lowered == kr_title:
            return True
    return False


def is_open_task(ticket: Dict[str, Any]) -> bool:
    status = str(ticket.get("status") or "").strip().lower()
    return status not in _DONE_STATUSES


def apply_current_updates(
    key_results: List[Dict[str, Any]],
    updates: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Copy LLM/heuristic current values onto matching measurable KRs."""
    by_id = {str(kr.get("id") or ""): kr for kr in key_results}
    now = datetime.now(timezone.utc).isoformat()
    for raw in updates or []:
        if not isinstance(raw, dict):
            continue
        kr = by_id.get(str(raw.get("id") or "").strip())
        if not kr or not kr.get("measurable"):
            continue
        if "current" not in raw:
            continue
        try:
            kr["current"] = float(raw["current"])
            kr["updated_at"] = now
        except (TypeError, ValueError):
            continue
    return key_results


def heuristic_ga4_currents(
    key_results: Sequence[Dict[str, Any]],
    ga4_text: str,
) -> List[Dict[str, Any]]:
    """Best-effort currents from the GA4 evidence block. Never invents missing metrics."""
    text = ga4_text or ""
    if not text.strip() or text.strip().startswith("("):
        return []
    traffic = re.search(
        r"sessions\s+(\d+)\s*\(prev[^)]*\),\s*users\s+(\d+)\s*\(prev[^)]*\),\s*pageviews\s+(\d+)",
        text,
        flags=re.I,
    )
    sessions = int(traffic.group(1)) if traffic else None
    users = int(traffic.group(2)) if traffic else None
    pageviews = int(traffic.group(3)) if traffic else None
    page_rows = re.findall(
        r"pagePath=([^\s,]+).*?screenPageViews=(\d+)",
        text,
        flags=re.I,
    )
    event_rows = re.findall(
        r"eventName=([^\s,]+).*?keyEvents=(\d+)",
        text,
        flags=re.I,
    )

    updates: List[Dict[str, Any]] = []
    for kr in key_results:
        if str(kr.get("source") or "").lower() != "ga4":
            continue
        kr_id = str(kr.get("id") or "").strip()
        if not kr_id:
            continue
        blob = f"{kr.get('metric') or ''} {kr.get('title') or ''}".lower()
        current: Optional[float] = None
        for path, views in page_rows:
            path_l = path.lower().strip()
            if path_l and path_l not in {"/", "(not set)"} and path_l in blob:
                current = float(views)
                break
        if current is None:
            if "session" in blob and "pageview" not in blob and sessions is not None:
                current = float(sessions)
            elif "pageview" in blob and pageviews is not None:
                current = float(pageviews)
            elif re.search(r"\busers?\b", blob) and users is not None:
                current = float(users)
            else:
                for name, count in event_rows:
                    tokens = [p for p in re.split(r"[_\s]+", name.lower()) if len(p) > 3]
                    if tokens and all(token in blob for token in tokens):
                        current = float(count)
                        break
        if current is not None:
            updates.append({"id": kr_id, "current": current})
    return updates


def _normalize_plan_tasks(
    raw_tasks: Any,
    *,
    key_results: Sequence[Dict[str, Any]],
    existing_titles: set[str],
) -> List[Dict[str, Any]]:
    kr_by_id = {str(kr.get("id") or ""): kr for kr in key_results}
    kr_titles = {
        str(kr.get("title") or "").strip().lower()
        for kr in key_results
        if str(kr.get("title") or "").strip()
    }
    seen = set(existing_titles)
    out: List[Dict[str, Any]] = []
    per_kr: Dict[str, int] = {}
    if not isinstance(raw_tasks, list):
        return out
    for item in raw_tasks:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("summary") or "").strip()
        description = str(item.get("description") or "").strip()
        kr_id = str(item.get("kr_id") or item.get("parent_kr_id") or "").strip()
        if not title or not description or kr_id not in kr_by_id:
            continue
        if _WIRE_TITLE_RE.match(title) or _INSTRUMENT_TITLE_RE.match(title):
            continue
        if title.lower() in kr_titles:
            continue
        key = title.lower()
        if key in seen:
            continue
        if per_kr.get(kr_id, 0) >= MAX_TASKS_PER_KR:
            continue
        kr = kr_by_id[kr_id]
        body = description
        if f"**KR:**" not in body:
            body = (
                f"{body}\n\n"
                f"**Objective work toward KR:** {kr.get('title')}\n"
                f"**Metric:** {kr.get('metric')} ({kr.get('source')})"
            )
        out.append(
            {
                "title": title[:120],
                "description": body,
                "kr_id": kr_id,
                "ai_doable": bool(item.get("ai_doable")),
            }
        )
        seen.add(key)
        per_kr[kr_id] = per_kr.get(kr_id, 0) + 1
        if len(out) >= MAX_TASKS_TOTAL:
            break
    return out


def _build_user_prompt(
    ticket: Dict[str, Any],
    evidence: Dict[str, str],
    *,
    key_results: Sequence[Dict[str, Any]],
    existing_tasks: Sequence[Dict[str, Any]],
) -> str:
    open_tasks = [
        {
            "key": t.get("key"),
            "title": t.get("title"),
            "status": t.get("status"),
            "parent_kr_id": t.get("parent_kr_id"),
        }
        for t in existing_tasks
        if is_open_task(t) and not is_mechanical_okr_task(t, key_results)
    ]
    return f"""Plan work toward these committed Key Results. Update currents from evidence.
Do not clone KR titles into tasks. Do not create analytics-wiring tasks.

Objective: {ticket.get('title') or ''}

Committed KRs:
{json.dumps(list(key_results), ensure_ascii=False, indent=2)}

Open tasks (do not duplicate):
{json.dumps(open_tasks, ensure_ascii=False, indent=2)}

Evidence pack:
{format_evidence_pack(evidence)}
"""


def run_okr_plan(
    ticket: Dict[str, Any],
    *,
    key_results: Optional[Sequence[Dict[str, Any]]] = None,
    existing_tasks: Optional[Sequence[Dict[str, Any]]] = None,
    evidence: Optional[Dict[str, str]] = None,
    llm: Any = None,
    model: Optional[str] = None,
) -> OkrPlanResult:
    krs = normalize_key_results(key_results if key_results is not None else ticket.get("key_results"))
    children = list(existing_tasks or [])
    pack = evidence if evidence is not None else gather_okr_evidence(ticket)
    heuristic_updates = heuristic_ga4_currents(krs, pack.get("ga4") or "")
    existing_titles = {
        str(t.get("title") or "").strip().lower()
        for t in children
        if is_open_task(t) and not is_mechanical_okr_task(t, krs)
    }
    briefing_fallback = (
        f"Could not finish OKR planning for {(pack.get('brand') or 'this brand')}. "
        "No KR-clone or wiring tickets were created. Re-drag to Design and plan after sources work."
    )
    plan_fallback = (
        "### Plan failed\n\n"
        "The model did not return concrete work items. Live status was still read from evidence "
        "where possible. Do not treat Key Results as tasks.\n\n"
        + format_evidence_pack(pack)
    )

    try:
        from bigas.llm.factory import get_llm_client

        client = llm
        model_name = model or ""
        if client is None:
            client, model_name = get_llm_client(feature="okr_plan")
        messages = [
            {"role": "system", "content": OKR_PLAN_SYSTEM},
            {
                "role": "user",
                "content": _build_user_prompt(
                    ticket, pack, key_results=krs, existing_tasks=children
                ),
            },
        ]
        call_kwargs: Dict[str, Any] = {
            "max_tokens": 8_192,
            "temperature": 0.2,
        }
        if str(model_name or "").lower().startswith("gemini"):
            budget = _thinking_budget()
            if budget is not None:
                call_kwargs["thinking_budget"] = budget
        raw = client.complete(messages=messages, **call_kwargs)
        parsed = _extract_json_object(raw)
        tasks = _normalize_plan_tasks(
            parsed.get("tasks_to_create"),
            key_results=krs,
            existing_titles=existing_titles,
        )
        llm_updates = parsed.get("key_result_updates")
        updates = heuristic_updates + (
            [u for u in llm_updates if isinstance(u, dict)] if isinstance(llm_updates, list) else []
        )
        plan_md = str(parsed.get("plan_markdown") or "").strip() or format_evidence_pack(pack)
        briefing = str(parsed.get("briefing") or "").strip() or (
            f"Proposed {len(tasks)} work items toward committed KRs. Review in Design approval."
        )
        if not tasks:
            raise ValueError("LLM returned no usable work items")
        return OkrPlanResult(
            tasks=tasks,
            plan_markdown=plan_md,
            briefing=briefing,
            current_updates=updates,
            model=model_name,
            used_llm=True,
            evidence=pack,
        )
    except Exception as exc:
        logger.warning("OKR plan LLM failed: %s", exc, exc_info=True)
        return OkrPlanResult(
            tasks=[],
            plan_markdown=plan_fallback,
            briefing=briefing_fallback,
            current_updates=heuristic_updates,
            model=model or "",
            used_llm=False,
            evidence=pack,
        )
