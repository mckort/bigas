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
from bigas.tickets.jira_import import sync_jira_board
from bigas.tickets.labels import normalize_label, normalize_labels
from bigas.tickets.service import comment_author_name, dispatch_ticket_status_automation
from bigas.tickets.attachments import reset_attachment_blob_store_for_tests, set_image_describer
from bigas.tickets.release_store import reset_release_store_for_tests
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
    reset_release_store_for_tests()
    reset_attachment_blob_store_for_tests()
    set_image_describer(None)
    yield
    ticket_store_module._store = None
    reset_release_store_for_tests()
    reset_attachment_blob_store_for_tests()
    set_image_describer(None)


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-dev-token", "Content-Type": "application/json"}


def test_board_spa_and_api_bypass_access_key(client, monkeypatch):
    monkeypatch.setitem(client.application.config, "BIGAS_ACCESS_MODE", "restricted")
    monkeypatch.setitem(client.application.config, "BIGAS_ACCESS_KEYS", {"secret-key"})
    monkeypatch.setitem(client.application.config, "BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")

    page = client.get("/board")
    assert page.status_code != 401
    assert page.get_json() is None or "access key" not in str(page.get_json()).lower()

    login = client.get("/login")
    assert login.status_code != 401

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert b"Disallow: /login" in robots.data

    unauth_api = client.get("/api/boards")
    assert unauth_api.status_code == 401
    body = unauth_api.get_json()
    detail = body.get("detail", "") if isinstance(body, dict) else str(body or "")
    assert "access key" not in detail.lower()

    allowed = client.get("/api/boards", headers=_auth_headers())
    assert allowed.status_code == 200

    unauth_releases = client.get("/api/projects/VFA/releases")
    assert unauth_releases.status_code == 401
    releases_body = unauth_releases.get_json()
    releases_detail = (
        releases_body.get("detail", "") if isinstance(releases_body, dict) else str(releases_body or "")
    )
    assert "access key" not in releases_detail.lower()

    allowed_releases = client.get("/api/projects/VFA/releases", headers=_auth_headers())
    assert allowed_releases.status_code == 200


def test_internal_board_default_without_jira():
    assert not jira_configured()
    assert use_internal_board()


def test_internal_board_default_even_when_jira_is_configured(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "dev@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "VFA")
    assert jira_configured()
    assert use_internal_board()
    monkeypatch.setenv("USE_INTERNAL_BOARD", "false")
    assert not use_internal_board()


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


def test_drag_to_done_sets_done_at(client):
    resp = client.get("/api/boards", headers=_auth_headers())
    boards = resp.get_json()["boards"]
    personal = next((b for b in boards if not b.get("project_key")), boards[0])
    create_resp = client.post(
        f"/api/boards/{personal['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Ship ranking"}),
    )
    assert create_resp.status_code == 201
    ticket = create_resp.get_json()["ticket"]
    assert not ticket.get("done_at")

    moved = client.put(
        f"/api/tickets/{ticket['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"status": "Done"}),
    )
    assert moved.status_code == 200
    body = moved.get_json()["ticket"]
    assert body["status"] == "Done"
    assert body["done_at"]

    reopened = client.put(
        f"/api/tickets/{ticket['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"status": "To Do"}),
    )
    assert reopened.status_code == 200
    assert reopened.get_json()["ticket"]["status"] == "To Do"
    assert not reopened.get_json()["ticket"].get("done_at")


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
    assert resp.get_json()["reason"] == "Internal board is the ticket source"

    names = [t["name"] for t in get_manifest()["tools"]]
    assert "jira_status_automation" not in names


def test_jira_webhook_requires_secret_when_configured(client, monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "dev@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "VFA")
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("USE_INTERNAL_BOARD", "false")

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


def test_normalize_labels_like_jira():
    assert normalize_label("Customer Request") == "customer-request"
    assert normalize_label("Acme Corp") == "acme-corp"
    assert normalize_labels(["customer request", "Acme Corp", "acme-corp"]) == [
        "customer-request",
        "acme-corp",
    ]
    assert normalize_labels(["seo"], marketing=True) == ["seo", "marketing"]


def test_ticket_labels_api(client):
    boards = client.get("/api/boards", headers=_auth_headers()).get_json()
    assert boards["jira_import_available"] is False
    vfa = next(b for b in boards["boards"] if b.get("project_key") == "VFA")
    created = client.post(
        f"/api/boards/{vfa['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps(
            {
                "title": "Export CSV",
                "description": "Customer asked",
                "labels": ["Customer Request", "Green Promo Wear"],
            }
        ),
    ).get_json()["ticket"]
    assert created["labels"] == ["customer-request", "green-promo-wear"]
    assert created["marketing"] is False

    updated = client.put(
        f"/api/tickets/{created['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"labels": ["customer-request", "green-promo-wear", "marketing"]}),
    ).get_json()["ticket"]
    assert "marketing" in updated["labels"]
    assert updated["marketing"] is True

    issue = TicketJiraAdapter().get_issue(created["key"])
    assert "customer-request" in issue["fields"]["labels"]
    assert "marketing" in issue["fields"]["labels"]


