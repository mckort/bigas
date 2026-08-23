"""Tests for ticket attachments used by board AI."""

from __future__ import annotations

import io
import json
import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from flask import Flask

from bigas.resources.tickets.endpoints import tickets_bp
from bigas.tickets import store as ticket_store_module
from bigas.tickets.attachments import (
    extract_attachment_text,
    format_ticket_attachments,
    reset_attachment_blob_store_for_tests,
    set_image_describer,
)
from bigas.tickets.jira_adapter import TicketJiraAdapter
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
    reset_attachment_blob_store_for_tests()
    set_image_describer(None)
    yield
    ticket_store_module._store = None
    reset_attachment_blob_store_for_tests()
    set_image_describer(None)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(tickets_bp)
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-dev-token"}


def _create_ticket(client):
    boards = client.get("/api/boards", headers={**_auth_headers(), "Content-Type": "application/json"}).get_json()[
        "boards"
    ]
    personal = next((b for b in boards if not b.get("project_key")), boards[0])
    created = client.post(
        f"/api/boards/{personal['board_id']}/tickets",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        data=json.dumps({"title": "Fix checkout", "description": "## Brief\nBroken on mobile"}),
    ).get_json()["ticket"]
    return personal, created


def test_extract_text_file_and_screenshot():
    notes = extract_attachment_text(
        data=b"Use the green Save button",
        filename="notes.txt",
        content_type="text/plain",
    )
    assert "green Save button" in notes

    set_image_describer(lambda data, mime, name: f"Error toast: Payment failed ({name})")
    seen = extract_attachment_text(
        data=b"\x89PNG",
        filename="checkout.png",
        content_type="image/png",
    )
    assert "Payment failed" in seen
    assert "checkout.png" in seen


def test_format_ticket_attachments_for_prompts():
    text = format_ticket_attachments(
        [
            {
                "filename": "bug.png",
                "content_type": "image/png",
                "extracted_text": "Red banner: Card declined",
            }
        ]
    )
    assert "bug.png" in text
    assert "Card declined" in text
    assert format_ticket_attachments([]) == "(none)"


def test_upload_screenshot_and_text_attachment_api(client):
    set_image_describer(lambda data, mime, name: "Modal: Confirm delete is cut off on iPhone.")
    _board, ticket = _create_ticket(client)
    ticket_id = ticket["ticket_id"]

    png = client.post(
        f"/api/tickets/{ticket_id}/attachments",
        headers=_auth_headers(),
        data={"file": (io.BytesIO(b"\x89PNG fake"), "iphone-cutoff.png")},
        content_type="multipart/form-data",
    )
    assert png.status_code == 201
    attachment = png.get_json()["attachment"]
    assert attachment["filename"] == "iphone-cutoff.png"
    assert "Confirm delete" in attachment["extracted_text"]
    assert attachment["content_type"] == "image/png"

    notes = client.post(
        f"/api/tickets/{ticket_id}/attachments",
        headers=_auth_headers(),
        data={"file": (io.BytesIO(b"Keep the current card layout"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert notes.status_code == 201

    listed = client.get(
        f"/api/boards/{_board['board_id']}/tickets",
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )
    card = next(t for t in listed.get_json()["tickets"] if t["ticket_id"] == ticket_id)
    assert "attachments" not in card
    assert card["attachment_count"] == 2

    detail = client.get(
        f"/api/tickets/{ticket_id}",
        headers={**_auth_headers(), "Content-Type": "application/json"},
    ).get_json()["ticket"]
    assert len(detail["attachments"]) == 2
    assert any("Confirm delete" in (a.get("extracted_text") or "") for a in detail["attachments"])

    downloaded = client.get(
        f"/api/tickets/{ticket_id}/attachments/{attachment['id']}",
        headers=_auth_headers(),
    )
    assert downloaded.status_code == 200
    assert downloaded.data.startswith(b"\x89PNG")

    deleted = client.delete(
        f"/api/tickets/{ticket_id}/attachments/{attachment['id']}",
        headers=_auth_headers(),
    )
    assert deleted.status_code == 200
    after = client.get(
        f"/api/tickets/{ticket_id}",
        headers={**_auth_headers(), "Content-Type": "application/json"},
    ).get_json()["ticket"]
    assert len(after["attachments"]) == 1


def test_reject_unsupported_attachment(client):
    _board, ticket = _create_ticket(client)
    resp = client.post(
        f"/api/tickets/{ticket['ticket_id']}/attachments",
        headers=_auth_headers(),
        data={"file": (io.BytesIO(b"MZ"), "payload.exe")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "Unsupported" in (resp.get_json() or {}).get("error", "")


def test_attachments_reach_ai_adapter():
    store = get_ticket_store()
    boards = store.ensure_default_boards("test-user")
    ticket = store.create_ticket(boards[0]["board_id"], title="Screenshot bug", user_id="test-user")
    store.add_attachment(
        ticket["ticket_id"],
        {
            "id": "att-1",
            "filename": "mobile.png",
            "content_type": "image/png",
            "extracted_text": "Primary button overflows the viewport",
            "storage_path": "ticket_attachments/x/att-1/mobile.png",
            "size_bytes": 12,
        },
    )
    items = TicketJiraAdapter().list_attachments(ticket["key"])
    text = format_ticket_attachments(items)
    assert "Primary button overflows" in text
    assert "mobile.png" in text
