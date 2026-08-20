"""MCP Streamable HTTP handshake used by Cursor and Grok Bot."""
from __future__ import annotations

from flask import Flask

from app import register_mcp_jsonrpc_routes


def _client(monkeypatch):
    monkeypatch.setenv("SERVER_URL", "https://mcp.example.test")
    app = Flask(__name__)
    app.config["BIGAS_ACCESS_MODE"] = "restricted"
    app.config["BIGAS_ACCESS_KEYS"] = {"test-key"}
    app.config["BIGAS_ACCESS_HEADER"] = "X-Bigas-Access-Key"

    def manifest():
        return {
            "tools": [
                {
                    "name": "get_latest_report",
                    "description": "latest",
                    "parameters": {"type": "object", "properties": {}},
                }
            ]
        }

    register_mcp_jsonrpc_routes(app, manifest)
    return app.test_client()


def test_get_mcp_returns_405_without_sse(monkeypatch):
    resp = _client(monkeypatch).get("/mcp")
    assert resp.status_code == 405
    assert resp.headers.get("Allow") == "POST"
    assert "text/event-stream" not in (resp.content_type or "")


def test_post_mcp_requires_key_and_sends_www_authenticate(monkeypatch):
    resp = _client(monkeypatch).post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
    )
    assert resp.status_code == 401
    assert "Bearer" in (resp.headers.get("WWW-Authenticate") or "")


def test_initialize_and_tools_list_with_bearer(monkeypatch):
    headers = {"Authorization": "Bearer test-key"}
    client = _client(monkeypatch)
    init = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
    )
    assert init.status_code == 200
    body = init.get_json()
    assert body["result"]["serverInfo"]["name"] == "bigas-mcp"

    listed = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200
    tools = listed.get_json()["result"]["tools"]
    assert any(t.get("name") == "get_latest_report" for t in tools)


def test_well_known_mcp_card_is_public(monkeypatch):
    resp = _client(monkeypatch).get("/.well-known/mcp.json")
    assert resp.status_code == 200
    card = resp.get_json()
    assert card["transport"]["baseUrl"] == "https://mcp.example.test"
    assert card["auth"]["optional"] is False
    assert card["auth"]["header"] == "X-Bigas-Access-Key"


def test_oauth_discovery_is_404_not_401(monkeypatch):
    client = _client(monkeypatch)
    for path in (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
    ):
        resp = client.get(path)
        assert resp.status_code == 404, path
        assert resp.get_json()["error"] == "oauth_not_supported"


def test_tools_call_uses_summary_as_text(monkeypatch):
    from flask import jsonify

    monkeypatch.setenv("SERVER_URL", "https://mcp.example.test")
    app = Flask(__name__)
    app.config["BIGAS_ACCESS_MODE"] = "restricted"
    app.config["BIGAS_ACCESS_KEYS"] = {"test-key"}
    app.config["BIGAS_ACCESS_HEADER"] = "X-Bigas-Access-Key"

    @app.route("/mcp/tools/autofix_pr", methods=["POST"])
    def autofix_pr():
        return jsonify(
            {
                "success": True,
                "launched": True,
                "agent_url": "https://cursor.com/agents/bc-123",
                "summary": "Autofix is running (round 2/5). Follow the agent: https://cursor.com/agents/bc-123",
            }
        )

    def manifest():
        return {
            "tools": [
                {
                    "name": "autofix_pr",
                    "description": "launch",
                    "path": "/mcp/tools/autofix_pr",
                    "method": "POST",
                    "parameters": {"type": "object", "properties": {}},
                }
            ]
        }

    register_mcp_jsonrpc_routes(app, manifest)
    resp = app.test_client().post(
        "/mcp",
        headers={"Authorization": "Bearer test-key"},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "autofix_pr", "arguments": {"repo": "a/b", "pr_number": 1}},
        },
    )
    assert resp.status_code == 200
    result = resp.get_json()["result"]
    text = result["content"][0]["text"]
    assert text.startswith("Autofix is running")
    assert text.startswith("{") is False
    assert result["structuredContent"]["launched"] is True
    assert result["structuredContent"]["summary"].startswith("Autofix is running")
