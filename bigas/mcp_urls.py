"""Public base URL for MCP discovery and OAuth metadata."""
from __future__ import annotations

import os

DEFAULT_MCP_SERVER_URL = "https://mcp-marketing-343105851187.europe-north1.run.app"


def mcp_public_base_url() -> str:
    base = (os.environ.get("SERVER_URL") or "").strip().rstrip("/")
    if base:
        return base
    try:
        from flask import has_request_context, request

        if has_request_context():
            return (request.host_url or "").rstrip("/")
    except Exception:
        pass
    return DEFAULT_MCP_SERVER_URL


def mcp_resource_url() -> str:
    return f"{mcp_public_base_url()}/mcp"


def oauth_protected_resource_url() -> str:
    return f"{mcp_public_base_url()}/.well-known/oauth-protected-resource"
