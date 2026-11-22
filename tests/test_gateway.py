"""Agent gateway: access levels, approvals, limits and the /mcp integration."""
from __future__ import annotations

import pytest
from flask import Flask, jsonify, request

from app import register_mcp_jsonrpc_routes
from bigas.gateway import policy
from bigas.gateway import service as gw
from bigas.gateway.store import reset_gateway_store_for_tests
from bigas.gateway.tiers import ACT, LOOK, NEVER, PROPOSE, tool_tier
from bigas.oauth.store import reset_oauth_store_for_tests

OWNER_KEY = "owner-key-123"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BIGAS_AGENT_GATEWAY", "on")
    monkeypatch.setenv("CHAT_STORAGE_MODE", "memory")
    monkeypatch.setenv("SERVER_URL", "https://bigas.example.test")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL_GATEWAY", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL_CHIEF", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL_PRODUCT", raising=False)
    reset_gateway_store_for_tests()
    reset_oauth_store_for_tests()
    gw.reset_cache_for_tests()
    yield
    reset_gateway_store_for_tests()
    gw.reset_cache_for_tests()


TOOLS = ["get_latest_report", "create_ticket", "trigger_deployment", "cleanup_old_reports"]


def _app():
    app = Flask(__name__)
    app.config["BIGAS_ACCESS_MODE"] = "restricted"
    app.config["BIGAS_ACCESS_KEYS"] = {OWNER_KEY}
    app.config["BIGAS_ACCESS_HEADER"] = "X-Bigas-Access-Key"
    calls = []
    app.config["CALLS"] = calls

    def make(name):
        def handler():
            calls.append((name, request.get_json(silent=True), request.headers.get("X-Bigas-Access-Key")))
            return jsonify({"summary": f"{name} ran", "args": request.get_json(silent=True)})

        handler.__name__ = f"h_{name}"
        app.add_url_rule(f"/mcp/tools/{name}", view_func=handler, methods=["POST"])

    for name in TOOLS:
        make(name)

    def manifest():
        return {
            "tools": [
                {"name": n, "description": n, "path": f"/mcp/tools/{n}", "method": "POST",
                 "parameters": {"type": "object", "properties": {}}}
                for n in TOOLS
            ]
        }

    register_mcp_jsonrpc_routes(app, manifest)
    return app


def _rpc(client, token, method, params=None):
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp


def _call(client, token, name, arguments=None):
    resp = _rpc(client, token, "tools/call", {"name": name, "arguments": arguments or {}})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["result"]


# ------------------------------------------------------------------ tiers and policy


def test_unknown_tools_need_approval_and_reads_are_look():
    assert tool_tier("brand_new_tool") == ACT
    assert tool_tier("fetch_something_new") == LOOK
    assert tool_tier("create_ticket") == PROPOSE
    assert tool_tier("cleanup_old_reports") == NEVER


def test_levels_decide_in_plain_words():
    look = {"id": "g", "name": "grok", "kind": "agent", "level": "look"}
    d = policy.decide(look, "create_ticket")
    assert d.outcome == policy.DENY
    assert "Propose" in d.reason and "cockpit" in d.reason

    operate = dict(look, level="operate")
    assert policy.decide(operate, "create_ticket").outcome == policy.ALLOW
    assert policy.decide(operate, "trigger_deployment").outcome == policy.HOLD
    assert policy.decide(operate, "cleanup_old_reports").outcome == policy.DENY


def test_owner_always_allowed_even_when_paused():
    owner = gw.owner_agent("owner-key", "You")
    assert policy.decide(owner, "trigger_deployment", paused=True).outcome == policy.ALLOW


def test_limits_deny_with_reset_hint():
    agent = {"id": "g", "name": "grok", "kind": "agent", "level": "propose"}
    d = policy.decide(agent, "create_ticket", used={"proposals_per_day": 20})
    assert d.outcome == policy.DENY and d.code == "limit_reached"
    assert "resets" in d.reason


def test_deny_list_and_allow_list_only_narrow():
    agent = {"id": "g", "name": "grok", "kind": "agent", "level": "operate", "deny_tools": ["create_*"]}
    assert policy.decide(agent, "create_ticket").code == "denied_tool"
    agent = {"id": "g", "name": "grok", "kind": "agent", "level": "look", "allow_tools": ["*"]}
    assert policy.decide(agent, "trigger_deployment").outcome == policy.DENY


# ------------------------------------------------------------------ /mcp integration


def test_owner_key_works_as_today():
    app = _app()
    client = app.test_client()
    result = _call(client, OWNER_KEY, "trigger_deployment", {"x": 1})
    assert result["isError"] is False
    assert app.config["CALLS"][-1][0] == "trigger_deployment"


