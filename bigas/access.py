"""Shared access-key auth for Bigas HTTP/MCP endpoints."""
from __future__ import annotations

from functools import wraps
from typing import Callable, Optional

from flask import current_app, jsonify, request


def _unauthorized(payload):
    response = jsonify(payload)
    response.status_code = 401
    response.headers["WWW-Authenticate"] = 'Bearer realm="bigas-mcp"'
    return response


def verify_bigas_access_key():
    """Return None if allowed, or a 401 response if denied."""
    mode = current_app.config.get("BIGAS_ACCESS_MODE", "open")
    if mode != "restricted":
        return None

    header_name = current_app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
    expected_keys = current_app.config.get("BIGAS_ACCESS_KEYS") or set()

    provided_key = (
        request.headers.get(header_name)
        or request.args.get("access_key")
        or (request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip() or None)
    )
    if not provided_key or provided_key not in expected_keys:
        return _unauthorized({"detail": "Invalid or missing access key"})
    return None


def require_bigas_access_key(view: Callable):
    @wraps(view)
    def wrapper(*args, **kwargs):
        err = verify_bigas_access_key()
        if err is not None:
            return err
        return view(*args, **kwargs)

    return wrapper
