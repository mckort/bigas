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
    return user, None


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