def test_unknown_token_is_rejected():
    resp = _rpc(_app().test_client(), "bga_nope_nope", "tools/list")
    assert resp.status_code == 401


def test_agent_sees_only_its_tools_plus_gateway_tools():
    _, token = gw.connect_agent("Grok growth", "look")
    resp = _rpc(_app().test_client(), token, "tools/list")
    names = {t["name"] for t in resp.get_json()["result"]["tools"]}
    assert "get_latest_report" in names
    assert "create_ticket" not in names
    assert {"gateway_whoami", "get_approval_status", "list_my_approvals"} <= names


def test_agent_deny_explains_why():
    _, token = gw.connect_agent("grok", "look")
    result = _call(_app().test_client(), token, "create_ticket", {"title": "x"})
    assert result["isError"] is True
    assert "Look access" in result["content"][0]["text"]


def test_agent_allowed_call_runs_with_owner_credential_and_strips_intent():
    app = _app()
    _, token = gw.connect_agent("grok", "propose")
    result = _call(app.test_client(), token, "create_ticket", {"title": "x", "_intent": "track an idea"})
    assert result["isError"] is False
    name, args, key = app.config["CALLS"][-1]
    assert name == "create_ticket" and args == {"title": "x"} and key == OWNER_KEY
    rows = gw.recent_activity("grok")
    assert rows[0]["decision"] == "allowed" and rows[0]["args"] == ["title"]


def test_act_tool_is_held_then_runs_exactly_as_sent_after_approval():
    app = _app()
    client = app.test_client()
    _, token = gw.connect_agent("grok", "operate")
    result = _call(client, token, "trigger_deployment", {"project": "GPW", "_intent": "ship the fix"})
    assert result["isError"] is False
    pending = result["structuredContent"]
    assert pending["status"] == "pending_approval"
    assert not any(c[0] == "trigger_deployment" for c in app.config["CALLS"])

    queue = gw.pending_approvals()
    assert len(queue) == 1 and queue[0]["intent"] == "ship the fix"

    with app.app_context():
        done = gw.approve_approval(pending["approval_id"], by="me", dispatch=gw.dispatch_with_owner_key(app))
    assert done["status"] == "succeeded"
    assert app.config["CALLS"][-1][:2] == ("trigger_deployment", {"project": "GPW"})

    status = _call(client, token, "get_approval_status", {"approval_id": pending["approval_id"]})
    assert status["structuredContent"]["status"] == "succeeded"
    assert "trigger_deployment ran" in status["structuredContent"]["result"]


def test_denied_request_tells_agent_the_note():
    app = _app()
    client = app.test_client()
    _, token = gw.connect_agent("grok", "operate")
    pending = _call(client, token, "trigger_deployment", {})["structuredContent"]
    gw.deny_approval(pending["approval_id"], by="me", note="not on a Friday")
    status = _call(client, token, "get_approval_status", {"approval_id": pending["approval_id"]})
    assert status["structuredContent"]["status"] == "denied"
    assert "not on a Friday" in status["structuredContent"]["message"]


def test_tampered_approval_cannot_run():
    app = _app()
    _, token = gw.connect_agent("grok", "operate")
    pending = _call(app.test_client(), token, "trigger_deployment", {"project": "GPW"})["structuredContent"]
    store_approval = gw.get_approval(pending["approval_id"])
    store_approval["arguments"] = {"project": "EVIL"}
    from bigas.gateway.store import get_gateway_store

    get_gateway_store().put_approval(store_approval)
    with pytest.raises(gw.GatewayError):
        gw.approve_approval(pending["approval_id"], dispatch=gw.dispatch_with_owner_key(app))


def test_expired_request_cannot_be_approved(monkeypatch):
    app = _app()
    _, token = gw.connect_agent("grok", "operate")
    pending = _call(app.test_client(), token, "trigger_deployment", {})["structuredContent"]
    real = gw.time.time
    monkeypatch.setattr(gw.time, "time", lambda: real() + 3 * 3600)
    with pytest.raises(gw.GatewayError, match="expired"):
        gw.approve_approval(pending["approval_id"], dispatch=gw.dispatch_with_owner_key(app))


def test_pause_all_stops_agents_and_cancels_pending_but_not_owner():
    app = _app()
    client = app.test_client()
    _, token = gw.connect_agent("grok", "operate")
    pending = _call(client, token, "trigger_deployment", {})["structuredContent"]
    gw.set_paused(True, by="me")
    assert gw.get_approval(pending["approval_id"])["status"] == "cancelled"
    assert _call(client, token, "get_latest_report")["isError"] is True
    assert _call(client, OWNER_KEY, "get_latest_report")["isError"] is False


