"""Tests for internal Kanban boards and tickets (BIG-19)."""
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
from bigas.tickets import store as ticket_store_module
from bigas.tickets.config import jira_configured, use_internal_board
from bigas.tickets.constants import columns_for_board
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


def test_ticket_persistence_in_memory_store():
    store = get_ticket_store()
    boards = store.ensure_default_boards("test-user")
    board = boards[0]
    ticket = store.create_ticket(board["board_id"], title="Persist me", user_id="test-user")
    fetched = store.get_ticket_by_key(ticket["key"])
    assert fetched is not None
    assert fetched["title"] == "Persist me"
