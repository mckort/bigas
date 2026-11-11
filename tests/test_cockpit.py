"""Tests for the cockpit read model and /api/cockpit."""
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
    assert set(by_key) == {"VFA-1", "VFA-2"}
    assert by_key["VFA-1"]["approve_status"] == "Design and plan (AI)"
    assert by_key["VFA-1"]["send_back_status"] == "Research and describe (AI)"
    assert by_key["VFA-2"]["approve_status"] == "In Progress (AI)"
    # Oldest waiting card first.
    assert [i["ticket"]["key"] for i in payload["needs"]] == ["VFA-1", "VFA-2"]
    assert payload["counts"]["needs"] == 2
    # Final approval is shipped work: listed apart, not as something blocking.
    assert [i["ticket"]["key"] for i in payload["signoff"]] == ["VFA-3"]
    assert payload["signoff"][0]["approve_status"] == "Done"
    assert payload["counts"]["signoff"] == 1


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
        now=datetime(2026, 11, 2, 9, tzinfo=timezone.utc),
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
        now=datetime(2026, 11, 11, 12, 30, tzinfo=timezone.utc),
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
    assert payload["counts"] == {"needs": 0, "working": 0, "stuck": 0, "signoff": 0}


def test_load_cockpit_keeps_later_release_modes_when_one_fails(monkeypatch):
    class _Service:
        def list_boards(self, user_id):
            return [
                {"board_id": "b1", "project_key": "ACME", "name": "Acme"},
                {"board_id": "b2", "project_key": "VFA", "name": "VFA"},
            ]

        def list_tickets(self, *args, **kwargs):
            return []

    monkeypatch.setattr("bigas.tickets.service.TicketService", lambda: _Service())
    monkeypatch.setattr(
        "bigas.chat.db.get_chat_store",
        lambda: type("Store", (), {"list_threads": lambda self, user_id: []})(),
    )
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.list_shortcut_projects",
        lambda: [{"key": "ACME", "name": "Acme"}, {"key": "VFA", "name": "VFA"}],
    )
    monkeypatch.setattr("bigas.resources.devops.gpw_pipeline.list_gpw_command_shortcuts", lambda: [])

    def release_state(key):
        if key == "ACME":
            raise RuntimeError("jira config")
        return {"mode": "development"}

    monkeypatch.setattr("bigas.go_live.release_state", release_state)

    from bigas.cockpit import load_cockpit

    modes = {row["key"]: row["release_mode"] for row in load_cockpit("user-1")["ship"]}
    assert modes["ACME"] == "production"
    assert modes["VFA"] == "development"


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


def test_sign_off_closes_final_approval_cards_with_one_note(client, monkeypatch):
    import bigas.chat.activity as activity

    notes = []
    monkeypatch.setattr(activity, "post_to_agent_thread", lambda agent, text, **kw: notes.append((agent, text)))
    monkeypatch.setattr(activity, "mirror_to_activity_feed", lambda *a, **kw: None)

    boards = client.get("/api/boards", headers=_auth()).get_json()["boards"]
    board = next(b for b in boards if b.get("project_key"))
    ids = []
    for title, status in (
        ("Shipped A", "Final approval (manual)"),
        ("Shipped B", "Final approval (manual)"),
        ("Still planning", "Design approval (manual)"),
    ):
        resp = client.post(
            f"/api/boards/{board['board_id']}/tickets",
            headers=_auth(),
            data=json.dumps({"title": title, "status": status}),
        )
        ids.append(resp.get_json()["ticket"]["ticket_id"])

    body = client.get("/api/cockpit", headers=_auth()).get_json()
    assert body["counts"]["signoff"] == 2
    assert all(i["ticket"]["title"] != "Shipped A" for i in body["needs"])

    resp = client.post("/api/cockpit/sign-off", headers=_auth(), data=json.dumps({"ticket_ids": ids}))
    assert resp.status_code == 200
    result = resp.get_json()
    assert len(result["closed"]) == 2
    assert [s["reason"] for s in result["skipped"]] == ["in Design approval (manual)"]

    for ticket_id in ids[:2]:
        ticket = client.get(f"/api/tickets/{ticket_id}", headers=_auth()).get_json()["ticket"]
        assert ticket["status"] == "Done"
    # One summary note for the batch, none per card.
    product_notes = [text for agent, text in notes if agent == "product"]
    assert len(product_notes) == 1
    assert "Marked 2 shipped cards done" in product_notes[0]
    assert client.get("/api/cockpit", headers=_auth()).get_json()["counts"]["signoff"] == 0


