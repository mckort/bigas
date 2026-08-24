"""OKR pipeline: research → description approval → plan → in progress."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bigas.okr.model import (
    annotate_key_result,
    cycle_end_for,
    expected_progress,
    is_objective,
    normalize_key_results,
    objective_progress,
    promote_objective_type,
)
from bigas.okr.research import run_okr_research
from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER
from bigas.resources.product.jira_automation.description import upsert_research_section
from bigas.tickets.labels import resolve_ticket_labels

logger = logging.getLogger(__name__)

DESCRIPTION_APPROVAL_STATUS = "Description approval (manual)"


def _phase_for_status(status: str) -> Optional[str]:
    from bigas.agents.proactive_engine import goal_phase_for_status

    return goal_phase_for_status(status)


def _comment(store, ticket_id: str, body: str) -> None:
    store.add_comment(ticket_id, f"{BIGAS_COMMENT_MARKER} {body}", author_name="Bigas")


def propose_key_results(ticket: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Propose KRs from live brand evidence + LLM. Never returns SaaS template KRs."""
    return run_okr_research(ticket).key_results


def _format_kr_comment(key_results: List[Dict[str, Any]], *, heading: str) -> str:
    lines = [heading, ""]
    for i, kr in enumerate(key_results, start=1):
        measurable = "measurable" if kr.get("measurable") else "not measurable yet"
        source = kr.get("source") or "unknown"
        lines.append(
            f"{i}. **{kr['title']}** ({measurable}, source: `{source}`)"
        )
        if kr.get("measurable"):
            lines.append(
                f"   Baseline {kr.get('baseline')} → target {kr.get('target')} {kr.get('unit') or ''}".rstrip()
            )
        if kr.get("measurement_gap"):
            lines.append(f"   Gap: {kr['measurement_gap']}")
        if kr.get("ai_note"):
            lines.append(f"   {kr['ai_note']}")
    lines.append("")
    lines.append("KRs are scored independently. Objective % is only a hint — a red KR still fails the objective.")
    return "\n".join(lines)


def _task_specs_for_kr(objective: Dict[str, Any], kr: Dict[str, Any]) -> List[Dict[str, Any]]:
    title = kr.get("title") or "Key result"
    specs = [
        {
            "title": f"{title[:80]}",
            "description": (
                f"Work that moves this parameter toward the Objective, not a new goal.\n\n"
                f"**Objective:** {objective.get('title')}\n"
                f"**KR:** {title}\n"
                f"**Metric:** {kr.get('metric')} ({kr.get('source')})\n"
                f"**Baseline → target:** {kr.get('baseline')} → {kr.get('target')} {kr.get('unit') or ''}\n\n"
                "Update this task as the number moves. Done only when the KR has moved, "
                "not when activity was completed."
            ),
            "ai_doable": False,
        }
    ]
    if not kr.get("measurable"):
        specs.insert(
            0,
            {
                "title": f"Instrument: {kr.get('metric') or title}",
                "description": (
                    f"This KR cannot be scored yet.\n\n{kr.get('measurement_gap')}\n\n"
                    "Done when a weekly number can be pulled from a named source."
                ),
                "ai_doable": True,
            },
        )
    elif kr.get("source") in {"ga4", "github", "jira"}:
        specs.append(
            {
                "title": f"Wire weekly snapshot for {kr.get('metric')}",
                "description": (
                    f"Pull `{kr.get('metric')}` from `{kr.get('source')}` into this KR's current value "
                    "on the weekly check-in. AI can do the first wiring pass."
                ),
                "ai_doable": True,
            }
        )
    return specs


