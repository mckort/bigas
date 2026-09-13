"""OAuth 2.1 authorization-code + PKCE for MCP clients (Claude), plus token checks."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bigas.mcp_urls import mcp_public_base_url, mcp_resource_url
from bigas.oauth.store import get_oauth_store

ACCESS_TOKEN_TTL_SECONDS = 3600
AUTH_CODE_TTL_SECONDS = 300
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600
LOOPBACK_HOSTS = {"localhost", "127.0.0.1"}
ALLOWED_HTTPS_REDIRECTS = {
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
}
CLAUDE_REDIRECT_HOSTS = {
    "claude.ai",
    "www.claude.ai",
    "claude.com",
    "www.claude.com",
}
_DEV_TOKEN_SECRET = b"bigas-mcp-oauth-dev"


def _config_or_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            cfg_val = current_app.config.get(name)
            if isinstance(cfg_val, str) and cfg_val.strip():
                return cfg_val.strip()
    except RuntimeError:
        pass
    return ""


def _first_access_key() -> str:
    raw = (os.environ.get("BIGAS_ACCESS_KEYS") or "").strip()
    if raw:
        return raw.split(",")[0].strip()
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            keys = current_app.config.get("BIGAS_ACCESS_KEYS") or set()
            if keys:
                return sorted(str(key) for key in keys)[0]
    except RuntimeError:
        pass
    return ""


def _is_dev_auth_mode() -> bool:
    mode = (_config_or_env("CHAT_AUTH_MODE") or "dev").strip().lower()
    return mode == "dev"


def _allowed_https_redirects() -> set[str]:
    allowed = {item.rstrip("/") for item in ALLOWED_HTTPS_REDIRECTS}
    extra = (os.environ.get("MCP_OAUTH_ALLOWED_HTTPS_REDIRECTS") or "").strip()
    if extra:
        for uri in extra.split(","):
            part = uri.strip()
            if part:
                allowed.add(part.rstrip("/"))
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            cfg_extra = current_app.config.get("MCP_OAUTH_ALLOWED_HTTPS_REDIRECTS")
            if isinstance(cfg_extra, str) and cfg_extra.strip():
                for uri in cfg_extra.split(","):
                    part = uri.strip()
                    if part:
                        allowed.add(part.rstrip("/"))
            elif isinstance(cfg_extra, (list, tuple, set)):
                for uri in cfg_extra:
                    part = str(uri).strip()
                    if part:
                        allowed.add(part.rstrip("/"))
    except RuntimeError:
        pass
    return allowed


def token_secret() -> bytes:
    explicit = _config_or_env("MCP_OAUTH_TOKEN_SECRET")
    if explicit:
        return explicit.encode("utf-8")
    first_key = _first_access_key()
    if first_key:
        return first_key.encode("utf-8")
    if _is_dev_auth_mode():
        return _DEV_TOKEN_SECRET
    raise RuntimeError(
        "MCP OAuth token secret is not configured. Set MCP_OAUTH_TOKEN_SECRET or BIGAS_ACCESS_KEYS."
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_json(payload: dict[str, Any]) -> str:
    return _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url(digest)


def verify_pkce(verifier: str, challenge: str, method: str = "S256") -> bool:
    if (method or "S256").upper() != "S256":
        return False
    if not verifier or not challenge:
        return False
    return hmac.compare_digest(pkce_challenge(verifier), challenge)


def is_loopback_redirect(uri: str) -> bool:
    parsed = urlparse(uri)
    return parsed.scheme == "http" and (parsed.hostname or "") in LOOPBACK_HOSTS and parsed.path == "/callback"


def is_allowed_redirect(uri: str) -> bool:
    if not uri:
        return False
    if uri.rstrip("/") in _allowed_https_redirects():
        return True
    parsed = urlparse(uri)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host in CLAUDE_REDIRECT_HOSTS and "auth_callback" in (parsed.path or ""):
        return True
    return is_loopback_redirect(uri)


def redirect_uris_match(registered: str, provided: str) -> bool:
    if registered == provided:
        return True
    a = urlparse(registered)
    b = urlparse(provided)
    if a.scheme != b.scheme or a.path != b.path:
        return False
    if a.scheme == "http" and (a.hostname or "") in LOOPBACK_HOSTS and (b.hostname or "") in LOOPBACK_HOSTS:
        return a.hostname == b.hostname
    return False


def client_accepts_redirect(client: dict[str, Any], redirect_uri: str) -> bool:
    for registered in client.get("redirect_uris") or []:
        if redirect_uris_match(str(registered), redirect_uri):
            return True
    return False


def filter_redirect_uris(uris: list[str]) -> list[str]:
    return [uri for uri in uris if isinstance(uri, str) and is_allowed_redirect(uri.strip())]


def authorization_server_metadata() -> dict[str, Any]:
    base = mcp_public_base_url()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": ["mcp"],
        "authorization_response_iss_parameter_supported": True,
    }


def protected_resource_metadata() -> dict[str, Any]:
    base = mcp_public_base_url()
    return {
        "resource": mcp_resource_url(),
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
    }


def register_client(body: dict[str, Any]) -> dict[str, Any]:
    requested_uris = body.get("redirect_uris") or []
    if not isinstance(requested_uris, list):
        raise ValueError("invalid_redirect_uri")
    redirect_uris = filter_redirect_uris([str(uri).strip() for uri in requested_uris])
    if not redirect_uris:
        raise ValueError("invalid_redirect_uri")

    requested_grants = body.get("grant_types") or ["authorization_code", "refresh_token"]
    if not isinstance(requested_grants, list):
        requested_grants = ["authorization_code", "refresh_token"]
    grant_types = [
        grant
        for grant in requested_grants
        if grant in {"authorization_code", "refresh_token"}
    ]
    if "authorization_code" not in grant_types:
        grant_types.insert(0, "authorization_code")
    if "refresh_token" not in grant_types:
        grant_types.append("refresh_token")

    client_id = f"mcp_cli_{secrets.token_urlsafe(16)}"
    record = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": redirect_uris,
        "grant_types": grant_types,
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "client_name": str(body.get("client_name") or "MCP client")[:200],
    }
    get_oauth_store().put_client(client_id, record)
    return record


def get_client(client_id: str) -> Optional[dict[str, Any]]:
    if not client_id:
        return None
    return get_oauth_store().get_client(client_id)


def issue_authorization_code(
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    email: str,
    uid: str,
    resource: str = "",
    scope: str = "mcp",
) -> str:
    code = secrets.token_urlsafe(32)
    get_oauth_store().put_code(
        code,
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": (code_challenge_method or "S256").upper(),
            "email": email,
            "uid": uid,
            "resource": resource or mcp_resource_url(),
            "scope": scope or "mcp",
            "exp": time.time() + AUTH_CODE_TTL_SECONDS,
        },
    )
    return code


def build_redirect(redirect_uri: str, params: dict[str, str]) -> str:
    parsed = urlparse(redirect_uri)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def issue_access_token(*, email: str, uid: str, scope: str = "mcp") -> tuple[str, int]:
    exp = int(time.time()) + ACCESS_TOKEN_TTL_SECONDS
    payload = {
        "email": email,
        "uid": uid,
        "exp": exp,
        "scope": scope,
        "typ": "mcp_at",
        "jti": secrets.token_urlsafe(8),
    }
    body = _b64url_json(payload)
    sig = hmac.new(token_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}", ACCESS_TOKEN_TTL_SECONDS


def verify_access_token(token: str) -> Optional[dict[str, Any]]:
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    if not body or not sig:
        return None
    expected = hmac.new(token_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("typ") != "mcp_at":
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload


def issue_refresh_token(*, client_id: str, email: str, uid: str, scope: str = "mcp") -> str:
    token = f"mcp_rt_{secrets.token_urlsafe(32)}"
    get_oauth_store().put_refresh(
        _sha256_hex(token),
        {
            "client_id": client_id,
            "email": email,
            "uid": uid,
            "scope": scope,
            "exp": time.time() + REFRESH_TOKEN_TTL_SECONDS,
        },
    )
    return token


def token_response(*, client_id: str, email: str, uid: str, scope: str = "mcp") -> dict[str, Any]:
    access_token, expires_in = issue_access_token(email=email, uid=uid, scope=scope)
    refresh_token = issue_refresh_token(client_id=client_id, email=email, uid=uid, scope=scope)
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "scope": scope,
    }


def exchange_authorization_code(
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
    resource: str = "",
) -> dict[str, Any]:
    record = get_oauth_store().pop_code(code)
    if not record:
        raise ValueError("invalid_grant")
    if float(record.get("exp") or 0) < time.time():
        raise ValueError("invalid_grant")
    if record.get("client_id") != client_id:
        raise ValueError("invalid_grant")
    if record.get("redirect_uri") != redirect_uri:
        raise ValueError("invalid_grant")
    if resource and record.get("resource") and resource != record.get("resource"):
        raise ValueError("invalid_target")
    if not verify_pkce(code_verifier, str(record.get("code_challenge") or ""), str(record.get("code_challenge_method") or "S256")):
        raise ValueError("invalid_grant")
    return token_response(
        client_id=client_id,
        email=str(record.get("email") or ""),
        uid=str(record.get("uid") or ""),
        scope=str(record.get("scope") or "mcp"),
    )


def exchange_refresh_token(*, refresh_token: str, client_id: str) -> dict[str, Any]:
    store = get_oauth_store()
    token_hash = _sha256_hex(refresh_token)
    record = store.get_refresh(token_hash)
    if not record:
        raise ValueError("invalid_grant")
    if record.get("client_id") != client_id:
        raise ValueError("invalid_grant")
    store.delete_refresh(token_hash)
    return token_response(
        client_id=client_id,
        email=str(record.get("email") or ""),
        uid=str(record.get("uid") or ""),
        scope=str(record.get("scope") or "mcp"),
    )
