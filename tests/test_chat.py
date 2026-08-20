"""Tests for the Bigas chat web interface (BIG-6)."""
from __future__ import annotations

import json
import os
import time

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
    assert {"chief", "marketing", "product", "cto", "devops"}.issubset(ids)


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


def test_send_message_stores_client_id(client, monkeypatch):
    from bigas.chat.db import get_chat_store

    def mock_handle(**kwargs):
        store = get_chat_store()
        metadata = {"client_id": kwargs["client_id"]} if kwargs.get("client_id") else None
        store.add_message(
            kwargs["thread_id"],
            role="user",
            content=kwargs["user_message"],
            metadata=metadata,
        )
        msg = store.add_message(
            kwargs["thread_id"], role="assistant", content="Hello from chief"
        )
        return {"status": "complete", "message": msg}

    monkeypatch.setattr("bigas.resources.chat.endpoints.handle_chat_message", mock_handle)

    thread_id = client.post(
        "/api/chat/threads",
        headers=_auth_headers(),
        data=json.dumps({"agent_id": "chief"}),
    ).get_json()["thread"]["thread_id"]

    client_id = "test-client-msg-id"
    msg_resp = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers=_auth_headers(),
        data=json.dumps({"content": "Hi there", "client_id": client_id}),
    )
    assert msg_resp.status_code == 200

    history = client.get(f"/api/chat/threads/{thread_id}/messages", headers=_auth_headers())
    user_msgs = [m for m in history.get_json()["messages"] if m["role"] == "user"]
    assert any(m.get("metadata", {}).get("client_id") == client_id for m in user_msgs)


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


def test_list_threads_resumes_conversation(client):
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    headers = _auth_headers()

    empty = client.post(
        "/api/chat/threads",
        headers=headers,
        data=json.dumps({"agent_id": "chief"}),
    ).get_json()["thread"]
    convo = client.post(
        "/api/chat/threads",
        headers=headers,
        data=json.dumps({"agent_id": "chief"}),
    ).get_json()["thread"]
    store.add_message(convo["thread_id"], role="user", content="Keep this chat")
    later_empty = client.post(
        "/api/chat/threads",
        headers=headers,
        data=json.dumps({"agent_id": "chief"}),
    ).get_json()["thread"]

    listed = client.get("/api/chat/threads", headers=headers)
    assert listed.status_code == 200
    ours = {empty["thread_id"], convo["thread_id"], later_empty["thread_id"]}
    chief = [t for t in listed.get_json()["threads"] if t["thread_id"] in ours]
    resumable = [t for t in chief if (t.get("message_count") or 0) > 0]
    assert [t["thread_id"] for t in resumable] == [convo["thread_id"]]
    assert empty["message_count"] == 0
    assert later_empty["message_count"] == 0


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


def test_get_or_create_agent_thread_reuses_existing():
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    first = store.get_or_create_agent_thread("thread-reuse-user", "product")
    second = store.get_or_create_agent_thread("thread-reuse-user", "product")
    assert first["thread_id"] == second["thread_id"]
    assert first["agent_id"] == "product"
    chief = store.get_or_create_chief_thread("thread-reuse-user")
    assert chief["agent_id"] == "chief"
    assert chief["thread_id"] != first["thread_id"]


def test_delegated_task_appears_in_specialist_thread(monkeypatch):
    from bigas.agents.chief_of_staff import run_specialist_task
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    store.upsert_user("delegate-user", "delegate@bigas.local")
    chief = store.create_thread("delegate-user", "chief")
    product = store.get_or_create_agent_thread("delegate-user", "product")

    class FakeClient:
        def list_tools(self):
            return []

    class FakeLlm:
        def complete(self, messages, temperature=0.4):
            return "Invoice follow-up tracked."

    monkeypatch.setattr("bigas.agents.chief_of_staff._mcp_client", lambda: FakeClient())
    monkeypatch.setattr(
        "bigas.agents.chief_of_staff._select_tool_via_llm",
        lambda *args, **kwargs: ("", None, None),
    )
    monkeypatch.setattr(
        "bigas.agents.chief_of_staff.get_llm_client",
        lambda feature="chat": (FakeLlm(), "gpt-test"),
    )

    result = run_specialist_task(
        "product",
        "Track invoice follow-up",
        thread_id=chief["thread_id"],
        async_mode=True,
    )
    assert "Delegated to product" in result

    deadline = time.time() + 2
    product_msgs = []
    while time.time() < deadline:
        product_msgs = store.list_messages(product["thread_id"])
        if any("Invoice follow-up tracked" in (m.get("content") or "") for m in product_msgs):
            break
        time.sleep(0.05)

    assert any(
        m.get("metadata", {}).get("type") == "handoff" and "Track invoice follow-up" in m["content"]
        for m in product_msgs
    )
    assert any("Delegated from" in (m.get("content") or "") for m in product_msgs)
    assert any("Invoice follow-up tracked" in (m.get("content") or "") for m in product_msgs)

    chief_msgs = store.list_messages(chief["thread_id"])
    assert any("working" in (m.get("content") or "").lower() for m in chief_msgs)
    assert any("Invoice follow-up tracked" in (m.get("content") or "") for m in chief_msgs)
    assert not any(m.get("metadata", {}).get("type") == "handoff" for m in chief_msgs)


