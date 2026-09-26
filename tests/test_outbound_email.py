"""Tests for board outbound email (BIG-111)."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")
os.environ["ENABLE_OUTBOUND_EMAIL"] = "true"

from app import create_app
from bigas.providers.email.templates import render_personalized_template, validate_email_address
from bigas.resources.email.outbound_store import parse_recipient_csv


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-dev-token", "Content-Type": "application/json"}


def _setup_board():
    from bigas.chat.db import get_chat_store
    from bigas.tickets.store import get_ticket_store

    store = get_chat_store()
    store.upsert_user("dev-user", "dev@bigas.local")
    board = get_ticket_store().create_board("dev-user", name="Outreach board")
    return board["board_id"]


class TestTemplateAndCsv:
    def test_template_substitution(self):
        body = "Hi {{first_name}}, welcome {first_name}!"
        out = render_personalized_template(body, {"first_name": "Jane"})
        assert out == "Hi Jane, welcome Jane!"

    def test_validate_email(self):
        assert validate_email_address("a@b.co")
        assert not validate_email_address("bad")

    def test_csv_parser_valid_and_invalid(self):
        csv_text = "first_name,email\nJane,jane@example.com\n,bad@example.com\nBob,not-an-email\n"
        valid, invalid = parse_recipient_csv(csv_text)
        assert len(valid) == 1
        assert valid[0]["first_name"] == "Jane"
        assert len(invalid) == 2


class TestOutboundApi:
    def test_settings_test_and_campaign_send(self, client, monkeypatch):
        board_id = _setup_board()
        headers = _auth_headers()

        save = client.put(
            f"/api/boards/{board_id}/email-settings",
            headers=headers,
            json={
                "smtp_host": "smtp.test.local",
                "smtp_port": 587,
                "security": "starttls",
                "username": "user@test.local",
                "password": "secret",
                "sender_email": "user@test.local",
                "sender_name": "Tester",
            },
        )
        assert save.status_code == 200

        mock_provider = MagicMock()
        mock_provider.test_connection.return_value = None
        mock_provider.send_email.return_value = "<msg@test>"

        with patch(
            "bigas.resources.email.outbound_endpoints.config_for_provider",
            return_value=mock_provider,
        ):
            test_resp = client.post(
                f"/api/boards/{board_id}/email-settings/test",
                headers=headers,
            )
            assert test_resp.status_code == 200
            assert test_resp.get_json()["ok"] is True

        upload = client.post(
            f"/api/boards/{board_id}/recipients?replace=true",
            headers=headers,
            json={
                "csv": "first_name,email\nAlex,alex@example.com\nSam,sam@example.com\n",
            },
        )
        assert upload.status_code == 200
        assert upload.get_json()["added"] == 2

        with patch(
            "bigas.resources.email.outbound_service.config_for_provider",
            return_value=mock_provider,
        ):
            send = client.post(
                f"/api/boards/{board_id}/campaigns/send",
                headers=headers,
                json={
                    "subject": "Hello {{first_name}}",
                    "body": "Hi {{first_name}},\n\nThanks!",
                    "select_all": True,
                },
            )
            assert send.status_code == 202
            campaign_id = send.get_json()["campaign"]["campaign_id"]

            import time

            for _ in range(30):
                status = client.get(
                    f"/api/boards/{board_id}/campaigns/{campaign_id}",
                    headers=headers,
                )
                data = status.get_json()["campaign"]
                if data["status"] not in ("pending", "in_progress"):
                    break
                time.sleep(0.05)

            assert mock_provider.send_email.call_count == 2
            args = mock_provider.send_email.call_args_list[0].kwargs
            assert args["subject"] == "Hello Alex"
            assert "Hi Alex" in args["body"]

    def test_mcp_draft_tool_saves_draft(self, client, monkeypatch):
        board_id = _setup_board()
        monkeypatch.setattr(
            "bigas.resources.email.outbound_endpoints.generate_email_draft",
            lambda **_: {"subject": "Hey {{first_name}}", "body": "Hi {{first_name}},"},
        )
        resp = client.post(
            "/mcp/tools/draft_marketing_email",
            json={"board_id": board_id, "prompt": "Intro email", "user_id": "dev-user"},
        )
        assert resp.status_code == 200
        draft_resp = client.get(
            f"/api/boards/{board_id}/email-draft",
            headers=_auth_headers(),
        )
        draft = draft_resp.get_json()["draft"]
        assert "{{first_name}}" in draft["body"]