def test_new_token_revokes_old_one_immediately():
    app = _app()
    client = app.test_client()
    _, token = gw.connect_agent("grok", "look")
    new = gw.new_token("grok")
    assert _rpc(client, token, "tools/list").status_code == 401
    assert _rpc(client, new, "tools/list").status_code == 200


def test_whoami_explains_level_and_limits():
    _, token = gw.connect_agent("grok", "operate")
    result = _call(_app().test_client(), token, "gateway_whoami")
    data = result["structuredContent"]
    assert data["level"] == "operate"
    assert "trigger_deployment" in data["needs_owner_approval"]
    assert "get_latest_report" in data["runs_directly"]
    assert "calls per hour" in data["limits"]


def test_agent_cannot_read_other_agents_approvals():
    app = _app()
    client = app.test_client()
    _, a = gw.connect_agent("grok", "operate")
    _, b = gw.connect_agent("other", "operate")
    pending = _call(client, a, "trigger_deployment", {})["structuredContent"]
    result = _call(client, b, "get_approval_status", {"approval_id": pending["approval_id"]})
    assert result["isError"] is True


def test_agent_token_in_url_is_refused():
    _, token = gw.connect_agent("grok", "look")
    resp = _app().test_client().post(
        f"/mcp?access_key={token}", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert resp.status_code == 401


def test_gateway_requires_restricted_mode():
    with pytest.raises(ValueError, match="restricted"):
        gw.validate_gateway_config("open")
    gw.validate_gateway_config("restricted")


def test_gateway_off_changes_nothing(monkeypatch):
    monkeypatch.setenv("BIGAS_AGENT_GATEWAY", "off")
    gw.validate_gateway_config("open")
    resp = _rpc(_app().test_client(), OWNER_KEY, "tools/list")
    names = {t["name"] for t in resp.get_json()["result"]["tools"]}
    assert names == set(TOOLS)


def test_connect_names_are_unique_and_levels_checked():
    gw.connect_agent("grok", "look")
    with pytest.raises(gw.GatewayError, match="already exists"):
        gw.connect_agent("Grok", "look")
    with pytest.raises(gw.GatewayError, match="Level"):
        gw.connect_agent("x", "admin")


def test_connect_instructions_cover_common_clients():
    items = gw.connect_instructions("bga_t", "https://bigas.example.test")
    ids = {i["id"] for i in items}
    assert {"grok-api", "grok-build", "claude-code", "cursor", "curl"} <= ids
    assert all("https://bigas.example.test/mcp" in i["code"] for i in items)


# ------------------------------------------------------------------ cockpit API and bypasses


@pytest.fixture
def full_app(monkeypatch):
    monkeypatch.setenv("GA4_PROPERTY_ID", "123")
    monkeypatch.setenv("BIGAS_ACCESS_MODE", "restricted")
    monkeypatch.setenv("BIGAS_ACCESS_KEYS", OWNER_KEY)
    monkeypatch.setenv("CHAT_AUTH_MODE", "dev")
    monkeypatch.setenv("CHAT_DEV_TOKEN", "dev-token")
    monkeypatch.delenv("CHAT_ADMIN_EMAILS", raising=False)
    from app import create_app

    return create_app()


def test_agent_token_cannot_use_direct_tool_endpoints(full_app):
    _, token = gw.connect_agent("grok", "operate")
    resp = full_app.test_client().post(
        "/mcp/tools/get_latest_report", json={}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


def test_cockpit_api_connect_approve_flow(full_app):
    client = full_app.test_client()
    auth = {"Authorization": "Bearer dev-token"}
    resp = client.post("/api/gateway/agents", json={"name": "grok-growth", "level": "operate"}, headers=auth)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["token"].startswith("bga_")
    assert any(i["id"] == "grok-api" for i in body["instructions"])

    overview = client.get("/api/gateway", headers=auth).get_json()
    assert overview["enabled"] is True
    assert [a["id"] for a in overview["agents"]] == ["grok-growth"]
    assert [lvl["id"] for lvl in overview["levels"]] == ["look", "propose", "operate"]

    resp = client.patch("/api/gateway/agents/grok-growth", json={"level": "look"}, headers=auth)
    assert resp.get_json()["agent"]["level"] == "look"

    resp = client.post("/api/gateway/pause", json={"paused": True}, headers=auth)
    assert resp.get_json() == {"paused": True}

    detail = client.get("/api/gateway/agents/grok-growth", headers=auth).get_json()
    assert "tools" in detail["agent"] and "activity" in detail


def test_cockpit_api_needs_login(full_app):
    assert full_app.test_client().get("/api/gateway").status_code == 401
