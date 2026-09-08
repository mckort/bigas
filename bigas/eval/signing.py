"""HMAC tokens for clickable eval report links."""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional


def signing_secret() -> str:
    for name in (
        "EVAL_REPORT_SIGNING_SECRET",
        "X_POST_SIGNING_SECRET",
        "JIRA_AUTOMATION_WEBHOOK_SECRET",
        "X_API_SECRET",
    ):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    raw_keys = os.environ.get("BIGAS_ACCESS_KEYS") or ""
    for part in raw_keys.split(","):
        key = part.strip()
        if key:
            return key
    return ""


def sign_report(use_case: str, run_id: str, *, secret: Optional[str] = None) -> str:
    key = (secret if secret is not None else signing_secret()).encode("utf-8")
    msg = f"eval-report:{use_case}:{run_id}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def verify_report_token(
    use_case: str,
    run_id: str,
    token: str,
    *,
    secret: Optional[str] = None,
) -> bool:
    expected = sign_report(use_case, run_id, secret=secret)
    provided = (token or "").strip()
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)
