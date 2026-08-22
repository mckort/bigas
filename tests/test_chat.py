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


def test_thread_tracks_last_incoming_for_unread(client):
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    headers = _auth_headers()
    thread = client.post(
        "/api/chat/threads",
        headers=headers,
        data=json.dumps({"agent_id": "marketing"}),
    ).get_json()["thread"]
    thread_id = thread["thread_id"]

    store.add_message(thread_id, role="user", content="hello")
    listed = client.get("/api/chat/threads", headers=headers).get_json()["threads"]
    ours = next(t for t in listed if t["thread_id"] == thread_id)
    assert ours["last_message_role"] == "user"
    assert not ours.get("last_incoming_at")

    store.add_message(thread_id, role="assistant", content="hi back")
    listed = client.get("/api/chat/threads", headers=headers).get_json()["threads"]
    ours = next(t for t in listed if t["thread_id"] == thread_id)
    assert ours["last_message_role"] == "assistant"
    incoming = ours.get("last_incoming_at")
    assert incoming

    store.add_message(thread_id, role="user", content="thanks")
    listed = client.get("/api/chat/threads", headers=headers).get_json()["threads"]
    ours = next(t for t in listed if t["thread_id"] == thread_id)
    assert ours["last_message_role"] == "user"
    assert ours.get("last_incoming_at") == incoming


def test_activity_feed(client):
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    store.add_activity(type_="test", content="PR review posted", source="cto")

    resp = client.get("/api/feed", headers=_auth_headers())
    assert resp.status_code == 200
    events = resp.get_json()["events"]
    assert any("PR review" in e["content"] for e in events)


def test_delete_old_activity_keeps_recent_events():
    from datetime import datetime, timedelta, timezone

    from bigas.chat.db import MemoryChatStore

    store = MemoryChatStore()
    now = datetime.now(timezone.utc)
    store._activity = [
        {
            "id": "old",
            "type": "test",
            "content": "stale",
            "source": "system",
            "created_at": (now - timedelta(days=8)).isoformat(),
        },
        {
            "id": "fresh",
            "type": "test",
            "content": "recent",
            "source": "system",
            "created_at": (now - timedelta(days=1)).isoformat(),
        },
    ]

    deleted = store.delete_old_activity(keep_days=7)
    remaining = {e["id"] for e in store.list_activity()}
    assert deleted == 1
    assert remaining == {"fresh"}


def test_delete_old_activity_respects_max_to_delete():
    from datetime import datetime, timedelta, timezone

    from bigas.chat.db import MemoryChatStore

    store = MemoryChatStore()
    now = datetime.now(timezone.utc)
    store._activity = [
        {
            "id": f"old-{i}",
            "type": "test",
            "content": f"stale {i}",
            "source": "system",
            "created_at": (now - timedelta(days=10 - i)).isoformat(),
        }
        for i in range(3)
    ]

    deleted = store.delete_old_activity(keep_days=7, max_to_delete=2)
    remaining = {e["id"] for e in store._activity}
    assert deleted == 2
    assert remaining == {"old-2"}


