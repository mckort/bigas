"""Shared access-key auth for Bigas HTTP/MCP endpoints."""
from __future__ import annotations

import os
from functools import wraps
from typing import Callable, Optional

from flask import current_app, jsonify, request

from bigas.resources.product.jira_automation.service import verify_webhook_secret


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


def _extract_bearer_or_header_token(*header_names: str) -> Optional[str]:
    for name in header_names:
        value = (request.headers.get(name) or "").strip()
        if value:
            return value
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    return None


def verify_evaluate_goals_webhook_auth():
    """
    Return None if allowed, or a 401/503 response if denied.

    The evaluate-goals scheduler webhook always requires auth, even when
    BIGAS_ACCESS_MODE=open. Accepts BIGAS_ACCESS_KEY or legacy CRON_SECRET.
    """
    header_name = current_app.config.get("BIGAS_ACCESS_HEADER", "X-Bigas-Access-Key")
    expected_keys = current_app.config.get("BIGAS_ACCESS_KEYS") or set()
    cron_secret = (os.environ.get("CRON_SECRET") or "").strip()

    provided_key = (
        request.headers.get(header_name)
        or request.args.get("access_key")
        or _extract_bearer_or_header_token()
    )
    cron_provided = _extract_bearer_or_header_token("X-Cron-Secret") or provided_key

    if expected_keys and provided_key and provided_key in expected_keys:
        return None
    if cron_secret and cron_provided and verify_webhook_secret(cron_provided, cron_secret):
        return None

    if not expected_keys and not cron_secret:
        response = jsonify(
            {
                "detail": (
                    "Evaluate-goals webhook is not configured "
                    "(set BIGAS_ACCESS_KEYS or CRON_SECRET)"
                )
            }
        )
        response.status_code = 503
        return response

    return _unauthorized({"detail": "Invalid or missing access key"})


def require_bigas_access_key(view: Callable):
    @wraps(view)
    def wrapper(*args, **kwargs):
        err = verify_bigas_access_key()
        if err is not None:
            return err
        return view(*args, **kwargs)

    return wrapper
