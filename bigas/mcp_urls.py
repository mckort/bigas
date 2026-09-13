"""Public base URL for MCP discovery and OAuth metadata."""
from __future__ import annotations

import os

DEFAULT_MCP_SERVER_URL = "https://mcp-marketing-343105851187.europe-north1.run.app"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "test.invalid"}


def _request_public_base_url() -> str:
    """Issuer/resource must match the host Claude called, not SERVER_URL."""
    try:
        from flask import has_request_context, request

        if not has_request_context():
            return ""
        host = (request.host or "").split(":")[0].strip().lower()
        if not host or host in _LOCAL_HOSTS:
            return ""
        forwarded = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        proto = forwarded if forwarded in {"http", "https"} else "https"
        return f"{proto}://{host}"
    except Exception:
        return ""


def mcp_public_base_url() -> str:
    from_request = _request_public_base_url()
    if from_request:
        return from_request
    base = (os.environ.get("SERVER_URL") or "").strip().rstrip("/")
    if base:
        return base
    return DEFAULT_MCP_SERVER_URL


def mcp_resource_url() -> str:
    return f"{mcp_public_base_url()}/mcp"


def oauth_protected_resource_url() -> str:
    return f"{mcp_public_base_url()}/.well-known/oauth-protected-resource"
