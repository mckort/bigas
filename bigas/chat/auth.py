"""Firebase JWT verification and dev-mode auth for the chat API."""
from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

from flask import g, jsonify, request

_firebase_initialized = False


def _init_firebase() -> bool:
    global _firebase_initialized
    if _firebase_initialized:
        return True

    project_id = (os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_PROJECT_ID") or "").strip()
    if not project_id:
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            cred_json = (os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()
            if cred_json:
                import json

                cred = credentials.Certificate(json.loads(cred_json))
                firebase_admin.initialize_app(cred, {"projectId": project_id})
            else:
                firebase_admin.initialize_app(options={"projectId": project_id})
        _firebase_initialized = True
        return True
    except Exception:
        return False


def chat_auth_mode() -> str:
    mode = (os.environ.get("CHAT_AUTH_MODE") or "").strip().lower()
    if mode in ("dev", "firebase"):
        return mode
    return "firebase" if _init_firebase() else "dev"


def verify_firebase_token(token: str) -> Optional[Dict[str, Any]]:
    if not _init_firebase():
        return None
    try:
        from firebase_admin import auth

        decoded = auth.verify_id_token(token)
        return {
            "uid": decoded.get("uid") or decoded.get("sub"),
            "email": decoded.get("email") or "",
        }
    except Exception:
        return None


def verify_dev_token(token: str) -> Optional[Dict[str, Any]]:
    expected = (os.environ.get("CHAT_DEV_TOKEN") or "bigas-dev-token").strip()
    if token and token == expected:
        return {"uid": "dev-user", "email": "dev@bigas.local"}
    return None


def authenticate_request() -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "", 1).strip() if auth_header.startswith("Bearer ") else ""
    if not token:
        return None, (jsonify({"error": "Missing authorization token"}), 401)

    mode = chat_auth_mode()
    user = verify_firebase_token(token) if mode == "firebase" else verify_dev_token(token)
    if not user and mode == "firebase":
        user = verify_dev_token(token)
    if not user:
        return None, (jsonify({"error": "Invalid or expired token"}), 401)
    if not is_chat_allowed(user):
        return None, (
            jsonify({"error": "This account is not allowed to use Bigas chat."}),
            403,
        )
    return user, None


def _parse_email_list(raw: str) -> set[str]:
    return {email.strip().lower() for email in (raw or "").split(",") if email.strip()}


def chat_allowed_emails() -> set[str]:
    """Emails permitted to use chat. Empty set means no allowlist (open)."""
    raw = (os.environ.get("CHAT_ALLOWED_EMAILS") or "").strip()
    if raw == "*":
        return set()
    explicit = _parse_email_list(raw)
    if explicit:
        return explicit
    # Fall back to admin list so a production Firebase deploy with only
    # CHAT_ADMIN_EMAILS set is not left open to any Google account.
    return _parse_email_list(os.environ.get("CHAT_ADMIN_EMAILS") or "")


def chat_admin_emails() -> set[str]:
    raw = (os.environ.get("CHAT_ADMIN_EMAILS") or "").strip()
    if not raw and chat_auth_mode() == "dev":
        return {"dev@bigas.local"}
    return _parse_email_list(raw)


def is_chat_admin(user: Dict[str, Any]) -> bool:
    email = (user.get("email") or "").strip().lower()
    return bool(email) and email in chat_admin_emails()


def is_chat_allowed(user: Dict[str, Any]) -> bool:
    if chat_auth_mode() != "firebase":
        return True
    allowed = chat_allowed_emails()
    if not allowed:
        return True
    email = (user.get("email") or "").strip().lower()
    return bool(email) and email in allowed


def require_chat_auth(view: Callable):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user, err = authenticate_request()
        if err:
            return err
        g.chat_user = user
        return view(*args, **kwargs)

    return wrapper


def verify_callback_secret() -> bool:
    secret = (os.environ.get("CHAT_CALLBACK_SECRET") or os.environ.get("BIGAS_ACCESS_KEYS") or "").split(",")[0].strip()
    if not secret:
        return False
    provided = request.headers.get("X-Bigas-Chat-Callback") or request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
    return bool(provided and provided == secret)