def test_sync_jira_creates_and_merges_labels(client):
    boards = client.get("/api/boards", headers=_auth_headers()).get_json()["boards"]
    vfa = next(b for b in boards if b.get("project_key") == "VFA")
    store = get_ticket_store()
    reserved = store.create_ticket(
        vfa["board_id"],
        title="Existing Jira key",
        description="Local brief",
        labels=["internal-note"],
        user_id="dev-user",
        key="VFA-42",
    )
    assert reserved["key"] == "VFA-42"

    class FakeJira:
        def search_issues_for_projects(self, keys, fields=None):
            assert keys == ["VFA"]
            return [
                {
                    "key": "VFA-42",
                    "fields": {
                        "summary": "Customer asked for export",
                        "description": "Please add CSV export",
                        "status": {"name": "To Do"},
                        "issuetype": {"name": "Task"},
                        "labels": ["customer-request", "Acme Corp"],
                        "parent": None,
                        "assignee": {"displayName": "Marcus"},
                        "fixVersions": [],
                    },
                },
                {
                    "key": "VFA-99",
                    "fields": {
                        "summary": "New from Jira",
                        "description": "Brand new",
                        "status": {"name": "To Do"},
                        "issuetype": {"name": "Story"},
                        "labels": ["customer-request"],
                        "parent": {"key": "VFA-1"},
                        "assignee": None,
                        "fixVersions": [{"name": "v2.0"}],
                    },
                },
            ]

    result = sync_jira_board(user_id="dev-user", board_id=vfa["board_id"], jira=FakeJira())
    assert result["created"] == 1
    assert result["updated"] == 1
    merged = store.get_ticket_by_key("VFA-42")
    assert merged["description"] == "Local brief"
    assert set(merged["labels"]) == {"internal-note", "customer-request", "acme-corp"}
    created = store.get_ticket_by_key("VFA-99")
    assert created["title"] == "New from Jira"
    assert created["labels"] == ["customer-request"]
    assert created["issue_type"] == "Task"
    assert created["fix_version"] == "v2.0"


def test_sync_jira_requires_project_board(client):
    boards = client.get("/api/boards", headers=_auth_headers()).get_json()["boards"]
    personal = next(b for b in boards if not b.get("project_key"))
    denied = client.post(
        f"/api/boards/{personal['board_id']}/sync-jira",
        headers=_auth_headers(),
    )
    assert denied.status_code == 400
    assert "Jira" in denied.get_json()["error"] or "Personal" in denied.get_json()["error"]


def test_sync_jira_rejects_concurrent_sync(client):
    boards = client.get("/api/boards", headers=_auth_headers()).get_json()["boards"]
    vfa = next(b for b in boards if b.get("project_key") == "VFA")
    store = get_ticket_store()
    assert store.try_begin_jira_sync(vfa["board_id"], user_id="dev-user")
    denied = client.post(
        f"/api/boards/{vfa['board_id']}/sync-jira",
        headers=_auth_headers(),
    )
    assert denied.status_code == 409
    assert denied.get_json()["status"] == "running"
    store.finish_jira_sync(
        vfa["board_id"],
        user_id="dev-user",
        status="completed",
        result={"ok": True},
    )


def test_allocate_key_auto_increment_is_reserved(client):
    boards = client.get("/api/boards", headers=_auth_headers()).get_json()["boards"]
    personal = next(b for b in boards if not b.get("project_key"))
    store = get_ticket_store()
    board = store.get_board(personal["board_id"])
    first_key = store._allocate_key(board, None)
    with pytest.raises(ValueError, match="already exists"):
        store._allocate_key(board, first_key)
    ticket = store.create_ticket(
        personal["board_id"],
        title="Next auto key",
        user_id="dev-user",
    )
    assert ticket["key"] != first_key


def test_allocate_key_skips_occupied_auto_keys(client):
    boards = client.get("/api/boards", headers=_auth_headers()).get_json()["boards"]
    personal = next(b for b in boards if not b.get("project_key"))
    store = get_ticket_store()
    store._key_index["PERS-1"] = "reserved"
    ticket = store.create_ticket(
        personal["board_id"],
        title="Skip occupied auto key",
        user_id="dev-user",
    )
    assert ticket["key"] == "PERS-2"
