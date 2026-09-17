"""Reasoned next steps for red Key Results."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("BIGAS_ACCESS_MODE", "open")

from bigas.okr.next_steps import (
    collect_red_kr_next_steps,
    flatten_red_kr_next_steps,
    reason_kr_next_steps,
)
from bigas.tickets import store as ticket_store_module
from bigas.tickets.store import get_ticket_store


@pytest.fixture(autouse=True)
def _reset_store():
    ticket_store_module._store = None
    yield
    ticket_store_module._store = None


def test_reason_points_at_gate_not_create_task():
    kr = {
        "id": "kr-fresh",
        "title": "3% add-to-cart",
        "health": "off_track",
        "measurable": True,
        "source": "ga4",
        "current": 2.5,
        "target": 3.0,
        "updated_at": "2026-09-17T00:00:00+00:00",
        "tickets": [
            {
                "key": "GPWW-22",
                "title": "Approve landing copy",
                "status": "Description approval (manual)",
            }
        ],
    }
    steps = reason_kr_next_steps(kr, objective_key="GPWW-17")
    assert steps
    assert any("gate" in s.lower() and "GPWW-22" in s for s in steps)
    assert not any("create a task" in s.lower() for s in steps)


def test_done_work_does_not_stop_red_kr_steps():
    kr = {
        "id": "kr-orders",
        "title": "10 wholesale orders",
        "health": "off_track",
        "measurable": True,
        "source": "ga4",
        "current": 2,
        "target": 10,
        "updated_at": "2026-08-01T00:00:00+00:00",
        "tickets": [
            {"key": "GPWW-30", "title": "Instrument orders", "status": "Done"},
        ],
    }
    steps = reason_kr_next_steps(kr, objective_key="GPWW-17", stale_days=7)
    assert any("done" in s.lower() for s in steps)
    assert any("stale" in s.lower() or "verify" in s.lower() for s in steps)


def test_collect_limits_three_steps_per_kr():
    objectives = [
        {
            "key": "GPWW-17",
            "key_results": [
                {
                    "id": "kr-a",
                    "title": "KR A",
                    "health": "at_risk",
                    "measurable": True,
                    "current": 1,
                    "target": 10,
                    "updated_at": "2026-08-01T00:00:00+00:00",
                    "tickets": [],
                }
            ],
        }
    ]
    entries = collect_red_kr_next_steps(objectives, stale_days=7)
    assert len(entries) == 1
    assert len(entries[0]["steps"]) <= 3
    flat = flatten_red_kr_next_steps(entries)
    assert flat[0].startswith("GPWW-17:")


def test_pulse_scoreboard_includes_red_kr_steps():
    from bigas.okr.scoreboard import build_okr_scoreboard
    from bigas.tickets.store import get_ticket_store

    store = get_ticket_store()
    board = store.create_board("dev-user", name="GPWW", project_key="GPWW")
    objective = store.create_ticket(
        board["board_id"],
        title="Grow",
        issue_type="Objective",
        user_id="dev-user",
        key_results=[
            {
                "id": "kr-1",
                "title": "Founder NPS",
                "measurable": False,
                "measurement_gap": "No weekly pulse survey yet",
            }
        ],
    )
    store.create_ticket(
        board["board_id"],
        title="Approve survey copy",
        user_id="dev-user",
        parent_key=objective["key"],
        parent_kr_id="kr-1",
        status="Description approval (manual)",
    )
    snapshot = build_okr_scoreboard(store, user_id="dev-user", use_cache=False)
    steps = snapshot.get("briefing", {}).get("red_kr_steps") or []
    assert steps
    assert not any("Create a task" in line for line in snapshot["briefing"].get("this_week") or [])
