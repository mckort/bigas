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
from bigas.okr.plan import (
    apply_current_updates,
    heuristic_ga4_currents,
    is_mechanical_okr_task,
    is_open_task,
    run_okr_plan,
)
from bigas.okr.research import run_okr_research
from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER
from bigas.resources.product.jira_automation.description import (
    upsert_plan_section,
    upsert_research_section,
)
from bigas.tickets.labels import resolve_ticket_labels

logger = logging.getLogger(__name__)

DESCRIPTION_APPROVAL_STATUS = "Description approval (manual)"
DESIGN_APPROVAL_STATUS = "Design approval (manual)"


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


def _retire_mechanical_children(store, children: List[Dict[str, Any]], key_results: List[Dict[str, Any]]) -> List[str]:
    """Close KR-clone and analytics-wiring tickets left by the old plan step."""
    closed: List[str] = []
    for child in children:
        if not is_open_task(child) or not is_mechanical_okr_task(child, key_results):
            continue
        ticket_id = child.get("ticket_id") or ""
        if not ticket_id:
            continue
        store.update_ticket(ticket_id, status="Done")
        _comment(
            store,
            ticket_id,
            "Closed: this was a Key Result copy or a GA4/wiring ticket. "
            "Design and plan now reads live status itself and opens real work items instead.",
        )
        closed.append(str(child.get("key") or ticket_id))
    return closed


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
            f"Review in {approval}. If you keep these KRs, drag to Design and plan "
            f"to open concrete work toward each KR (status is read from GA4 there, not as tickets)."
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
        from bigas.resources.product.jira_automation.config import JiraAutomationConfig
        from bigas.tickets.service import TicketService

        key_results = normalize_key_results(ticket.get("key_results")) or propose_key_results(ticket)
        for kr in key_results:
            kr["status"] = "committed"
        existing = store.list_tickets_for_parent(key)
        closed = _retire_mechanical_children(store, existing, key_results)
        remaining = [
            t
            for t in store.list_tickets_for_parent(key)
            if is_open_task(t) and not is_mechanical_okr_task(t, key_results)
        ]
        planned = run_okr_plan(ticket, key_results=key_results, existing_tasks=remaining)
        key_results = apply_current_updates(key_results, planned.current_updates)
        created: List[Dict[str, str]] = []
        existing_titles = {(t.get("title") or "").strip().lower() for t in remaining}
        service = TicketService()
        for spec in planned.tasks:
            title = (spec.get("title") or "").strip()
            if not title or title.lower() in existing_titles:
                continue
            labels = ["okr"]
            if spec.get("ai_doable"):
                labels.append("ai-doable")
            child = service.create_ticket(
                ticket["board_id"],
                user_id=None,
                title=title,
                description=spec.get("description") or "",
                status="To Do",
                issue_type="Task",
                labels=labels,
                parent_key=key,
                parent_kr_id=spec.get("kr_id"),
            )
            created.append({"key": child.get("key") or "", "kr_id": spec.get("kr_id") or ""})
            existing_titles.add(title.lower())
        approval = (
            JiraAutomationConfig.from_env().status_design_approval or DESIGN_APPROVAL_STATUS
        )
        created_txt = ", ".join(item["key"] for item in created if item["key"]) or "none"
        closed_txt = ", ".join(closed) if closed else "none"
        if created:
            briefing = planned.briefing or (
                f"Committed {len(key_results)} Key Results and opened {len(created)} work items. "
                f"Review in {approval}. Tasks stay in To Do until a human moves them."
            )
            description = upsert_plan_section(
                ticket.get("description") or "",
                plan_markdown=planned.plan_markdown,
                brief_fallback=str(ticket.get("title") or key),
            )
            store.update_ticket(
                ticket_id,
                key_results=key_results,
                okr_phase="plan",
                okr_briefing=briefing,
                description=description,
                status=approval,
            )
            _comment(
                store,
                ticket_id,
                f"**OKR plan** for {key}: opened {created_txt} "
                f"(closed mechanical tickets: {closed_txt}; model={planned.model or 'n/a'}). "
                f"Moved to {approval} for review.",
            )
            return {
                "ok": True,
                "handler": "okr_plan",
                "issue_key": key,
                "phase": phase,
                "tasks_created": created,
                "mechanical_closed": closed,
                "moved_to": approval,
                "summary": ticket.get("title") or key,
                "model": planned.model,
                "used_llm": planned.used_llm,
            }

        briefing = planned.briefing or (
            "Planning did not open new work items. Left in Design and plan so you can retry."
        )
        description = upsert_plan_section(
            ticket.get("description") or "",
            plan_markdown=planned.plan_markdown,
            brief_fallback=str(ticket.get("title") or key),
        )
        store.update_ticket(
            ticket_id,
            key_results=key_results,
            okr_phase="plan",
            okr_briefing=briefing,
            description=description,
        )
        _comment(
            store,
            ticket_id,
            f"**OKR plan** for {key}: no new work items "
            f"(closed mechanical tickets: {closed_txt}; model={planned.model or 'n/a'}). "
            "Left in Design and plan (AI) so you can retry.",
        )
        return {
            "ok": True,
            "handler": "okr_plan",
            "issue_key": key,
            "phase": phase,
            "tasks_created": created,
            "mechanical_closed": closed,
            "summary": ticket.get("title") or key,
            "model": planned.model,
            "used_llm": planned.used_llm,
        }

    # in_progress
    from bigas.okr.context import gather_okr_evidence

    key_results = normalize_key_results(ticket.get("key_results"))
    children = store.list_tickets_for_parent(key)
    try:
        evidence = gather_okr_evidence(ticket)
        key_results = apply_current_updates(
            key_results,
            heuristic_ga4_currents(key_results, evidence.get("ga4") or ""),
        )
    except Exception:
        logger.warning("OKR pulse could not refresh KR currents for %s", key, exc_info=True)
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
    store.update_ticket(
        ticket_id,
        key_results=key_results,
        okr_phase="in_progress",
        okr_briefing=briefing,
    )
    _comment(store, ticket_id, f"**OKR weekly pulse**\n\n{briefing}")
    return {
        "ok": True,
        "handler": "okr_in_progress",
        "issue_key": key,
        "phase": phase,
        "started": [],
        "progress": objective_progress(key_results),
    }