def test_sign_off_validates_input(client):
    for payload in ({}, {"ticket_ids": []}, {"ticket_ids": "abc"}, {"ticket_ids": [1]}):
        resp = client.post("/api/cockpit/sign-off", headers=_auth(), data=json.dumps(payload))
        assert resp.status_code == 400
    assert client.post("/api/cockpit/sign-off", data=json.dumps({"ticket_ids": ["x"]})).status_code == 401


def test_old_ai_cards_and_pipelines_are_stuck_not_working():
    now = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat()
    old = (now - timedelta(days=52)).isoformat()
    payload = build_cockpit(
        boards=[BOARD],
        tickets_by_board={
            "b1": [
                _ticket("1", "In Progress (AI)", updated_at=recent),
                _ticket("2", "Design and plan (AI)", updated_at=old),
            ]
        },
        threads=[
            {
                "thread_id": "t9",
                "agent_id": "devops",
                "pending_prepare_poll": {"project_key": "VFA", "phase": "autofix", "started_at": (now - timedelta(hours=8)).isoformat()},
            }
        ],
        now=now,
    )
    assert [i["ticket"]["key"] for i in payload["working"]] == ["VFA-1"]
    stuck = {i.get("ticket", {}).get("key") or i["project_key"]: i for i in payload["stuck"]}
    assert set(stuck) == {"VFA-2", "VFA"}
    assert all(i["stuck"] for i in payload["stuck"])
    assert payload["counts"]["stuck"] == 2


def test_build_cockpit_treats_naive_now_as_utc():
    now = datetime(2026, 11, 11, 12)
    recent = datetime(2026, 11, 11, 10, tzinfo=timezone.utc).isoformat()
    old = datetime(2026, 11, 9, 11, tzinfo=timezone.utc).isoformat()
    payload = build_cockpit(
        boards=[BOARD],
        tickets_by_board={
            "b1": [
                _ticket("1", "In Progress (AI)", updated_at=recent),
                _ticket("2", "Design and plan (AI)", updated_at=old),
                _ticket("3", "Done", updated_at=recent),
            ]
        },
        threads=[],
        now=now,
    )
    assert [i["ticket"]["key"] for i in payload["working"]] == ["VFA-1"]
    assert [i["ticket"]["key"] for i in payload["stuck"]] == ["VFA-2"]
    assert payload["week"]["shipped"] == 1


def test_week_stats_count_recently_shipped_cards_and_releases():
    now = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
    payload = build_cockpit(
        boards=[BOARD],
        tickets_by_board={
            "b1": [
                _ticket("1", "Done", updated_at=(now - timedelta(days=1)).isoformat()),
                _ticket("2", "Final approval (manual)", updated_at=(now - timedelta(days=3)).isoformat()),
                _ticket("3", "Done", updated_at=(now - timedelta(days=30)).isoformat()),
                _ticket("4", "In Progress (AI)", updated_at=(now - timedelta(hours=1)).isoformat()),
            ]
        },
        threads=[],
        releases_by_project={
            "VFA": [
                {"name": "0.2.3", "released": True, "released_at": (now - timedelta(days=2)).isoformat()},
                {"name": "0.2.2", "released": True, "released_at": (now - timedelta(days=20)).isoformat()},
                {"name": "0.2.4"},
            ]
        },
        now=now,
    )
    assert payload["week"] == {"shipped": 2, "shipped_by_project": {"VFA": 2}, "releases": ["VFA 0.2.3"]}


def test_ship_rows_carry_their_step():
    payload = build_cockpit(
        boards=[BOARD],
        tickets_by_board={"b1": []},
        threads=[
            {"thread_id": "t1", "agent_id": "devops", "pending_prepare_poll": {"project_key": "VFA", "phase": "wait_merge"}},
        ],
        projects=[{"key": "VFA", "name": "VC Field Assistant"}],
        releases_by_project={"VFA": [{"name": "0.2.4", "is_default": True}]},
        now=datetime(2026, 11, 11, tzinfo=timezone.utc),
    )
    assert payload["ship_steps"] == ["Review", "Build", "Confirm", "Deploy", "Done"]
    assert payload["ship"][0]["step"] == 1


