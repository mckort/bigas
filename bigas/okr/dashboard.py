"""Assemble the OKR dashboard payload from tickets."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from bigas.okr.model import (
    annotate_key_result,
    cycle_end_for,
    expected_progress,
    is_objective,
    normalize_key_results,
    objective_progress,
)
from bigas.tickets.labels import resolve_ticket_labels
from bigas.tickets.service import ticket_url


def _child_health_counts(objectives: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {
        "on_track": 0,
        "at_risk": 0,
        "off_track": 0,
        "unmeasured": 0,
        "activity_without_outcome": 0,
        "objectives": len(objectives),
        "key_results": 0,
    }
    for obj in objectives:
        for kr in obj.get("key_results") or []:
            counts["key_results"] += 1
            health = kr.get("health") or "unmeasured"
            if health in counts:
                counts[health] += 1
            if kr.get("activity_without_outcome"):
                counts["activity_without_outcome"] += 1
    return counts


def _briefing(objectives: List[Dict[str, Any]], stats: Dict[str, int]) -> Dict[str, Any]:
    risks = []
    next_tasks = []
    unmeasured = []
    theater = []
    for obj in objectives:
        for kr in obj.get("key_results") or []:
            label = f"{obj['key']}: {kr.get('title')}"
            if kr.get("health") in {"at_risk", "off_track"}:
                risks.append(label)
            if kr.get("health") == "unmeasured":
                unmeasured.append(label)
            if kr.get("activity_without_outcome"):
                theater.append(label)
            if kr.get("linked_open", 0) == 0 and kr.get("health") != "on_track":
                next_tasks.append(f"Create a task for {label}")
    headline_parts = []
    if stats["off_track"]:
        headline_parts.append(f"{stats['off_track']} KR(s) off track")
    if stats["unmeasured"]:
        headline_parts.append(f"{stats['unmeasured']} still unmeasured")
    if stats["activity_without_outcome"]:
        headline_parts.append("work is shipping without moving outcomes")
    if not headline_parts:
        headline_parts.append("Cycle is roughly on the expected path")
    return {
        "headline": " · ".join(headline_parts).capitalize() + ".",
        "risks": risks[:6],
        "unmeasured": unmeasured[:6],
        "activity_without_outcome": theater[:6],
        "this_week": next_tasks[:5]
        or [
            "Confirm KR scores with live sources before adding more tasks.",
            "Kill one task that cannot name the KR it is supposed to move.",
        ],
        "principle": (
            "An Objective is healthy only if its Key Results move. "
            "Closed tickets are evidence, not progress. A human signs off KR current."
        ),
    }


def serialize_objective(
    ticket: Dict[str, Any],
    *,
    children: List[Dict[str, Any]],
    board: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    key_results = normalize_key_results(ticket.get("key_results"))
    expected = expected_progress(
        created_at=ticket.get("created_at"),
        cycle_end=cycle_end_for(
            ticket.get("okr_cycle") or "", created_at=ticket.get("created_at")
        ),
    )
    by_kr: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    unlinked: List[Dict[str, Any]] = []
    for child in children:
        kr_id = (child.get("parent_kr_id") or "").strip()
        summary = {
            "key": child.get("key"),
            "ticket_id": child.get("ticket_id"),
            "title": child.get("title"),
            "status": child.get("status"),
            "url": ticket_url(child.get("key") or ""),
            "parent_kr_id": kr_id or None,
            "ai_doable": "ai-doable" in resolve_ticket_labels(child),
            "done_at": child.get("done_at") or "",
            "updated_at": child.get("updated_at") or "",
        }
        if kr_id:
            by_kr[kr_id].append(summary)
        else:
            unlinked.append(summary)
    annotated = [
        {
            **annotate_key_result(
                kr,
                expected=expected,
                child_tickets=[c for c in children if (c.get("parent_kr_id") or "") == kr.get("id")],
            ),
            "tickets": by_kr.get(kr.get("id") or "", []),
        }
        for kr in key_results
    ]
    progress = objective_progress(key_results)
    healths = [kr.get("health") for kr in annotated]
    if "off_track" in healths:
        rollup = "off_track"
    elif "unmeasured" in healths:
        rollup = "unmeasured"
    elif "at_risk" in healths:
        rollup = "at_risk"
    elif annotated:
        rollup = "on_track"
    else:
        rollup = "draft"
    return {
        "ticket_id": ticket.get("ticket_id"),
        "key": ticket.get("key"),
        "title": ticket.get("title"),
        "description": ticket.get("description") or "",
        "status": ticket.get("status"),
        "url": ticket_url(ticket.get("key") or ""),
        "board_id": ticket.get("board_id"),
        "board_name": (board or {}).get("name"),
        "project_key": ticket.get("project_key") or (board or {}).get("project_key"),
        "cycle": ticket.get("okr_cycle") or "",
        "owner": ticket.get("okr_owner") or ticket.get("assignee") or "",
        "phase": ticket.get("okr_phase") or "",
        "briefing": ticket.get("okr_briefing") or "",
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
        "expected_progress": round(expected, 3),
        "progress": None if progress is None else round(progress, 3),
        "health": rollup,
        "key_results": annotated,
        "unlinked_tickets": unlinked,
        "jira_epic_key": ticket.get("key") if (ticket.get("issue_type") or "") == "Epic" else None,
        "issue_type": ticket.get("issue_type"),
    }


def build_okr_dashboard(store, *, user_id: str) -> Dict[str, Any]:
    tickets = store.list_tickets_for_user(user_id)
    board_list = store.list_boards(user_id)
    boards = {b["board_id"]: b for b in board_list}
    children_by_parent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    objectives: List[Dict[str, Any]] = []
    for ticket in tickets:
        parent = (ticket.get("parent_key") or "").strip().upper()
        if parent:
            children_by_parent[parent].append(ticket)
        if is_objective(ticket):
            objectives.append(ticket)

    serialized = [
        serialize_objective(
            obj,
            children=children_by_parent.get((obj.get("key") or "").upper(), []),
            board=boards.get(obj.get("board_id") or ""),
        )
        for obj in objectives
    ]
    serialized.sort(key=lambda item: (item.get("health") != "off_track", item.get("key") or ""))
    by_board: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in serialized:
        by_board[item.get("board_id") or ""].append(item)
    grouped = []
    for board in board_list:
        board_id = board.get("board_id") or ""
        grouped.append(
            {
                "board_id": board_id,
                "name": board.get("name") or board_id,
                "project_key": board.get("project_key") or "",
                "objectives": by_board.get(board_id, []),
            }
        )
    stats = _child_health_counts(serialized)
    cycles = sorted({item.get("cycle") for item in serialized if item.get("cycle")})
    return {
        "prototype": True,
        "cycle": cycles[0] if len(cycles) == 1 else " / ".join(cycles) or "Current cycle",
        "stats": stats,
        "briefing": _briefing(serialized, stats),
        "boards": grouped,
        "objectives": serialized,
    }


def kr_options_for_parent(store, parent_key: str) -> List[Dict[str, str]]:
    ticket = store.get_ticket_by_key(parent_key)
    if not ticket:
        return []
    return [
        {"id": kr["id"], "title": kr["title"]}
        for kr in normalize_key_results(ticket.get("key_results"))
        if kr.get("id")
    ]
