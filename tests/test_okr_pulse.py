"""OKR chat priming and mechanical Monday pulse."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from app import create_app
from bigas.agents.chief_of_staff import _agent_system_prompt
from bigas.okr.priming import format_okr_priming_block, okr_priming_block_for_agent
from bigas.okr.pulse import format_okr_pulse
from bigas.okr.scoreboard import (
    build_okr_scoreboard,
    clear_okr_scoreboard_cache,
    is_stale_current,
)
from bigas.tickets import store as ticket_store_module
from bigas.tickets.store import get_ticket_store

USER = "dev-user"


@pytest.fixture(autouse=True)
def _reset_stores(monkeypatch):
    for key in (
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
        "JIRA_PROJECT_KEY",
        "JIRA_PROJECT_KEYS",
        "USE_INTERNAL_BOARD",
        "SECRET_MANAGER",
        "BIGAS_ACCESS_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CHAT_STORAGE_MODE", "memory")
    monkeypatch.setenv("BIGAS_ACCESS_MODE", "open")
    ticket_store_module._store = None
    clear_okr_scoreboard_cache()
    yield
    ticket_store_module._store = None
    clear_okr_scoreboard_cache()


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["BIGAS_ACCESS_MODE"] = "open"
    with app.test_client() as c:
        yield c


def _seed_objective(*, linked_done: bool = True, unlinked_done: bool = True):
    store = get_ticket_store()
    board = store.create_board(USER, name="GPWW", project_key="GPWW")
    now = datetime.now(timezone.utc).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    objective = store.create_ticket(
        board["board_id"],
        title="Grow wholesale",
        issue_type="Objective",
        user_id=USER,
        okr_cycle="2026-Q3",
        key_results=[
            {
                "id": "kr-orders01",
                "title": "10 wholesale orders",
                "baseline": 1,
                "target": 10,
                "current": 2,
                "source": "ga4",
                "measurable": True,
                "updated_at": stale,
            },
            {
                "id": "kr-fresh001",
                "title": "3% add-to-cart",
                "baseline": 1.0,
                "target": 3.0,
                "current": 2.5,
                "source": "ga4",
                "measurable": True,
                "updated_at": now,
            },
            {
                "id": "kr-nps00001",
                "title": "NPS from customers",
                "measurable": False,
                "measurement_gap": "No survey yet",
            },
        ],
    )
    if linked_done:
        store.create_ticket(
            board["board_id"],
            title="Instrument orders event",
            user_id=USER,
            parent_key=objective["key"],
            parent_kr_id="kr-orders01",
            status="Done",
        )
    waiting = store.create_ticket(
        board["board_id"],
        title="Approve landing copy",
        user_id=USER,
        parent_key=objective["key"],
        parent_kr_id="kr-fresh001",
        status="Description approval (manual)",
    )
    if unlinked_done:
        store.create_ticket(
            board["board_id"],
            title="Random refactor",
            user_id=USER,
            parent_key=objective["key"],
            status="Done",
        )
    return store, objective, waiting


def test_empty_scoreboard_cannot_look_clean():
    store = get_ticket_store()
    store.create_board(USER, name="Empty", project_key="GPWW")
    snapshot = build_okr_scoreboard(store, user_id=USER, use_cache=False)
    pulse = format_okr_pulse(snapshot)
    priming = format_okr_priming_block(snapshot)
    assert "Sample: 0 Objective" in pulse
    assert "sample size 0" in pulse.lower() or "Sample size 0" in pulse
    assert "Cannot report on track" in pulse
    assert "No live Objectives" in priming
    assert "on track" not in pulse.split("KR health")[0].lower()


def test_stale_current_and_unlinked_done_are_visible():
    store, objective, waiting = _seed_objective()
    snapshot = build_okr_scoreboard(store, user_id=USER, use_cache=False)
    assert snapshot["objective_count"] == 1
    assert snapshot["done_total"] == 2
    assert snapshot["done_linked"] == 1
    assert snapshot["done_unlinked"] == 1
    assert snapshot["unlinked_done"] == 1
    assert any("10 wholesale orders" in item for item in snapshot["stale_krs"])
    assert any(g.get("key") == waiting["key"] for g in snapshot["pending_gates"])
    pulse = format_okr_pulse(snapshot)
    assert "sample size 2" in pulse
    assert "unlinked 1" in pulse
    assert "Pending human gates: 1" in pulse
    priming = format_okr_priming_block(snapshot)
    assert objective["key"] in priming
    assert "stale" in priming
    assert "Do not celebrate" in priming


def test_fresh_kr_is_not_stale():
    now = datetime.now(timezone.utc).isoformat()
    assert not is_stale_current(
        {"updated_at": now, "current": 2}, stale_days=7
    )
    assert is_stale_current({"current": 2}, stale_days=7)


def test_priming_is_injected_for_chief_not_cto():
    _seed_objective()
    chief = _agent_system_prompt(
        {"agent_id": "chief", "system_prompt_goals": "Coordinate."},
        user_id=USER,
    )
    cto = _agent_system_prompt(
        {"agent_id": "cto", "system_prompt_goals": "Review PRs."},
        user_id=USER,
    )
    assert "Live Objectives" in chief
    assert "10 wholesale orders" in chief
    assert "Live Objectives" not in cto
    assert okr_priming_block_for_agent("marketing", user_id=USER)
    assert not okr_priming_block_for_agent("devops", user_id=USER)


def test_weekly_okr_pulse_endpoint_posts_numbers(client, monkeypatch):
    _seed_objective()
    monkeypatch.setattr(
        "bigas.okr.pulse.comment_on_okr_pulse", lambda numbers: None
    )
    posted = {}

    def fake_publish(message, *, post_to_discord, post_to_chat):
        posted["message"] = message
        posted["discord"] = post_to_discord
        posted["chat"] = post_to_chat
        return {"posted_to_discord": False, "posted_to_chat": True}

    monkeypatch.setattr("bigas.okr.pulse.publish_weekly_okr_pulse", fake_publish)
    resp = client.post(
        "/mcp/tools/weekly_okr_pulse",
        data=json.dumps(
            {
                "include_comment": False,
                "post_to_discord": False,
                "user_id": USER,
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["comment"] is None
    assert "sample size 2" in body["numbers"]
    assert "cannot flatter" in body["numbers"]
    assert posted["message"] == body["message"]
    assert posted["chat"] is True


def test_manifest_includes_weekly_okr_pulse():
    from bigas.resources.product.endpoints import get_manifest

    tools = {t["name"]: t for t in get_manifest()["tools"]}
    assert tools["weekly_okr_pulse"]["path"] == "/mcp/tools/weekly_okr_pulse"