def test_money_summary_reports_each_source_and_caches(monkeypatch):
    from bigas import cockpit_money

    cockpit_money.reset_cache_for_tests()
    monkeypatch.setenv("ACCOUNTED_API_KEY", "k")
    monkeypatch.setenv("ACCOUNTED_CFO_COMPANIES", "Green Promo Wear:uuid-1\nScaleup Advisor:uuid-2")
    monkeypatch.setenv("BIGAS_USAGE_PROVIDERS", "cursor")
    calls = {"books": 0}

    def fake_books(company_id, *, api_key, today):
        calls["books"] += 1
        if company_id == "uuid-2":
            raise RuntimeError("401 from Accounted")
        return {
            "month_start": today.replace(day=1),
            "current": {"nettoomsattning": 50000, "net_result": 18400.4, "total_expenses": 31600},
            "previous": {"total_expenses": 28000, "net_result": 9000},
            "cash_now": 120000,
            "unbooked": 3,
            "inbox": 1,
        }

    monkeypatch.setattr("bigas.resources.cto.usage.accounted_books.fetch_company_books", fake_books)
    monkeypatch.setattr(
        "bigas.resources.cto.usage.service.fetch_ai_usage",
        lambda days=7: {"totals": {"est_cost_usd": 138.0, "by_provider": {"cursor": 100.0, "llm_logs": 38.0}}},
    )
    monkeypatch.setattr(
        "bigas.resources.cto.usage.service.enrich_with_prior_period",
        lambda report: {**report, "prior_period": {"totals": {"est_cost_usd": 100.0}}},
    )
    out = cockpit_money.money_summary()
    gpw, scaleup = out["books"]["companies"]
    assert gpw["label"] == "Green Promo Wear" and gpw["result"] == 18400.4 and gpw["unbooked"] == 3
    assert "401" in scaleup["error"]
    assert out["ai"]["week"] == 138.0 and out["ai"]["change_pct"] == 38
    assert out["ai"]["top"][0] == {"name": "cursor", "amount": 100.0}

    cockpit_money.money_summary()
    assert calls["books"] == 2  # cached: no second fetch
    cockpit_money.money_summary(refresh=True)
    assert calls["books"] == 4
    cockpit_money.reset_cache_for_tests()


def test_money_summary_bad_shapes_do_not_fail_the_endpoint(monkeypatch):
    from bigas import cockpit_money

    cockpit_money.reset_cache_for_tests()
    monkeypatch.setenv("ACCOUNTED_API_KEY", "k")
    monkeypatch.setenv("ACCOUNTED_CFO_COMPANIES", "Empty Co:uuid-1\nString Month:uuid-2")
    monkeypatch.setenv("BIGAS_USAGE_PROVIDERS", "cursor")

    def fake_books(company_id, *, api_key, today):
        if company_id == "uuid-1":
            return None
        return {
            "month_start": "2026-11-01",
            "current": {"nettoomsattning": 10, "net_result": 2, "total_expenses": 8},
            "previous": {"total_expenses": 4, "net_result": 1},
            "cash_now": 3,
            "unbooked": 0,
            "inbox": 1,
        }

    monkeypatch.setattr("bigas.resources.cto.usage.accounted_books.fetch_company_books", fake_books)
    monkeypatch.setattr("bigas.resources.cto.usage.service.fetch_ai_usage", lambda days=7: {})
    monkeypatch.setattr(
        "bigas.resources.cto.usage.service.enrich_with_prior_period",
        lambda report: {
            "totals": {
                "est_cost_usd": 5,
                "by_provider": {"cursor": None, "llm_logs": "nope", "tavily": 3},
            },
            "errors": ["cursor blew up", {"provider": "llm_logs"}],
        },
    )
    out = cockpit_money.money_summary()
    empty, string_month = out["books"]["companies"]
    assert "empty" in empty["error"]
    assert string_month["month"] == "2026-11" and string_month["result"] == 2.0
    assert out["ai"]["week"] == 5.0
    assert out["ai"]["top"][0] == {"name": "tavily", "amount": 3.0}
    assert out["ai"]["errors"] == ["cursor blew up", "llm_logs"]
    cockpit_money.reset_cache_for_tests()


def test_money_summary_without_config(monkeypatch):
    from bigas import cockpit_money

    cockpit_money.reset_cache_for_tests()
    for name in ("ACCOUNTED_API_KEY", "ACCOUNTED_CFO_COMPANIES", "BIGAS_USAGE_PROVIDERS"):
        monkeypatch.delenv(name, raising=False)
    out = cockpit_money.money_summary()
    assert out["books"] == {"configured": False, "companies": []}
    assert out["ai"] == {"configured": False}
    cockpit_money.reset_cache_for_tests()
