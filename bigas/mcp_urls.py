"""Public base URL for MCP discovery and OAuth metadata."""
from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_MCP_SERVER_URL = "https://mcp-marketing-343105851187.europe-north1.run.app"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "test.invalid"}
_RUN_APP_SUFFIX = ".run.app"


def _url_sources() -> list[str]:
    sources = [DEFAULT_MCP_SERVER_URL]
    server_url = (os.environ.get("SERVER_URL") or "").strip().rstrip("/")
    if server_url:
        sources.append(server_url)
    extra = (os.environ.get("MCP_PUBLIC_ALLOWED_HOSTS") or "").strip()
    if extra:
        for part in extra.split(","):
            item = part.strip()
            if item:
                sources.append(item if "://" in item else f"https://{item}")
    return sources


def _allowed_public_host_identities() -> frozenset[str]:
    identities: set[str] = set()
    for raw in _url_sources():
        parsed = urlparse(raw)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            continue
        identities.add(hostname)
        if parsed.port:
            identities.add(f"{hostname}:{parsed.port}")
        netloc = (parsed.netloc or "").lower()
        if netloc:
            identities.add(netloc)
    return frozenset(identities)


def _request_host_allowed(host: str) -> bool:
    """True when Host may be reflected in OAuth discovery metadata."""
    normalized = (host or "").strip().lower()
    if not normalized or normalized in _LOCAL_HOSTS:
        return False
    if normalized in _allowed_public_host_identities():
        return True
    hostname = urlparse(f"//{normalized}").hostname or ""
    if not hostname:
        return False
    if hostname in _allowed_public_host_identities():
        return True
    return hostname.endswith(_RUN_APP_SUFFIX)


def _request_public_base_url() -> str:
    """Issuer/resource must match the host Claude called, not SERVER_URL."""
    try:
        from flask import has_request_context, request

        if not has_request_context():
            return ""
        host = (request.host or "").strip()
        if not host or not _request_host_allowed(host):
            return ""
        scheme = (request.scheme or "https").lower()
        if scheme not in {"http", "https"}:
            scheme = "https"
        if scheme == "http":
            scheme = "https"
        return f"{scheme}://{host}".rstrip("/")
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
