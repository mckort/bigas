"""Seed a realistic OKR cycle so the dashboard is clickable locally."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from bigas.okr.model import normalize_key_results, promote_objective_type

DEMO_LABEL = "okr-demo"
DEMO_CYCLE = "2026-Q3"


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _seed_boards(store, user_id: str) -> List[Dict[str, Any]]:
    boards = store.list_boards(user_id)
    project = [b for b in boards if b.get("project_key")]
    return project or boards


def demo_already_seeded(store, user_id: str) -> bool:
    from bigas.okr.model import is_objective

    for ticket in store.list_tickets_for_user(user_id):
        if is_objective(ticket) and DEMO_LABEL in (ticket.get("labels") or []):
            return True
    return False


def _specs() -> List[Dict[str, Any]]:
    return [
        {
            "title": "Make Bigas the operating system a solo founder actually stays in",
            "description": (
                "Outcome, not output: founders who run their week through Bigas "
                "(chat + board + AI columns) without bouncing back to a pile of docs.\n\n"
                "This is a prototype Objective. Drag it through Research / Design / In Progress "
                "to see the OKR pipeline, or inspect the dashboard."
            ),
            "status": "In Progress (AI)",
            "okr_phase": "in_progress",
            "okr_owner": "Chief of Staff",
            "okr_briefing": (
                "Two KRs are on track, activation is lagging expected pace, and NPS is still unmeasured. "
                "This week: instrument the pulse and stop shipping board polish that does not move activation."
            ),
            "created_days_ago": 38,
            "key_results": [
                {
                    "id": "kr-demo01aa",
                    "title": "40 weekly active founders",
                    "metric": "Weekly active founders",
                    "unit": "founders",
                    "baseline": 12,
                    "target": 40,
                    "current": 24,
                    "source": "ga4",
                    "measurable": True,
                    "status": "committed",
                    "ai_note": "On a linear Q3 path we should be near 24. Live GA4 event is still a proxy (sessions with chat).",
                },
                {
                    "id": "kr-demo01ab",
                    "title": "25% activate within 7 days of signup",
                    "metric": "7-day activation rate",
                    "unit": "%",
                    "baseline": 8,
                    "target": 25,
                    "current": 11,
                    "source": "ga4",
                    "measurable": True,
                    "status": "committed",
                    "ai_note": "Behind expected. Lots of onboard tickets closed — activity without outcome.",
                },
                {
                    "id": "kr-demo01ac",
                    "title": "Founder NPS ≥ 40 from a weekly pulse",
                    "metric": "NPS",
                    "unit": "score",
                    "baseline": 0,
                    "target": 40,
                    "current": 0,
                    "source": "unknown",
                    "measurable": False,
                    "measurement_gap": "No survey exists. Need a 1-question pulse after the weekly check-in before this can score.",
                    "status": "committed",
                },
            ],
            "tasks": [
                {
                    "title": "Define activation as first AI column drag",
                    "kr": "kr-demo01ab",
                    "status": "Done",
                    "labels": ["okr"],
                },
                {
                    "title": "Rewrite empty-board onboarding copy",
                    "kr": "kr-demo01ab",
                    "status": "Done",
                    "labels": ["okr"],
                },
                {
                    "title": "Add a third empty-state illustration",
                    "kr": "kr-demo01ab",
                    "status": "Done",
                    "labels": ["okr"],
                },
                {
                    "title": "Ship 1-question NPS pulse after weekly check-in",
                    "kr": "kr-demo01ac",
                    "status": "To Do",
                    "labels": ["okr", "ai-doable"],
                },
                {
                    "title": "Weekly active event in GA4 (chat or board, not raw sessions)",
                    "kr": "kr-demo01aa",
                    "status": "In Progress (AI)",
                    "labels": ["okr", "ai-doable"],
                },
            ],
        },
        {
            "title": "Prove a marketing flywheel we can actually measure",
            "description": (
                "Distribution should produce a number we trust weekly — not a folder of reports. "
                "KRs mix leading (content shipped) and lagging (qualified signups)."
            ),
            "status": "Design and plan (AI)",
            "okr_phase": "plan",
            "okr_owner": "Marketing",
            "okr_briefing": (
                "KRs are committed. Instrumentation for Reddit/LinkedIn → signup is the missing piece. "
                "Do not create more content tickets until the signup KR can move."
            ),
            "created_days_ago": 20,
            "key_results": [
                {
                    "id": "kr-demo02aa",
                    "title": "120 qualified signups from owned + paid in Q3",
                    "metric": "Qualified signups",
                    "unit": "signups",
                    "baseline": 18,
                    "target": 120,
                    "current": 31,
                    "source": "ga4",
                    "measurable": True,
                    "status": "committed",
                },
                {
                    "id": "kr-demo02ab",
                    "title": "CAC under 400 SEK on the winning channel",
                    "metric": "CAC",
                    "unit": "SEK",
                    "baseline": 720,
                    "target": 400,
                    "current": 610,
                    "source": "ads",
                    "direction": "decrease",
                    "measurable": True,
                    "status": "committed",
                    "ai_note": "At risk. LinkedIn spend is the drag; Reddit is cheaper but volume is thin.",
                },
                {
                    "id": "kr-demo02ac",
                    "title": "8 founder-facing posts with a tracked UTM",
                    "metric": "Tracked posts",
                    "unit": "posts",
                    "baseline": 1,
                    "target": 8,
                    "current": 5,
                    "source": "manual",
                    "measurable": True,
                    "status": "committed",
                },
            ],
            "tasks": [
                {
                    "title": "UTM dictionary for X / LinkedIn / Reddit",
                    "kr": "kr-demo02ac",
                    "status": "Done",
                    "labels": ["okr", "marketing"],
                },
                {
                    "title": "Pause LinkedIn campaigns with CAC > 800 SEK",
                    "kr": "kr-demo02ab",
                    "status": "To Do",
                    "labels": ["okr", "marketing"],
                },
                {
                    "title": "Join ad click → signup in GA4",
                    "kr": "kr-demo02aa",
                    "status": "To Do",
                    "labels": ["okr", "ai-doable", "marketing"],
                },
            ],
        },
        {
            "title": "AI delivery that a founder can leave overnight",
            "description": (
                "Reliability objective: if a card is in In Progress (AI), it should either "
                "finish, ask a question, or fail loudly — never stall silently."
            ),
            "status": "Research and describe (AI)",
            "okr_phase": "research",
            "okr_owner": "CTO",
            "okr_briefing": (
                "Research proposed 3 KRs. One is not measurable until we log automation skips. "
                "Confirm the KRs, then drag to Design and plan to open concrete work toward them."
            ),
            "created_days_ago": 5,
            "key_results": [
                {
                    "id": "kr-demo03aa",
                    "title": "Median ticket-to-merge under 48 hours",
                    "metric": "Hours to merge",
                    "unit": "hours",
                    "baseline": 96,
                    "target": 48,
                    "current": 96,
                    "source": "github",
                    "direction": "decrease",
                    "measurable": True,
                    "status": "proposed",
                    "ai_note": "Needs a join of board timestamps and GitHub merged_at. Not wired yet.",
                },
                {
                    "id": "kr-demo03ab",
                    "title": "80% of failed deploys recovered without a human",
                    "metric": "Unattended recovery rate",
                    "unit": "%",
                    "baseline": 20,
                    "target": 80,
                    "current": 20,
                    "source": "github",
                    "measurable": True,
                    "status": "proposed",
                },
                {
                    "id": "kr-demo03ac",
                    "title": "Zero silent AI-column stalls per week",
                    "metric": "Silent stalls",
                    "unit": "stalls",
                    "baseline": 4,
                    "target": 0,
                    "current": 4,
                    "source": "unknown",
                    "direction": "decrease",
                    "measurable": False,
                    "measurement_gap": "Define 'stall' (no comment + same column > 6h) and log it. Until then this KR is a vibe.",
                    "status": "proposed",
                },
            ],
            "tasks": [],
        },
    ]


def seed_okr_demo(store, *, user_id: str) -> Dict[str, Any]:
    """Create demo objectives + KR-linked tasks, one set per project board when possible."""
    from bigas.tickets.service import TicketService

    store.ensure_default_boards(user_id)
    boards = _seed_boards(store, user_id)
    if not boards:
        raise ValueError("no board available to seed")

    service = TicketService()
    created_objectives: List[str] = []
    created_tasks: List[str] = []
    extra_titles = [
        "Trim signup email to one CTA",
        "Cut first-run checklist from 8 steps to 3",
        "Add sample board for new founders",
        "Fix broken invite link on mobile",
        "Rename first chat prompt to a job-to-be-done",
        "Hide unused sidebar links on first session",
        "Preload demo tickets after signup",
        "Show a 60-second product clip on empty board",
        "Ask for the one outcome in onboarding",
        "Skip billing until first AI run",
        "Pin the weekly check-in in chat",
        "Log activation event from first column drag",
    ]

    for index, spec in enumerate(_specs()):
        board = boards[index % len(boards)]
        labels = [DEMO_LABEL, "objective"]
        ticket = service.create_ticket(
            board["board_id"],
            user_id=user_id,
            title=spec["title"],
            description=spec["description"],
            status=spec["status"],
            issue_type=promote_objective_type("Objective", labels),
            labels=labels,
            okr_cycle=DEMO_CYCLE,
            okr_owner=spec["okr_owner"],
            okr_briefing=spec["okr_briefing"],
            okr_phase=spec["okr_phase"],
            key_results=normalize_key_results(spec["key_results"]),
        )
        store.update_ticket(
            ticket["ticket_id"],
            user_id=user_id,
            created_at=_days_ago(spec["created_days_ago"]),
        )
        created_objectives.append(ticket["key"])
        tasks = list(spec["tasks"])
        if index == 0:
            for title in extra_titles:
                tasks.append(
                    {
                        "title": title,
                        "kr": "kr-demo01ab",
                        "status": "Done" if title.startswith(("Trim", "Cut", "Add")) else "To Do",
                        "labels": ["okr"],
                    }
                )
        for task in tasks:
            child = service.create_ticket(
                board["board_id"],
                user_id=user_id,
                title=task["title"],
                description=f"Linked to KR `{task['kr']}` on {ticket['key']}.",
                status=task["status"],
                issue_type="Task",
                labels=task.get("labels") or ["okr"],
                parent_key=ticket["key"],
                parent_kr_id=task["kr"],
            )
            created_tasks.append(child["key"])

    return {
        "board_id": boards[0]["board_id"],
        "board_name": boards[0].get("name"),
        "boards": [b.get("name") for b in boards],
        "cycle": DEMO_CYCLE,
        "objectives": created_objectives,
        "tasks": created_tasks,
    }
