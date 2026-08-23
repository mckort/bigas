"""Tests for internal Kanban boards and tickets (BIG-19)."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from app import create_app
from bigas.resources.product.endpoints import get_manifest
from bigas.tickets import store as ticket_store_module
from bigas.tickets.config import jira_configured, use_internal_board
from bigas.tickets.constants import columns_for_board
from bigas.resources.product.jira_automation.comments import format_human_comments
from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER
from bigas.tickets.jira_adapter import TicketJiraAdapter
from bigas.tickets.service import comment_author_name, dispatch_ticket_status_automation
from bigas.tickets.store import get_ticket_store

_JIRA_ENV_KEYS = (
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_PROJECT_KEY",
    "JIRA_PROJECT_KEYS",
    "USE_INTERNAL_BOARD",
)


@pytest.fixture(autouse=True)
def _force_internal_board(monkeypatch):
    for key in _JIRA_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    ticket_store_module._store = None
    yield
    ticket_store_module._store = None


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-dev-token", "Content-Type": "application/json"}


def test_board_spa_and_api_bypass_access_key(client):
    client.application.config["BIGAS_ACCESS_MODE"] = "restricted"
    client.application.config["BIGAS_ACCESS_KEYS"] = {"secret-key"}
    client.application.config["BIGAS_ACCESS_HEADER"] = "X-Bigas-Access-Key"

    page = client.get("/board")
    assert page.status_code != 401
    assert page.get_json() is None or "access key" not in str(page.get_json()).lower()

    unauth_api = client.get("/api/boards")
    assert unauth_api.status_code == 401
    assert "access key" not in (unauth_api.get_json() or {}).get("detail", "").lower()

    allowed = client.get("/api/boards", headers=_auth_headers())
    assert allowed.status_code == 200


def test_internal_board_default_without_jira():
    assert not jira_configured()
    assert use_internal_board()


def test_columns_for_personal_vs_project():
    personal = columns_for_board(project_key=None)
    project = columns_for_board(project_key="VFA")
    assert "To Do" in personal
    assert "Research and describe (AI)" in project
    assert len(project) > len(personal)


def test_boards_and_tickets_api(client):
    resp = client.get("/api/boards", headers=_auth_headers())
    assert resp.status_code == 200
    boards = resp.get_json()["boards"]
    assert len(boards) >= 1

    personal = next((b for b in boards if not b.get("project_key")), boards[0])
    board_id = personal["board_id"]

    create_resp = client.post(
        f"/api/boards/{board_id}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Fix login bug", "description": "Users cannot log in"}),
    )
    assert create_resp.status_code == 201
    ticket = create_resp.get_json()["ticket"]
    assert ticket["key"].startswith("PERS-")
    assert ticket["title"] == "Fix login bug"

    list_resp = client.get(f"/api/boards/{board_id}/tickets", headers=_auth_headers())
    assert list_resp.status_code == 200
    assert any(t["ticket_id"] == ticket["ticket_id"] for t in list_resp.get_json()["tickets"])

    update_resp = client.put(
        f"/api/tickets/{ticket['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"status": "In Progress"}),
    )
    assert update_resp.status_code == 200
    assert update_resp.get_json()["ticket"]["status"] == "In Progress"


def test_create_jira_issue_uses_internal_board(client):
    resp = client.post(
        "/mcp/tools/create_jira_issue",
        data=json.dumps(
            {
                "project_key": "BIG",
                "summary": "Add native board",
                "description": "Build Kanban into Bigas UI",
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["source"] == "internal_board"
    assert data["key"].startswith("BIG-")


def test_lookup_internal_ticket(client):
    create = client.post(
        "/mcp/tools/create_jira_issue",
        data=json.dumps(
            {
                "project_key": "VFA",
                "summary": "Test ticket",
                "description": "Lookup test",
            }
        ),
        content_type="application/json",
    )
    key = create.get_json()["key"]
    lookup = client.post(
        "/mcp/tools/lookup_jira",
        data=json.dumps({"issue_key": key}),
        content_type="application/json",
    )
    assert lookup.status_code == 200
    body = lookup.get_json()
    assert body["ok"] is True
    assert body["issue"]["key"] == key


def test_create_and_delete_board(client):
    resp = client.post(
        "/api/boards",
        headers=_auth_headers(),
        data=json.dumps({"name": "Temp board", "project_key": "REM"}),
    )
    assert resp.status_code == 201
    board_id = resp.get_json()["board"]["board_id"]

    del_resp = client.delete(f"/api/boards/{board_id}", headers=_auth_headers())
    assert del_resp.status_code == 200


def test_ticket_comments_api(client):
    boards = client.get("/api/boards", headers=_auth_headers()).get_json()["boards"]
    personal = next((b for b in boards if not b.get("project_key")), boards[0])
    created = client.post(
        f"/api/boards/{personal['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Need feedback", "description": "Brief"}),
    ).get_json()["ticket"]
    ticket_id = created["ticket_id"]

    empty = client.post(
        f"/api/tickets/{ticket_id}/comments",
        headers=_auth_headers(),
        data=json.dumps({"body": "   "}),
    )
    assert empty.status_code == 400

    posted = client.post(
        f"/api/tickets/{ticket_id}/comments",
        headers=_auth_headers(),
        data=json.dumps({"body": "Skip Redis, use Firestore"}),
    )
    assert posted.status_code == 201
    comment = posted.get_json()["comment"]
    assert comment["body"] == "Skip Redis, use Firestore"
    assert comment["author_name"] == "dev"
    assert comment["author_id"] == "dev-user"

    listed = client.get(f"/api/boards/{personal['board_id']}/tickets", headers=_auth_headers())
    card = next(t for t in listed.get_json()["tickets"] if t["ticket_id"] == ticket_id)
    assert "comments" not in card
    assert card["comment_count"] == 1

    detail = client.get(f"/api/tickets/{ticket_id}", headers=_auth_headers())
    comments = detail.get_json()["ticket"]["comments"]
    assert len(comments) == 1
    assert comments[0]["body"] == "Skip Redis, use Firestore"


def test_human_comments_reach_ai_adapter():
    store = get_ticket_store()
    boards = store.ensure_default_boards("test-user")
    board = boards[0]
    ticket = store.create_ticket(board["board_id"], title="Plan this", user_id="test-user")
    store.add_comment(
        ticket["ticket_id"],
        "Keep the mobile layout as-is",
        author_name="Marcus",
        author_id="u1",
    )
    store.add_comment(
        ticket["ticket_id"],
        f"{BIGAS_COMMENT_MARKER} Research complete.",
        author_name="Bigas",
    )

    raw = TicketJiraAdapter().list_comments(ticket["key"])
    text = format_human_comments(raw)
    assert "Keep the mobile layout as-is" in text
    assert "Marcus" in text
    assert BIGAS_COMMENT_MARKER not in text


def test_comment_author_name_from_email():
    assert comment_author_name({"email": "marcus@bigas.me", "uid": "abc"}) == "marcus"
    assert comment_author_name({"uid": "abc"}) == "abc"


def test_ticket_persistence_in_memory_store():
    store = get_ticket_store()
    boards = store.ensure_default_boards("test-user")
    board = boards[0]
    ticket = store.create_ticket(board["board_id"], title="Persist me", user_id="test-user")
    fetched = store.get_ticket_by_key(ticket["key"])
    assert fetched is not None
    assert fetched["title"] == "Persist me"


def test_automation_worker_requires_chat_auth(client):
    resp = client.post(
        "/api/tickets/automation-worker",
        data=json.dumps({"ticket_id": "missing", "new_status": "Research and describe (AI)"}),
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_automation_worker_runs_with_chat_auth(client, monkeypatch):
    boards = client.get("/api/boards", headers=_auth_headers()).get_json()["boards"]
    vfa = next(b for b in boards if b.get("project_key") == "VFA")
    created = client.post(
        f"/api/boards/{vfa['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Research this", "description": "Brief"}),
    ).get_json()["ticket"]

    called = {}

    def fake_run(ticket, **kwargs):
        called["ticket"] = ticket
        called.update(kwargs)

    monkeypatch.setattr(
        "bigas.resources.tickets.endpoints.run_ticket_status_automation",
        fake_run,
    )
    resp = client.post(
        "/api/tickets/automation-worker",
        headers=_auth_headers(),
        data=json.dumps(
            {
                "ticket_id": created["ticket_id"],
                "new_status": "Research and describe (AI)",
                "old_status": "To Do",
                "project_key": "VFA",
            }
        ),
    )
    assert resp.status_code == 200
    assert called["new_status"] == "Research and describe (AI)"
    assert called["ticket"]["ticket_id"] == created["ticket_id"]


def test_dispatch_uses_loopback_and_chat_auth(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return MagicMock(status_code=200)

    monkeypatch.setattr("bigas.tickets.service.requests.post", fake_post)
    monkeypatch.setenv("PORT", "8080")

    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = False
    with app.test_request_context("/", headers={"Authorization": "Bearer test-dev-token"}):
        dispatch_ticket_status_automation(
            {"ticket_id": "t1", "key": "VFA-1"},
            old_status="To Do",
            new_status="Research and describe (AI)",
            project_key="VFA",
        )

    assert captured["url"] == "http://127.0.0.1:8080/api/tickets/automation-worker"
    assert captured["headers"]["Authorization"] == "Bearer test-dev-token"
    assert "X-Bigas-Webhook-Secret" not in captured["headers"]
    assert "X-Bigas-Access-Key" not in captured["headers"]


def test_jira_webhook_disabled_without_jira(client):
    resp = client.post(
        "/mcp/tools/jira_status_automation",
        data=json.dumps(
            {
                "issue_key": "VFA-1",
                "to_status": "Research and describe (AI)",
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Jira webhook disabled"

    names = [t["name"] for t in get_manifest()["tools"]]
    assert "jira_status_automation" not in names


def test_jira_webhook_requires_secret_when_configured(client, monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "dev@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "VFA")
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")

    denied = client.post(
        "/mcp/tools/jira_status_automation",
        data=json.dumps(
            {
                "issue_key": "VFA-1",
                "to_status": "Research and describe (AI)",
            }
        ),
        content_type="application/json",
    )
    assert denied.status_code == 401

    monkeypatch.setattr(
        "bigas.resources.product.endpoints.JiraAutomationService.handle_event",
        lambda self, **kwargs: {"ok": True, "handler": "research_describe"},
    )
    ok = client.post(
        "/mcp/tools/jira_status_automation",
        headers={"X-Bigas-Webhook-Secret": "abc", "Content-Type": "application/json"},
        data=json.dumps(
            {
                "issue_key": "VFA-1",
                "to_status": "Research and describe (AI)",
                "sync": True,
            }
        ),
    )
    assert ok.status_code == 200
    assert ok.get_json()["ok"] is True

    names = [t["name"] for t in get_manifest()["tools"]]
    assert "jira_status_automation" in names
