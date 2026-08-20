"""Tests for email ingest (BIG-9)."""
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
os.environ.setdefault("BIGAS_EMAIL_IMAP_SERVER", "imap.test.local")
os.environ.setdefault("BIGAS_EMAIL_USERNAME", "cos@bigas.me")
os.environ.setdefault("BIGAS_EMAIL_PASSWORD", "test-password")

from app import create_app
from bigas.providers.email.base import InboundEmail
from bigas.providers.email.imap import truncate_body


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-dev-token", "Content-Type": "application/json"}


def _access_headers():
    return {"Content-Type": "application/json", "X-Bigas-Access-Key": "scheduler-key"}


class TestTruncateBody:
    def test_no_truncation_when_short(self):
        assert truncate_body("hello", max_chars=100) == "hello"

    def test_truncates_long_body(self):
        text = "x" * 200
        result = truncate_body(text, max_chars=50)
        assert len(result) <= 50
        assert "truncated" in result

    def test_truncates_with_small_limit(self):
        text = "x" * 100
        result = truncate_body(text, max_chars=10)
        assert "truncated" in result
        assert "x" not in result


class TestEmailSyncEndpoint:
    def test_sync_requires_imap_and_posts_to_chief_thread(self, client, monkeypatch):
        from bigas.chat.db import get_chat_store

        store = get_chat_store()
        store.upsert_user("dev-user", "dev@bigas.local")

        sample = InboundEmail(
            message_id="<test@example.com>",
            uid="1",
            sender="vendor@example.com",
            subject="Invoice overdue",
            body_text="Please pay invoice #123.",
        )

        class FakeProvider:
            name = "imap"
            marked_uids = []

            def fetch_unread(self):
                return [sample]

            def mark_processed(self, uid):
                self.marked_uids.append(uid)

        fake = FakeProvider()
        monkeypatch.setattr(
            "bigas.resources.email.endpoints._email_provider",
            lambda: fake,
        )
        monkeypatch.setattr(
            "bigas.resources.email.endpoints.analyze_email",
            lambda msg: {
                "is_spam": False,
                "summary": "**From:** vendor@example.com\n\nInvoice needs attention.",
                "proposals": [
                    {
                        "id": "delegate_product",
                        "label": "Ask Product Manager",
                        "kind": "delegate",
                        "params": {"agent_id": "product", "task": "Track invoice follow-up"},
                    }
                ],
            },
        )

        app = client.application
        app.config["BIGAS_ACCESS_MODE"] = "restricted"
        app.config["BIGAS_ACCESS_KEYS"] = {"scheduler-key"}

        resp = client.post("/api/v1/providers/email/sync", headers=_access_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["synced"] == 1
        assert data["fetched"] == 1
        thread_id = data["thread_id"]

        messages = store.list_messages(thread_id)
        assert len(messages) == 1
        msg = messages[0]
        assert msg["role"] == "assistant"
        assert "Invoice" in msg["content"]
        meta = msg["metadata"]
        assert meta["type"] == "action_proposal"
        assert meta["status"] == "pending"
        assert len(meta["actions"]) == 1
        assert fake.marked_uids == ["1"]

    def test_sync_skips_spam(self, client, monkeypatch):
        from bigas.chat.db import get_chat_store

        store = get_chat_store()
        store.upsert_user("dev-user", "dev@bigas.local")

        sample = InboundEmail(
            message_id="<spam@example.com>",
            uid="2",
            sender="spam@bad.com",
            subject="WIN NOW",
            body_text="Click here",
        )

        class FakeProvider:
            marked_uids = []

            def fetch_unread(self):
                return [sample]

            def mark_processed(self, uid):
                self.marked_uids.append(uid)

        fake = FakeProvider()
        monkeypatch.setattr("bigas.resources.email.endpoints._email_provider", lambda: fake)
        monkeypatch.setattr(
            "bigas.resources.email.endpoints.analyze_email",
            lambda msg: {"is_spam": True, "summary": "", "proposals": []},
        )

        resp = client.post("/api/v1/providers/email/sync", headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["synced"] == 0
        assert data["skipped_spam"] == 1
        assert fake.marked_uids == ["2"]

    def test_sync_not_configured(self, client, monkeypatch):
        monkeypatch.setattr("bigas.resources.email.endpoints._email_provider", lambda: None)
        resp = client.post("/api/v1/providers/email/sync", headers={"Content-Type": "application/json"})
        assert resp.status_code == 503


class TestProposalApproveReject:
    def _seed_proposal(self, client):
        from bigas.chat.db import get_chat_store

        store = get_chat_store()
        store.upsert_user("dev-user", "dev@bigas.local")
        thread = store.create_thread("dev-user", "chief")
        message = store.add_message(
            thread["thread_id"],
            role="assistant",
            content="Email summary with proposal",
            metadata={
                "type": "action_proposal",
                "proposal_id": "prop-123",
                "status": "pending",
                "agent_id": "chief",
                "actions": [
                    {
                        "id": "draft1",
                        "label": "Save draft reply",
                        "kind": "draft_reply",
                        "params": {"text": "Thanks, we will review."},
                    }
                ],
            },
        )
        return thread, message

    def test_approve_draft_reply(self, client):
        thread, message = self._seed_proposal(client)
        meta = message["metadata"]

        resp = client.post(
            f"/api/v1/chat/proposals/{meta['proposal_id']}/approve",
            headers=_auth_headers(),
            data=json.dumps({"message_id": message["message_id"], "action_id": "draft1"}),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "approved"

        from bigas.chat.db import get_chat_store

        store = get_chat_store()
        updated = store.get_message(message["message_id"])
        assert updated["metadata"]["status"] == "approved"

        msgs = store.list_messages(thread["thread_id"])
        assert any("Draft reply" in m.get("content", "") for m in msgs)

    def test_approve_rejects_duplicate(self, client):
        thread, message = self._seed_proposal(client)
        meta = message["metadata"]
        payload = json.dumps({"message_id": message["message_id"], "action_id": "draft1"})

        first = client.post(
            f"/api/v1/chat/proposals/{meta['proposal_id']}/approve",
            headers=_auth_headers(),
            data=payload,
        )
        assert first.status_code == 200

        second = client.post(
            f"/api/v1/chat/proposals/{meta['proposal_id']}/approve",
            headers=_auth_headers(),
            data=payload,
        )
        assert second.status_code == 409

    def test_reject_proposal(self, client):
        thread, message = self._seed_proposal(client)
        meta = message["metadata"]

        resp = client.post(
            f"/api/v1/chat/proposals/{meta['proposal_id']}/reject",
            headers=_auth_headers(),
            data=json.dumps({"message_id": message["message_id"]}),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "rejected"

        from bigas.chat.db import get_chat_store

        store = get_chat_store()
        updated = store.get_message(message["message_id"])
        assert updated["metadata"]["status"] == "rejected"

    def test_approve_requires_auth(self, client):
        _, message = self._seed_proposal(client)
        resp = client.post(
            "/api/v1/chat/proposals/prop-123/approve",
            data=json.dumps({"message_id": message["message_id"], "action_id": "draft1"}),
        )
        assert resp.status_code == 401


class TestImapProviderConfigured:
    def test_is_configured_with_env(self):
        from bigas.providers.email.imap import ImapEmailProvider

        assert ImapEmailProvider.is_configured() is True