def test_direct_specialist_chat_does_not_mirror_handoff(monkeypatch):
    from bigas.agents.chief_of_staff import run_specialist_task
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    product = store.create_thread("direct-product-user", "product")

    class FakeClient:
        def list_tools(self):
            return []

    class FakeLlm:
        def complete(self, messages, temperature=0.4):
            return "Direct product reply."

    monkeypatch.setattr("bigas.agents.chief_of_staff._mcp_client", lambda: FakeClient())
    monkeypatch.setattr(
        "bigas.agents.chief_of_staff._select_tool_via_llm",
        lambda *args, **kwargs: ("", None, None),
    )
    monkeypatch.setattr(
        "bigas.agents.chief_of_staff.get_llm_client",
        lambda feature="chat": (FakeLlm(), "gpt-test"),
    )

    run_specialist_task(
        "product",
        "What is on the board?",
        thread_id=product["thread_id"],
        async_mode=True,
    )

    deadline = time.time() + 2
    msgs = []
    while time.time() < deadline:
        msgs = store.list_messages(product["thread_id"])
        if any("Direct product reply" in (m.get("content") or "") for m in msgs):
            break
        time.sleep(0.05)

    assert not any(m.get("metadata", {}).get("type") == "handoff" for m in msgs)
    assert any("Direct product reply" in (m.get("content") or "") for m in msgs)


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


def test_humanize_tool_result_unwraps_indented_json():
    from bigas.agents.chief_of_staff import humanize_tool_result

    raw = 'Tool ask_analytics_question failed:\n  {\n    "answer": "Traffic is up."\n  }'
    assert humanize_tool_result(raw) == "Traffic is up."


def test_humanize_tool_result_unwraps_summary():
    from bigas.agents.chief_of_staff import humanize_tool_result

    raw = json.dumps(
        {
            "success": True,
            "launched": True,
            "agent_url": "https://cursor.com/agents/bc-123",
            "summary": "Autofix is running (round 2/5). Follow the agent: https://cursor.com/agents/bc-123",
        }
    )
    assert humanize_tool_result(raw).startswith("Autofix is running")


def test_discord_mirror_activity():
    from bigas.chat.db import get_chat_store
    from bigas.chat.activity import mirror_discord_message

    store = get_chat_store()
    before = len(store.list_activity())
    mirror_discord_message("https://discord.example/cto", "Uptime alert: site down", channel_hint="cto")
    after = len(store.list_activity())
    assert after == before + 1


def test_favicon_is_served_without_auth():
    from flask import Flask

    from bigas.resources.chat.endpoints import chat_bp

    app = Flask(__name__)
    app.register_blueprint(chat_bp)
    client = app.test_client()
    resp = client.get("/favicon.png")
    assert resp.status_code == 200
    assert "png" in (resp.content_type or "")
    ico = client.get("/favicon.ico")
    assert ico.status_code == 200
    png = client.get("/favicon-32x32.png")
    assert png.status_code == 200
    assert "png" in (png.content_type or "")
    logo = client.get("/bigas-logo.png")
    assert logo.status_code == 200
    assert "png" in (logo.content_type or "")


def test_logo_bypasses_restricted_access():
    from flask import Flask, jsonify, request

    from bigas.resources.chat.endpoints import BRAND_ICON_FILES, chat_bp

    app = Flask(__name__)
    app.register_blueprint(chat_bp)

    @app.before_request
    def _enforce_access_key():
        if request.path.lstrip("/") in BRAND_ICON_FILES:
            return None
        return jsonify({"error": "Unauthorized"}), 401

    client = app.test_client()
    resp = client.get("/bigas-logo.png")
    assert resp.status_code == 200
    assert "png" in (resp.content_type or "")
    blocked = client.get("/api/secret")
    assert blocked.status_code == 401