def test_cleanup_old_activity_endpoint():
    from datetime import datetime, timedelta, timezone

    from flask import Flask

    from bigas.chat.db import get_chat_store
    from bigas.resources.chat.endpoints import chat_bp

    store = get_chat_store()
    now = datetime.now(timezone.utc)
    store._activity.append(
        {
            "id": "endpoint-old",
            "type": "test",
            "content": "stale endpoint event",
            "source": "system",
            "created_at": (now - timedelta(days=8)).isoformat(),
        }
    )
    store.add_activity(type_="test", content="fresh endpoint event", source="cto")

    app = Flask(__name__)
    app.register_blueprint(chat_bp)
    resp = app.test_client().post("/mcp/tools/cleanup_old_activity", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["keep_days"] == 7
    assert body["deleted"] >= 1
    contents = [e["content"] for e in store.list_activity(limit=100)]
    assert "stale endpoint event" not in contents
    assert "fresh endpoint event" in contents


def test_cleanup_old_activity_requires_access_key_in_restricted_mode():
    from flask import Flask

    from bigas.resources.chat.endpoints import chat_bp

    app = Flask(__name__)
    app.config["BIGAS_ACCESS_MODE"] = "restricted"
    app.config["BIGAS_ACCESS_KEYS"] = {"scheduler-key"}
    app.config["BIGAS_ACCESS_HEADER"] = "X-Bigas-Access-Key"
    app.register_blueprint(chat_bp)
    client = app.test_client()

    denied = client.post("/mcp/tools/cleanup_old_activity", json={})
    assert denied.status_code == 401

    allowed = client.post(
        "/mcp/tools/cleanup_old_activity",
        json={},
        headers={"X-Bigas-Access-Key": "scheduler-key"},
    )
    assert allowed.status_code == 200


def test_cleanup_old_activity_deletes_all_eligible_batches(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from flask import Flask

    from bigas.chat.db import MemoryChatStore
    from bigas.resources.chat.endpoints import chat_bp

    store = MemoryChatStore()
    now = datetime.now(timezone.utc)
    store._activity = [
        {
            "id": f"old-{i}",
            "type": "test",
            "content": f"stale {i}",
            "source": "system",
            "created_at": (now - timedelta(days=10)).isoformat(),
        }
        for i in range(3)
    ]

    monkeypatch.setattr(
        "bigas.resources.chat.endpoints.get_chat_store",
        lambda: store,
    )

    app = Flask(__name__)
    app.register_blueprint(chat_bp)
    resp = app.test_client().post(
        "/mcp/tools/cleanup_old_activity",
        json={"max_to_delete": 2},
    )

    assert resp.status_code == 200
    assert resp.get_json()["deleted"] == 3
    assert store.list_activity(limit=100) == []


def test_cleanup_old_activity_ignores_non_object_json():
    from flask import Flask

    from bigas.resources.chat.endpoints import chat_bp

    app = Flask(__name__)
    app.register_blueprint(chat_bp)
    resp = app.test_client().post("/mcp/tools/cleanup_old_activity", json=[])
    assert resp.status_code == 200
    assert resp.get_json()["keep_days"] == 7


def test_manifest_includes_cleanup_old_activity():
    from bigas.resources.chat.endpoints import get_manifest

    names = {t["name"] for t in get_manifest()["tools"]}
    assert "cleanup_old_activity" in names


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


def test_resolve_delegate_target_aliases():
    from bigas.agents.chief_of_staff import _resolve_delegate_target

    assert _resolve_delegate_target("devops") == "devops"
    assert _resolve_delegate_target("delegate_to_devops") == "devops"
    assert _resolve_delegate_target("DevOps") == "devops"
    assert _resolve_delegate_target("cto") == "cto"
    assert _resolve_delegate_target("unknown") is None


def test_chief_direct_tools_exclude_deploy_trigger():
    from bigas.agents.chief_of_staff import _chief_direct_tools

    names = {
        t["name"]
        for t in _chief_direct_tools(
            [
                {"name": "trigger_deployment", "description": "deploy"},
                {"name": "get_deployment_status", "description": "status"},
                {"name": "fetch_analytics_report", "description": "ga4"},
                {"name": "check_website_health", "description": "ping"},
                {"name": "create_jira_issue", "description": "file a ticket"},
                {"name": "lookup_jira", "description": "look up a ticket"},
            ]
        )
    }
    assert names == {"get_deployment_status", "check_website_health", "create_jira_issue", "lookup_jira"}


def test_create_jira_issue_is_shared_across_agents():
    from bigas.agents.chief_of_staff import (
        CHIEF_DIRECT_TOOL_NAMES,
        MUST_DELEGATE_TOOLS,
        SHARED_AGENT_TOOLS,
        _filter_tools_for_agent,
    )

    assert SHARED_AGENT_TOOLS == frozenset({"create_jira_issue", "lookup_jira"})
    for name in SHARED_AGENT_TOOLS:
        assert name in CHIEF_DIRECT_TOOL_NAMES
        assert name not in MUST_DELEGATE_TOOLS

    catalog = [
        {"name": "fetch_analytics_report", "description": "ga4"},
        {"name": "lookup_jira", "description": "look up a ticket"},
        {"name": "create_jira_issue", "description": "file a ticket"},
        {"name": "create_release_notes", "description": "notes"},
        {"name": "review_and_comment_pr", "description": "review"},
        {"name": "review_jira_issue", "description": "pm view"},
        {"name": "trigger_deployment", "description": "deploy"},
    ]
    for agent_id in ("marketing", "product", "cto", "devops"):
        names = [t["name"] for t in _filter_tools_for_agent(catalog, agent_id)]
        assert names[:2] == ["lookup_jira", "create_jira_issue"], agent_id
        assert "lookup_jira" in names
        assert "create_jira_issue" in names
    product_names = [t["name"] for t in _filter_tools_for_agent(catalog, "product")]
    assert "review_jira_issue" in product_names
    assert MUST_DELEGATE_TOOLS["review_jira_issue"] == "product"


def test_dispatch_chief_tool_allows_create_jira_issue():
    from bigas.agents.chief_of_staff import _dispatch_chief_tool

    called = {}

    class FakeMcp:
        def call_tool(self, name, args):
            called["name"] = name
            called["args"] = args
            payload = {
                "ok": True,
                "key": "GPWW-9",
                "url": "https://example.atlassian.net/browse/GPWW-9",
                "summary": "Fix tracking",
            }
            return {"is_error": False, "text": "", "structured": payload}

    result = _dispatch_chief_tool(
        "create_jira_issue",
        {"project_key": "GPWW", "summary": "Fix tracking", "description": "GA4 key events"},
        user_message="create a GPWW ticket for tracking",
        thread_id="thread-1",
        mcp_client=FakeMcp(),
    )
    assert called["name"] == "create_jira_issue"
    assert called["args"]["project_key"] == "GPWW"
    assert "GPWW-9" in (result or "")


def test_enrich_create_jira_issue_sets_marketing_for_marketing_agent():
    from bigas.agents.chief_of_staff import _enrich_tool_args

    args = _enrich_tool_args(
        "create_jira_issue",
        {"project_key": "GPWW", "summary": "CTA", "description": "Fix homepage CTA"},
        "create a GPWW ticket",
        caller_agent_id="marketing",
    )
    assert args["marketing"] is True

    product_args = _enrich_tool_args(
        "create_jira_issue",
        {"project_key": "GPWW", "summary": "CTA", "description": "Fix homepage CTA"},
        "create a GPWW ticket",
        caller_agent_id="product",
    )
    assert "marketing" not in product_args


def test_enrich_review_jira_issue_extracts_key_from_url():
    from bigas.agents.chief_of_staff import _enrich_tool_args

    args = _enrich_tool_args(
        "review_jira_issue",
        {},
        "comments on this: https://scaleupadvisor.atlassian.net/browse/VFA-17",
        caller_agent_id="product",
    )
    assert args["issue_key"] == "VFA-17"

    lookup_args = _enrich_tool_args(
        "lookup_jira",
        {"issue_key": "https://example.atlassian.net/browse/BIG-13"},
        "look this up",
        caller_agent_id="chief",
    )
    assert lookup_args["issue_key"] == "BIG-13"


def test_dispatch_chief_tool_rejects_unauthorized_tool():
    from bigas.agents.chief_of_staff import _dispatch_chief_tool

    class FakeMcp:
        def call_tool(self, name, args):
            raise AssertionError("should not call unauthorized tool")

    result = _dispatch_chief_tool(
        "trigger_deployment",
        {"project_key": "VFA"},
        user_message="deploy vfa",
        thread_id="thread-1",
        mcp_client=FakeMcp(),
    )
    assert "Delegated to devops" in result


def test_dispatch_chief_tool_blocks_disallowed_direct_tool():
    from bigas.agents.chief_of_staff import _dispatch_chief_tool

    class FakeMcp:
        def call_tool(self, name, args):
            raise AssertionError("should not call disallowed tool")

    result = _dispatch_chief_tool(
        "fetch_analytics_report",
        {},
        user_message="show analytics",
        thread_id="thread-1",
        mcp_client=FakeMcp(),
    )
    assert result == "Tool fetch_analytics_report is not allowed for Chief of Staff."


def test_dispatch_chief_tool_coerces_non_dict_args(monkeypatch):
    from bigas.agents.chief_of_staff import _dispatch_chief_tool

    called = {}

    def fake_run(agent_id, task, **kwargs):
        called["task"] = task
        return "Delegated to devops agent."

    monkeypatch.setattr("bigas.agents.chief_of_staff.run_specialist_task", fake_run)

    result = _dispatch_chief_tool(
        "trigger_deployment",
        ["bad", "args"],
        user_message="deploy vfa",
        thread_id="thread-1",
        mcp_client=None,
    )
    assert "Delegated to devops" in result
    assert called["task"] == "deploy vfa"


def test_dispatch_chief_tool_preserves_delegated_context(monkeypatch):
    from bigas.agents.chief_of_staff import _dispatch_chief_tool

    called = {}

    def fake_run(agent_id, task, **kwargs):
        called["task"] = task
        return "Delegated to devops agent."

    monkeypatch.setattr("bigas.agents.chief_of_staff.run_specialist_task", fake_run)

    _dispatch_chief_tool(
        "trigger_deployment",
        {"project_key": "VFA", "environment": "production"},
        user_message="please deploy",
        thread_id="thread-1",
        mcp_client=None,
    )
    assert "please deploy" in called["task"]
    assert "Context:" in called["task"]
    assert "VFA" in called["task"]


def test_mcp_tool_to_openai_def_allows_long_description():
    from bigas.agents.chief_of_staff import _mcp_tool_to_openai_def

    long_desc = "x" * 500
    out = _mcp_tool_to_openai_def({"name": "get_job_status", "description": long_desc})
    assert len(out["function"]["description"]) == 500


def test_chief_select_tool_prompt_documents_delegate(monkeypatch):
    from bigas.agents.chief_of_staff import _select_tool_via_llm

    captured = {}

    class FakeLlm:
        def complete(self, messages, temperature=0.2):
            captured["system"] = messages[0]["content"]
            return json.dumps(
                {
                    "action": "delegate",
                    "agent_id": "devops",
                    "task": "Deploy VFA to production",
                }
            )

    monkeypatch.setattr(
        "bigas.agents.chief_of_staff.get_llm_client",
        lambda feature="chat": (FakeLlm(), "gemini-3.1-pro-preview"),
    )

    text, tool_name, args = _select_tool_via_llm(
        "chief",
        {"system_prompt_goals": "Coordinate the team."},
        "please ask devops to ship vfa",
        [
            {"name": "fetch_analytics_report", "description": "ga4"},
            {"name": "trigger_deployment", "description": "deploy now"},
            {"name": "get_deployment_status", "description": "status of a run"},
        ],
        [],
    )
    assert not text
    assert tool_name == "__delegate__:devops"
    assert args["task"] == "Deploy VFA to production"
    system = captured["system"]
    assert '"action":"delegate"' in system
    assert "- get_deployment_status:" in system
    assert "- trigger_deployment:" not in system
    assert "- fetch_analytics_report:" not in system
    assert "devops" in system.lower()


def test_chief_deploy_intent_delegates_without_llm(monkeypatch):
    from bigas.agents.chief_of_staff import handle_chat_message
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    store.upsert_user("chief-deploy-user", "chief@bigas.local")
    chief = store.create_thread("chief-deploy-user", "chief")
    called = {}

    def fake_run(agent_id, task, **kwargs):
        called["agent_id"] = agent_id
        called["task"] = task
        called["thread_id"] = kwargs.get("thread_id")
        called["async_mode"] = kwargs.get("async_mode")
        return "Delegated to devops agent. Results will appear in this thread when ready."

    def boom(*_args, **_kwargs):
        raise AssertionError("LLM should not run for a deploy intent")

    monkeypatch.setattr("bigas.agents.chief_of_staff.run_specialist_task", fake_run)
    monkeypatch.setattr("bigas.agents.chief_of_staff.get_llm_client", boom)

    result = handle_chat_message(
        thread_id=chief["thread_id"],
        user_id="chief-deploy-user",
        user_message="deploy vcfieldassistant",
    )
    assert called["agent_id"] == "devops"
    assert called["task"] == "deploy vcfieldassistant"
    assert called["thread_id"] == chief["thread_id"]
    assert called["async_mode"] is True
    assert "Delegated to devops" in (result.get("message") or {}).get("content", "")


def test_chief_gemini_delegate_json_reaches_devops(monkeypatch):
    from bigas.agents.chief_of_staff import handle_chat_message
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    store.upsert_user("chief-json-user", "chief@bigas.local")
    chief = store.create_thread("chief-json-user", "chief")
    called = {}

    class FakeLlm:
        def complete(self, messages, temperature=0.2):
            return json.dumps(
                {
                    "action": "delegate",
                    "agent_id": "devops",
                    "task": "Deploy vcfieldassistant",
                }
            )

    def fake_run(agent_id, task, **kwargs):
        called["agent_id"] = agent_id
        called["task"] = task
        return "Delegated to devops agent. Results will appear in this thread when ready."

    monkeypatch.setattr(
        "bigas.agents.chief_of_staff.get_llm_client",
        lambda feature="chat": (FakeLlm(), "gemini-3.1-pro-preview"),
    )
    monkeypatch.setattr(
        "bigas.agents.chief_of_staff._list_chief_mcp_tools",
        lambda: (None, []),
    )
    monkeypatch.setattr("bigas.agents.chief_of_staff.run_specialist_task", fake_run)

    handle_chat_message(
        thread_id=chief["thread_id"],
        user_id="chief-json-user",
        user_message="delega till devops",
    )
    assert called["agent_id"] == "devops"
    assert "Deploy vcfieldassistant" in called["task"]


def test_chief_trigger_deployment_tool_is_rewritten_to_handoff(monkeypatch):
    from bigas.agents.chief_of_staff import handle_chat_message
    from bigas.chat.db import get_chat_store

    store = get_chat_store()
    store.upsert_user("chief-rewrite-user", "chief@bigas.local")
    chief = store.create_thread("chief-rewrite-user", "chief")
    called = {}

    class FakeLlm:
        def complete(self, messages, temperature=0.2):
            return json.dumps(
                {
                    "action": "tool",
                    "tool_name": "trigger_deployment",
                    "arguments": {"project_key": "VFA"},
                }
            )

    def fake_run(agent_id, task, **kwargs):
        called["agent_id"] = agent_id
        called["task"] = task
        return "Delegated to devops agent. Results will appear in this thread when ready."

    monkeypatch.setattr(
        "bigas.agents.chief_of_staff.get_llm_client",
        lambda feature="chat": (FakeLlm(), "gemini-3.1-pro-preview"),
    )
    monkeypatch.setattr(
        "bigas.agents.chief_of_staff._list_chief_mcp_tools",
        lambda: (None, []),
    )
    monkeypatch.setattr("bigas.agents.chief_of_staff.run_specialist_task", fake_run)

    handle_chat_message(
        thread_id=chief["thread_id"],
        user_id="chief-rewrite-user",
        user_message="can you start a production rollout for the field assistant site",
    )
    assert called["agent_id"] == "devops"
    assert "production rollout" in called["task"]


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


def test_discord_mirror_posts_to_product_thread(monkeypatch):
    from bigas.chat.activity import mirror_discord_message
    from bigas.chat.db import get_chat_store

    monkeypatch.setenv("CHAT_ENABLED", "true")
    monkeypatch.setenv("CHAT_AUTH_MODE", "dev")
    monkeypatch.delenv("BIGAS_EMAIL_SYNC_USER_UID", raising=False)
    monkeypatch.delenv("BIGAS_EMAIL_SYNC_USER_EMAIL", raising=False)
    monkeypatch.delenv("CHAT_ADMIN_EMAILS", raising=False)

    store = get_chat_store()
    mirror_discord_message(
        "",
        "**Research complete** `VFA-17` — Founder section",
        chat_agent_id="product",
    )
    thread = store.get_or_create_agent_thread("dev-user", "product")
    messages = store.list_messages(thread["thread_id"])
    assert any("VFA-17" in (m.get("content") or "") for m in messages)


def test_discord_mirror_skips_on_its_way_ping(monkeypatch):
    from bigas.chat.activity import mirror_discord_message

    posted = []

    def fake_thread(agent_id, content, **kwargs):
        posted.append(content)
        return {"thread_id": "t1"}

    monkeypatch.setattr("bigas.chat.activity.post_to_agent_thread", fake_thread)
    mirror_discord_message(
        "https://discord.example/marketing",
        "# 📊 Weekly Analytics Report on its way...",
        chat_agent_id="marketing",
    )
    assert posted == []


def test_resolve_discord_chat_agent_from_webhook_env(monkeypatch):
    from bigas.chat.activity import resolve_discord_chat_agent

    monkeypatch.setenv("DISCORD_WEBHOOK_URL_PRODUCT", "https://discord.example/pm")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_CTO", "https://discord.example/cto")
    assert resolve_discord_chat_agent("https://discord.example/pm") == "product"
    assert resolve_discord_chat_agent("https://discord.example/cto") == "cto"
    assert resolve_discord_chat_agent("", chat_agent_id="devops") == "devops"


def test_post_to_discord_mirrors_chat_without_webhook(monkeypatch):
    from bigas.chat.activity import post_to_agent_thread
    from bigas.chat.db import get_chat_store
    from bigas.discord_webhook import post_to_discord

    monkeypatch.setenv("CHAT_ENABLED", "true")
    monkeypatch.setenv("CHAT_AUTH_MODE", "dev")
    monkeypatch.delenv("BIGAS_EMAIL_SYNC_USER_UID", raising=False)
    monkeypatch.delenv("BIGAS_EMAIL_SYNC_USER_EMAIL", raising=False)
    monkeypatch.delenv("CHAT_ADMIN_EMAILS", raising=False)

    posted = post_to_discord("", "Site down: example.com", chat_agent_id="cto")
    assert posted is False
    store = get_chat_store()
    thread = store.get_or_create_agent_thread("dev-user", "cto")
    messages = store.list_messages(thread["thread_id"])
    assert any("Site down" in (m.get("content") or "") for m in messages)
    # Dedup: identical follow-up must not add a second message.
    before = len(messages)
    post_to_agent_thread("cto", "Site down: example.com")
    assert len(store.list_messages(thread["thread_id"])) == before


def test_post_long_to_discord_mirrors_full_text_once(monkeypatch):
    from bigas.discord_webhook import post_long_to_discord

    chat_calls = []
    discord_calls = []

    def fake_thread(agent_id, content, **kwargs):
        chat_calls.append({"agent_id": agent_id, "content": content})
        return {"thread_id": "marketing-thread"}

    def fake_short(webhook_url, message, **kwargs):
        discord_calls.append(message)
        return True

    monkeypatch.setattr("bigas.chat.activity.post_to_agent_thread", fake_thread)
    monkeypatch.setattr("bigas.discord_webhook.post_to_discord", fake_short)

    body = "A" * 2000 + "\n" + "B" * 200
    post_long_to_discord(
        "https://discord.example/marketing",
        body,
        chunk_size=1900,
        chat_agent_id="marketing",
    )
    assert len(chat_calls) == 1
    assert chat_calls[0]["agent_id"] == "marketing"
    assert chat_calls[0]["content"] == body
    assert len(discord_calls) >= 2


def test_post_to_agent_thread_writes_product_message(monkeypatch):
    from bigas.chat.activity import post_to_agent_thread
    from bigas.chat.db import get_chat_store

    monkeypatch.setenv("CHAT_AUTH_MODE", "dev")
    monkeypatch.delenv("BIGAS_EMAIL_SYNC_USER_UID", raising=False)
    monkeypatch.delenv("BIGAS_EMAIL_SYNC_USER_EMAIL", raising=False)
    monkeypatch.delenv("CHAT_ADMIN_EMAILS", raising=False)

    store = get_chat_store()
    posted = post_to_agent_thread(
        "product",
        "**New X post draft ready for approval**",
        metadata={"source": "generate_weekly_x_post"},
    )
    assert posted is not None
    thread = store.get_or_create_agent_thread("dev-user", "product")
    assert posted["thread_id"] == thread["thread_id"]
    messages = store.list_messages(thread["thread_id"])
    assert any("X post draft" in (m.get("content") or "") for m in messages)
    assert posted["metadata"]["agent_id"] == "product"


def test_post_to_agent_thread_skips_when_chat_disabled(monkeypatch):
    from bigas.chat.activity import post_to_agent_thread

    monkeypatch.setenv("CHAT_ENABLED", "false")
    assert post_to_agent_thread("product", "hello") is None


def test_marketing_report_helper_posts_to_chat(monkeypatch):
    from bigas.chat.activity import post_marketing_report_to_chat

    posted = {}

    def fake_thread(agent_id, content, **kwargs):
        posted["agent_id"] = agent_id
        posted["content"] = content
        posted["source"] = (kwargs.get("metadata") or {}).get("source")
        return {"thread_id": "marketing-thread", "message_id": "m1"}

    monkeypatch.setenv("CHAT_ENABLED", "true")
    monkeypatch.setattr("bigas.chat.activity.post_to_agent_thread", fake_thread)

    posted_msg = post_marketing_report_to_chat("## 📊 Google Ads Portfolio Report\n\nSpend is down.")
    assert posted_msg["thread_id"] == "marketing-thread"
    assert posted["agent_id"] == "marketing"
    assert "Spend is down" in posted["content"]
    assert posted["source"] == "marketing_report"

    posted.clear()
    skipped = post_marketing_report_to_chat(
        "# 📊 Weekly Analytics Report on its way...",
        skip_status_pings=True,
    )
    assert skipped is None
    assert posted == {}


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
