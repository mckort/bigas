"""MCP Streamable HTTP handshake used by Cursor and Grok Bot."""
from __future__ import annotations

from flask import Flask

from app import register_mcp_jsonrpc_routes
from bigas.oauth.endpoints import register_mcp_oauth_routes
from bigas.oauth.store import reset_oauth_store_for_tests


def _client(monkeypatch):
    monkeypatch.setenv("SERVER_URL", "https://mcp.example.test")
    monkeypatch.setenv("CHAT_STORAGE_MODE", "memory")
    reset_oauth_store_for_tests()
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
    register_mcp_oauth_routes(app)
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
    authenticate = resp.headers.get("WWW-Authenticate") or ""
    assert "Bearer" in authenticate
    assert "resource_metadata=" in authenticate
    assert "oauth-protected-resource" in authenticate


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


def test_oauth_discovery_is_public(monkeypatch):
    client = _client(monkeypatch)
    protected = client.get("/.well-known/oauth-protected-resource")
    assert protected.status_code == 200
    assert protected.get_json()["resource"] == "https://mcp.example.test/mcp"
    assert protected.get_json()["authorization_servers"] == ["https://mcp.example.test"]

    server = client.get("/.well-known/oauth-authorization-server")
    assert server.status_code == 200
    body = server.get_json()
    assert body["issuer"] == "https://mcp.example.test"
    assert body["registration_endpoint"].endswith("/oauth/register")
    assert "S256" in body["code_challenge_methods_supported"]


def test_oauth_metadata_rejects_untrusted_host(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setenv("SERVER_URL", "https://bigas.me")
    server = client.get(
        "/.well-known/oauth-authorization-server",
        headers={"Host": "evil.com"},
    )
    assert server.status_code == 200
    assert server.get_json()["issuer"] == "https://bigas.me"


def test_oauth_metadata_follows_request_host(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setenv("SERVER_URL", "https://bigas.me")
    host = "mcp-marketing-343105851187.europe-north1.run.app"
    server = client.get("/.well-known/oauth-authorization-server", headers={"Host": host})
    assert server.status_code == 200
    assert server.get_json()["issuer"] == f"https://{host}"
    assert server.get_json()["registration_endpoint"] == f"https://{host}/oauth/register"

    denied = client.post(
        "/mcp",
        headers={"Host": host},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert denied.status_code == 401
    authenticate = denied.headers.get("WWW-Authenticate") or ""
    assert f'resource_metadata="https://{host}/.well-known/oauth-protected-resource"' in authenticate


def test_oauth_discovery_sends_cors(monkeypatch):
    client = _client(monkeypatch)
    preflight = client.options(
        "/oauth/register",
        headers={
            "Origin": "https://claude.ai",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers.get("Access-Control-Allow-Origin") == "*"

    listed = client.get("/.well-known/oauth-authorization-server")
    assert listed.headers.get("Access-Control-Allow-Origin") == "*"


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