def handle_objective_status_change(
    ticket: Dict[str, Any],
    *,
    to_status: str,
    from_status: str = "",
) -> Dict[str, Any]:
    """Run the OKR prototype pipeline. Does not launch Cursor implement agents."""
    from bigas.tickets.store import get_ticket_store

    if not is_objective(ticket):
        return {"ok": True, "skipped": True, "reason": "not an objective"}

    phase = _phase_for_status(to_status)
    if not phase:
        return {"ok": True, "skipped": True, "reason": "no okr phase for status"}

    store = get_ticket_store()
    ticket_id = ticket.get("ticket_id") or ""
    key = ticket.get("key") or ""
    logger.info("OKR pipeline %s %s → %s (%s)", key, from_status, to_status, phase)

    if phase == "research":
        from bigas.resources.product.jira_automation.config import JiraAutomationConfig

        research = run_okr_research(ticket)
        key_results = research.key_results
        approval = (
            JiraAutomationConfig.from_env().status_description_approval
            or DESCRIPTION_APPROVAL_STATUS
        )
        briefing = research.briefing or (
            f"Proposed {len(key_results)} Key Results for “{ticket.get('title')}”. "
            f"{sum(1 for kr in key_results if not kr.get('measurable'))} KR(s) still need instrumentation. "
            f"Review in {approval}. If you keep these KRs, drag to Design and plan to create "
            f"one Task per KR (kept current on the weekly pulse)."
        )
        description = upsert_research_section(
            ticket.get("description") or "",
            research_markdown=research.research_markdown,
            brief_fallback=str(ticket.get("title") or key),
        )
        store.update_ticket(
            ticket_id,
            key_results=key_results,
            okr_phase="research",
            okr_briefing=briefing,
            description=description,
            status=approval,
            issue_type=promote_objective_type(ticket.get("issue_type") or "", resolve_ticket_labels(ticket)),
        )
        _comment(
            store,
            ticket_id,
            _format_kr_comment(
                key_results,
                heading=(
                    f"**OKR research** for {key}: proposed Key Results "
                    f"(not committed yet; model={research.model or 'n/a'}). "
                    f"Moved to {approval} for review."
                ),
            ),
        )
        return {
            "ok": True,
            "handler": "okr_research",
            "issue_key": key,
            "phase": phase,
            "key_results": len(key_results),
            "moved_to": approval,
            "summary": ticket.get("title") or key,
            "model": research.model,
            "used_llm": research.used_llm,
        }

    if phase == "plan":
        key_results = normalize_key_results(ticket.get("key_results")) or propose_key_results(ticket)
        for kr in key_results:
            kr["status"] = "committed"
        created: List[Dict[str, str]] = []
        existing = store.list_tickets_for_parent(key)
        existing_titles = {(t.get("title") or "").strip().lower() for t in existing}
        from bigas.tickets.service import TicketService

        service = TicketService()
        for kr in key_results:
            for spec in _task_specs_for_kr(ticket, kr):
                if spec["title"].strip().lower() in existing_titles:
                    continue
                labels = ["okr"]
                if spec.get("ai_doable"):
                    labels.append("ai-doable")
                child = service.create_ticket(
                    ticket["board_id"],
                    user_id=None,
                    title=spec["title"],
                    description=spec["description"],
                    status="To Do",
                    issue_type="Task",
                    labels=labels,
                    parent_key=key,
                    parent_kr_id=kr["id"],
                )
                created.append({"key": child.get("key") or "", "kr_id": kr["id"]})
                existing_titles.add(spec["title"].strip().lower())
        briefing = (
            f"Committed {len(key_results)} Key Results and created {len(created)} tasks. "
            "Each task is tied to a KR. Drag the objective to In Progress (AI) for a weekly pulse. "
            "Tasks stay in To Do until a human moves them."
        )
        store.update_ticket(
            ticket_id,
            key_results=key_results,
            okr_phase="plan",
            okr_briefing=briefing,
        )
        created_txt = ", ".join(item["key"] for item in created if item["key"]) or "none"
        _comment(
            store,
            ticket_id,
            f"**OKR plan** for {key}: committed Key Results and created {created_txt}. "
            "Left in Design and plan (AI) so you can edit before starting.",
        )
        return {
            "ok": True,
            "handler": "okr_plan",
            "issue_key": key,
            "phase": phase,
            "tasks_created": created,
        }

    # in_progress
    key_results = normalize_key_results(ticket.get("key_results"))
    children = store.list_tickets_for_parent(key)
    expected = expected_progress(
        created_at=ticket.get("created_at"),
        cycle_end=cycle_end_for(ticket.get("okr_cycle") or "", created_at=ticket.get("created_at")),
    )
    annotated = [
        annotate_key_result(
            kr,
            expected=expected,
            child_tickets=[c for c in children if (c.get("parent_kr_id") or "") == kr.get("id")],
        )
        for kr in key_results
    ]
    risks = [kr for kr in annotated if kr.get("health") in {"at_risk", "off_track", "unmeasured"}]
    activity = [kr for kr in annotated if kr.get("activity_without_outcome")]
    briefing_bits = [
        f"Weekly pulse for {key}.",
        f"{len(annotated) - len(risks)}/{len(annotated) or 1} KRs on track vs expected {round(expected * 100)}% of cycle.",
        "No tasks were moved — a human starts work from To Do.",
    ]
    if risks:
        briefing_bits.append(
            "Watch: " + "; ".join(f"{kr['title']} ({kr['health']})" for kr in risks[:3])
        )
    if activity:
        briefing_bits.append(
            "Activity without outcome on: "
            + ", ".join(kr["title"] for kr in activity[:2])
            + ". Stop opening tasks; change the work."
        )
    briefing = " ".join(briefing_bits)
    store.update_ticket(ticket_id, okr_phase="in_progress", okr_briefing=briefing)
    _comment(store, ticket_id, f"**OKR weekly pulse**\n\n{briefing}")
    return {
        "ok": True,
        "handler": "okr_in_progress",
        "issue_key": key,
        "phase": phase,
        "started": [],
        "progress": objective_progress(key_results),
    }
