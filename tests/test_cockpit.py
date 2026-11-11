"""Tests for the cockpit read model and /api/cockpit."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from app import create_app
from bigas.cockpit import build_cockpit
from bigas.tickets import store as ticket_store_module
from bigas.tickets.release_store import reset_release_store_for_tests

_JIRA_ENV_KEYS = (
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_PROJECT_KEY",
    "JIRA_PROJECT_KEYS",
    "USE_INTERNAL_BOARD",
)


BOARD = {"board_id": "b1", "project_key": "VFA", "name": "VC Field Assistant"}
PERSONAL = {"board_id": "p1", "project_key": None, "name": "Personal tasks"}


def _ticket(tid, status, **extra):
    return {
        "ticket_id": tid,
        "board_id": "b1",
        "key": f"VFA-{tid}",
        "title": f"Ticket {tid}",
        "status": status,
        "issue_type": "Task",
        "updated_at": extra.pop("updated_at", f"2026-11-0{tid}T10:00:00+00:00"),
        **extra,
    }


def test_manual_columns_become_needs_with_next_and_back_columns():
    payload = build_cockpit(
        boards=[BOARD, PERSONAL],
        tickets_by_board={
            "b1": [
                _ticket("1", "Description approval (manual)"),
                _ticket("2", "Design approval (manual)"),
                _ticket("3", "Final approval (manual)"),
                _ticket("4", "To Do"),
                _ticket("5", "Done"),
            ],
            "p1": [_ticket("9", "Review")],
        },
        threads=[],
    )
    by_key = {item["ticket"]["key"]: item for item in payload["needs"]}
    assert set(by_key) == {"VFA-1", "VFA-2", "VFA-3"}
    assert by_key["VFA-1"]["approve_status"] == "Design and plan (AI)"
    assert by_key["VFA-1"]["send_back_status"] == "Research and describe (AI)"
    assert by_key["VFA-2"]["approve_status"] == "In Progress (AI)"
    assert by_key["VFA-3"]["approve_status"] == "Done"
    assert by_key["VFA-3"]["send_back_status"] == "In Progress (AI)"
    # Oldest waiting card first.
    assert [i["ticket"]["key"] for i in payload["needs"]] == ["VFA-1", "VFA-2", "VFA-3"]
    assert payload["counts"]["needs"] == 3


def test_ai_columns_become_working_and_epics_are_skipped():
    payload = build_cockpit(
        boards=[BOARD],
        tickets_by_board={
            "b1": [
                _ticket("1", "Research and describe (AI)"),
                _ticket("2", "In Progress (AI)", review={"pr_url": "https://github.com/x/y/pull/7"}),
                _ticket("3", "Design approval (manual)", issue_type="Epic"),
            ]
        },
        threads=[],
    )
    stages = {i["ticket"]["key"]: i["stage"] for i in payload["working"]}
    assert stages == {"VFA-1": "Researching", "VFA-2": "Implementing"}
    pr = next(i for i in payload["working"] if i["ticket"]["key"] == "VFA-2")
    assert pr["ticket"]["pr_url"] == "https://github.com/x/y/pull/7"
    assert payload["needs"] == []


def test_devops_thread_state_feeds_needs_working_and_ship():
    threads = [
        {
            "thread_id": "t1",
            "agent_id": "devops",
            "updated_at": "2026-11-11T12:00:00+00:00",
            "pending_deploy": {
                "kind": "prepare",
                "project_key": "VFA",
                "version": "0.2.4",
                "risk_level": "medium",
            },
        },
        {
            "thread_id": "t2",
            "agent_id": "devops",
            "pending_deploy_poll": {
                "kind": "gpw",
                "phase": "prepare_staging",
                "project_key": "GPW-PROD",
                "triggered": [{"html_url": "https://github.com/run/1"}],
                "started_at": "2026-11-11T12:05:00+00:00",
            },
        },
        {"thread_id": "t3", "agent_id": "chief", "pending_deploy": {"project_key": "BIG"}},
    ]
    payload = build_cockpit(
        boards=[BOARD],
        tickets_by_board={
            "b1": [
                _ticket("1", "Done", fix_version="0.2.4"),
                _ticket("2", "Final approval (manual)", fix_version="0.2.4"),
                _ticket("3", "In Progress (AI)", fix_version="0.2.4"),
            ]
        },
        threads=threads,
        projects=[{"key": "VFA", "name": "VC Field Assistant"}],
        command_groups=[
            {
                "key": "GPW-PROD",
                "name": "Green Promo Wear",
                "commands": [{"label": "Prepare staging", "prompt": "prepare staging GPW-PROD"}],
            }
        ],
        releases_by_project={
            "VFA": [
                {"name": "0.2.3", "released": True},
                {"name": "0.2.4", "is_default": True},
            ]
        },
    )
    deploy = payload["needs"][0]
    assert deploy["type"] == "deploy_confirm"
    assert deploy["thread_id"] == "t1"
    assert deploy["title"] == "Version 0.2.4"
    assert deploy["risk_level"] == "medium"
    assert all(i.get("project_key") != "BIG" for i in payload["needs"])

    pipeline = next(i for i in payload["working"] if i["type"] == "pipeline")
    assert pipeline["stage"] == "Building staging"
    assert pipeline["run_url"] == "https://github.com/run/1"

    ship = {row["key"]: row for row in payload["ship"]}
    assert ship["GPW-PROD"]["mode"] == "staging"
    assert ship["GPW-PROD"]["state"] == "running"
    assert ship["VFA"]["version"] == "0.2.4"
    assert ship["VFA"]["cut_count"] == 2
    assert ship["VFA"]["state"] == "waiting"
    assert ship["VFA"]["commands"][0]["prompt"] == "prepare deploy VFA 0.2.4"


def test_deploy_project_keys_are_normalized_to_uppercase():
    payload = build_cockpit(
        boards=[BOARD],
        tickets_by_board={"b1": []},
        threads=[
            {
                "thread_id": "t1",
                "agent_id": "devops",
                "pending_deploy": {"project_key": "gpw-prod", "version": "1.0"},
            },
            {
                "thread_id": "t2",
                "agent_id": "devops",
                "pending_deploy_poll": {"project_key": "vfa", "phase": "backup"},
            },
            {
                "thread_id": "t3",
                "agent_id": "devops",
                "pending_prepare_poll": {"project_key": "Big", "phase": "autofix", "version": "0.1"},
            },
        ],
        command_groups=[{"key": "gpw-prod", "name": "GPW", "commands": []}],
    )
    keys = [item["project_key"] for item in payload["needs"] + payload["working"]]
    assert keys == ["GPW-PROD", "VFA", "BIG"]
    ship = {row["key"]: row for row in payload["ship"]}
    assert ship["GPW-PROD"]["state"] == "waiting"


def test_load_cockpit_degrades_when_board_list_fails(monkeypatch):
    class _Service:
        def list_boards(self, user_id):
            raise RuntimeError("jira down")

        def list_tickets(self, *args, **kwargs):
            raise AssertionError("tickets should not be listed")

    monkeypatch.setattr("bigas.tickets.service.TicketService", lambda: _Service())
    monkeypatch.setattr(
        "bigas.chat.db.get_chat_store",
        lambda: type("Store", (), {"list_threads": lambda self, user_id: []})(),
    )
    monkeypatch.setattr("bigas.resources.devops.prepare.list_shortcut_projects", lambda: [])
    monkeypatch.setattr("bigas.resources.devops.gpw_pipeline.list_gpw_command_shortcuts", lambda: [])

    from bigas.cockpit import load_cockpit

    payload = load_cockpit("user-1")
    assert payload["needs"] == []
    assert payload["working"] == []
    assert payload["ship"] == []
    assert payload["counts"] == {"needs": 0, "working": 0}


@pytest.fixture
def client(monkeypatch):
    for key in _JIRA_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    ticket_store_module._store = None
    reset_release_store_for_tests()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    ticket_store_module._store = None
    reset_release_store_for_tests()


def _auth():
    return {"Authorization": "Bearer test-dev-token", "Content-Type": "application/json"}


def test_cockpit_api_lists_cards_waiting_for_approval(client):
    assert client.get("/api/cockpit").status_code == 401

    boards = client.get("/api/boards", headers=_auth()).get_json()["boards"]
    project_board = next(b for b in boards if b.get("project_key"))
    created = client.post(
        f"/api/boards/{project_board['board_id']}/tickets",
        headers=_auth(),
        data=json.dumps({"title": "Share link", "status": "Description approval (manual)"}),
    )
    assert created.status_code == 201

    resp = client.get("/api/cockpit", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    titles = [i["ticket"]["title"] for i in body["needs"] if i["type"] == "ticket"]
    assert "Share link" in titles
    assert set(body) >= {"needs", "working", "ship", "counts"}


def test_cockpit_spa_route_is_served(client, monkeypatch):
    monkeypatch.setitem(client.application.config, "BIGAS_ACCESS_MODE", "restricted")
    monkeypatch.setitem(client.application.config, "BIGAS_ACCESS_KEYS", {"secret-key"})
    monkeypatch.setitem(client.application.config, "BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
    assert client.get("/cockpit").status_code != 403
    assert client.get("/chat").status_code != 403
    assert client.get("/api/cockpit", headers=_auth()).status_code == 200
