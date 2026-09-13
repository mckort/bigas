"""Claude MCP OAuth (DCR + PKCE) alongside Cursor access keys."""
from __future__ import annotations

import os

from flask import Flask

os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "bigas-dev-token")

from app import register_mcp_jsonrpc_routes
from bigas.oauth import service
from bigas.oauth.endpoints import register_mcp_oauth_routes
from bigas.oauth.store import reset_oauth_store_for_tests


CLAUDE_REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def _app(monkeypatch):
    monkeypatch.setenv("SERVER_URL", "https://mcp.example.test")
    monkeypatch.setenv("CHAT_STORAGE_MODE", "memory")
    monkeypatch.setenv("CHAT_AUTH_MODE", "dev")
    monkeypatch.setenv("CHAT_DEV_TOKEN", "bigas-dev-token")
    monkeypatch.setenv("BIGAS_ACCESS_KEYS", "test-key")
    reset_oauth_store_for_tests()
    app = Flask(__name__)
    app.config["BIGAS_ACCESS_MODE"] = "restricted"
    app.config["BIGAS_ACCESS_KEYS"] = {"test-key"}
    app.config["BIGAS_ACCESS_HEADER"] = "X-Bigas-Access-Key"

    def manifest():
        return {"tools": []}

    register_mcp_jsonrpc_routes(app, manifest)
    register_mcp_oauth_routes(app)
    return app


def _register(client, redirect_uri=CLAUDE_REDIRECT):
    resp = client.post(
        "/oauth/register",
        json={
            "client_name": "Claude",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def test_dcr_rejects_claude_open_redirect_path(monkeypatch):
    client = _app(monkeypatch).test_client()
    resp = client.post(
        "/oauth/register",
        json={
            "redirect_uris": ["https://claude.ai/projects/foo/auth_callback/exfil"],
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_redirect_uri"


def test_dcr_rejects_unknown_redirect(monkeypatch):
    client = _app(monkeypatch).test_client()
    resp = client.post(
        "/oauth/register",
        json={"redirect_uris": ["https://evil.example/callback"]},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_redirect_uri"


def test_dcr_keeps_supported_grant_subset(monkeypatch):
    client = _app(monkeypatch).test_client()
    body = _register(client)
    assert body["token_endpoint_auth_method"] == "none"
    assert "authorization_code" in body["grant_types"]
    assert "refresh_token" in body["grant_types"]


def test_authorize_page_and_complete_with_dev_token(monkeypatch):
    app = _app(monkeypatch)
    client = app.test_client()
    registered = _register(client)
    verifier = "a" * 64
    challenge = service.pkce_challenge(verifier)
    page = client.get(
        "/oauth/authorize",
        query_string={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": CLAUDE_REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "resource": "https://mcp.example.test/mcp",
        },
    )
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Connect to Bigas" in html
    assert "claude.ai" in html
    assert "getIdToken(true)" in html
    assert "/api/auth/verify" in html

    complete = client.post(
        "/oauth/authorize/complete",
        headers={"Authorization": "Bearer bigas-dev-token"},
        json={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": CLAUDE_REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "resource": "https://mcp.example.test/mcp",
        },
    )
    assert complete.status_code == 200
    redirect_to = complete.get_json()["redirect_to"]
    assert redirect_to.startswith(CLAUDE_REDIRECT)
    assert "code=" in redirect_to
    assert "state=xyz" in redirect_to

    expired = client.post(
        "/oauth/authorize/complete",
        headers={"Authorization": "Bearer stale-firebase-token"},
        json={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": CLAUDE_REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert expired.status_code == 401
    assert "expired" in (expired.get_json() or {}).get("error", "").lower()


def test_token_exchange_and_mcp_initialize(monkeypatch):
    app = _app(monkeypatch)
    client = app.test_client()
    registered = _register(client)
    verifier = "b" * 64
    challenge = service.pkce_challenge(verifier)
    complete = client.post(
        "/oauth/authorize/complete",
        json={
            "access_key": "test-key",
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": CLAUDE_REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert complete.status_code == 200
    redirect_to = complete.get_json()["redirect_to"]
    code = redirect_to.split("code=", 1)[1].split("&", 1)[0]

    denied = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CLAUDE_REDIRECT,
            "client_id": registered["client_id"],
            "code_verifier": "wrong-verifier",
        },
    )
    assert denied.status_code == 400
    assert denied.get_json()["error"] == "invalid_grant"

    # Code is single-use even after a failed PKCE attempt? We pop on first exchange.
    # Re-issue so a valid verifier can succeed.
    complete = client.post(
        "/oauth/authorize/complete",
        json={
            "access_key": "test-key",
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": CLAUDE_REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    code = complete.get_json()["redirect_to"].split("code=", 1)[1].split("&", 1)[0]
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CLAUDE_REDIRECT,
            "client_id": registered["client_id"],
            "code_verifier": verifier,
            "resource": "https://mcp.example.test/mcp",
        },
    )
    assert token.status_code == 200
    body = token.get_json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["refresh_token"].startswith("mcp_rt_")

    mcp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {body['access_token']}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "claude", "version": "0"},
            },
        },
    )
    assert mcp.status_code == 200
    assert mcp.get_json()["result"]["serverInfo"]["name"] == "bigas-mcp"

    static = client.post(
        "/mcp",
        headers={"Authorization": "Bearer test-key"},
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "cursor", "version": "0"},
            },
        },
    )
    assert static.status_code == 200

    refreshed = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": body["refresh_token"],
            "client_id": registered["client_id"],
        },
    )
    assert refreshed.status_code == 200
    assert refreshed.get_json()["access_token"] != body["access_token"]

    reused = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": body["refresh_token"],
            "client_id": registered["client_id"],
        },
    )
    assert reused.status_code == 400
    assert reused.get_json()["error"] == "invalid_grant"


def test_loopback_redirect_is_accepted_for_claude_code(monkeypatch):
    client = _app(monkeypatch).test_client()
    body = _register(client, redirect_uri="http://127.0.0.1:3118/callback")
    assert body["redirect_uris"] == ["http://127.0.0.1:3118/callback"]
    page = client.get(
        "/oauth/authorize",
        query_string={
            "response_type": "code",
            "client_id": body["client_id"],
            "redirect_uri": "http://127.0.0.1:4242/callback",
            "code_challenge": service.pkce_challenge("c" * 64),
            "code_challenge_method": "S256",
        },
    )
    assert page.status_code == 200
    assert "local machine" in page.get_data(as_text=True)
