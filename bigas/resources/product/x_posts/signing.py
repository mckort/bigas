"""HMAC tokens for X-post approval links."""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional


def signing_secret() -> str:
    for name in (
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


def sign_draft_id(draft_id: str, *, secret: Optional[str] = None) -> str:
    key = (secret if secret is not None else signing_secret()).encode("utf-8")
    msg = (draft_id or "").encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return digest[:32]


def verify_draft_token(draft_id: str, token: str, *, secret: Optional[str] = None) -> bool:
    expected = sign_draft_id(draft_id, secret=secret)
    provided = (token or "").strip()
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)
