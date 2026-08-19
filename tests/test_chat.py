"""Tests for the Bigas chat web interface (BIG-6)."""
from __future__ import annotations

import json
import os

import pytest

# Force in-memory chat storage and dev auth before app import
os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-dev-token", "Content-Type": "application/json"}


def test_auth_config_public(client):
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["auth_mode"] == "dev"


def test_verify_auth(client):
    resp = client.post("/api/auth/verify", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_list_agents(client):
    resp = client.get("/api/agents", headers=_auth_headers())
    assert resp.status_code == 200
    agents = resp.get_json()["agents"]
    ids = {a["agent_id"] for a in agents}
    assert {"chief", "marketing", "product", "cto"}.issubset(ids)


def test_create_thread_and_messages(client, monkeypatch):
    from bigas.chat.db import get_chat_store

    def mock_handle(**kwargs):
        store = get_chat_store()
        store.add_message(kwargs["thread_id"], role="user", content=kwargs["user_message"])
        msg = store.add_message(
            kwargs["thread_id"], role="assistant", content="Hello from chief"
        )
        return {"status": "complete", "message": msg}

    monkeypatch.setattr("bigas.resources.chat.endpoints.handle_chat_message", mock_handle)

    thread_resp = client.post(
        "/api/chat/threads",
        headers=_auth_headers(),
        data=json.dumps({"agent_id": "chief"}),
    )
    assert thread_resp.status_code == 201
    thread_id = thread_resp.get_json()["thread"]["thread_id"]

    msg_resp = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers=_auth_headers(),
        data=json.dumps({"content": "Hi there"}),
    )
    assert msg_resp.status_code == 200
    assert msg_resp.get_json()["status"] == "complete"

    history = client.get(f"/api/chat/threads/{thread_id}/messages", headers=_auth_headers())
    assert history.status_code == 200
    messages = history.get_json()["messages"]
    assert any(m["role"] == "user" and m["content"] == "Hi there" for m in messages)


def test_update_agent_goals(client):
    resp = client.put(
        "/api/agents/marketing",
        headers=_auth_headers(),
        data=json.dumps(
            {
                "name": "Marketing Analyst",
                "system_prompt_goals": "Focus on GA4 and ads performance.",
            }
        ),
    )
    assert resp.status_code == 200
    assert "GA4" in resp.get_json()["agent"]["system_prompt_goals"]


def test_update_agent_requires_admin(client, monkeypatch):
    monkeypatch.setenv("CHAT_ADMIN_EMAILS", "admin@example.com")
    resp = client.put(
        "/api/agents/marketing",
        headers=_auth_headers(),
        data=json.dumps(
            {
                "name": "Marketing Analyst",
                "system_prompt_goals": "Should be rejected.",
            }
        ),
    )
    assert resp.status_code == 403


def test_invalid_api_route_returns_json_404(client):
    resp = client.get("/api/does-not-exist", headers=_auth_headers())
    assert resp.status_code == 404
    assert resp.is_json
    assert resp.get_json()["error"] == "Not found"


def test_invalid_mcp_route_returns_json_404(client):
    resp = client.get("/mcp/tools/invalid-endpoint")
    assert resp.status_code == 404
    assert resp.is_json
    assert resp.get_json()["error"] == "Not found"


def test_activity_feed(client):
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    store.add_activity(type_="test", content="PR review posted", source="cto")

    resp = client.get("/api/feed", headers=_auth_headers())
    assert resp.status_code == 200
    events = resp.get_json()["events"]
    assert any("PR review" in e["content"] for e in events)


def test_chat_callback(client):
    thread_resp = client.post(
        "/api/chat/threads",
        headers=_auth_headers(),
        data=json.dumps({"agent_id": "chief"}),
    )
    thread_id = thread_resp.get_json()["thread"]["thread_id"]

    os.environ["CHAT_CALLBACK_SECRET"] = "callback-secret"
    resp = client.post(
        "/api/chat/callback",
        headers={"X-Bigas-Chat-Callback": "callback-secret", "Content-Type": "application/json"},
        data=json.dumps(
            {
                "thread_id": thread_id,
                "content": "Async task complete",
                "agent_id": "marketing",
            }
        ),
    )
    assert resp.status_code == 200

    history = client.get(f"/api/chat/threads/{thread_id}/messages", headers=_auth_headers())
    messages = history.get_json()["messages"]
    assert any("Async task complete" in m["content"] for m in messages)


def test_humanize_tool_result_unwraps_answer_json():
    from bigas.agents.chief_of_staff import humanize_tool_result

    raw = json.dumps(
        {
            "answer": "Over the last 7 days, your website experienced very light traffic.\n\n* Busiest day: August 14th"
        }
    )
    out = humanize_tool_result(raw)
    assert out.startswith("Over the last 7 days")
    assert "Busiest day" in out
    assert not out.startswith("{")


def test_humanize_tool_result_unwraps_error_after_prefix():
    from bigas.agents.chief_of_staff import humanize_tool_result

    raw = 'Tool ask_analytics_question failed:\n{"error": "No GA4 data returned"}'
    assert humanize_tool_result(raw) == "No GA4 data returned"


def test_discord_mirror_activity():
    from bigas.chat.db import get_chat_store
    from bigas.chat.activity import mirror_discord_message

    store = get_chat_store()
    before = len(store.list_activity())
    mirror_discord_message("https://discord.example/cto", "Uptime alert: site down", channel_hint="cto")
    after = len(store.list_activity())
    assert after == before + 1
