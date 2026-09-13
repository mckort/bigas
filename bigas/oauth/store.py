"""Persist MCP OAuth clients, auth codes, and refresh tokens."""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional


def _now() -> float:
    return time.time()


class MemoryOAuthStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.clients: dict[str, dict[str, Any]] = {}
        self.codes: dict[str, dict[str, Any]] = {}
        self.refresh: dict[str, dict[str, Any]] = {}

    def put_client(self, client_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            self.clients[client_id] = dict(record)

    def get_client(self, client_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            record = self.clients.get(client_id)
            return dict(record) if record else None

    def put_code(self, code: str, record: dict[str, Any]) -> None:
        with self._lock:
            self.codes[code] = dict(record)

    def pop_code(self, code: str) -> Optional[dict[str, Any]]:
        with self._lock:
            record = self.codes.pop(code, None)
            return dict(record) if record else None

    def put_refresh(self, token_hash: str, record: dict[str, Any]) -> None:
        with self._lock:
            self.refresh[token_hash] = dict(record)

    def get_refresh(self, token_hash: str) -> Optional[dict[str, Any]]:
        with self._lock:
            record = self.refresh.get(token_hash)
            if not record:
                return None
            if float(record.get("exp") or 0) < _now():
                self.refresh.pop(token_hash, None)
                return None
            return dict(record)

    def delete_refresh(self, token_hash: str) -> None:
        with self._lock:
            self.refresh.pop(token_hash, None)


class FirestoreOAuthStore:
    def __init__(self, project_id: str):
        from google.cloud import firestore

        self._db = firestore.Client(project=project_id)
        self._clients = self._db.collection("mcp_oauth_clients")
        self._codes = self._db.collection("mcp_oauth_codes")
        self._refresh = self._db.collection("mcp_oauth_refresh")

    def put_client(self, client_id: str, record: dict[str, Any]) -> None:
        self._clients.document(client_id).set(record)

    def get_client(self, client_id: str) -> Optional[dict[str, Any]]:
        snap = self._clients.document(client_id).get()
        return snap.to_dict() if snap.exists else None

    def put_code(self, code: str, record: dict[str, Any]) -> None:
        self._codes.document(code).set(record)

    def pop_code(self, code: str) -> Optional[dict[str, Any]]:
        ref = self._codes.document(code)
        snap = ref.get()
        if not snap.exists:
            return None
        ref.delete()
        return snap.to_dict()

    def put_refresh(self, token_hash: str, record: dict[str, Any]) -> None:
        self._refresh.document(token_hash).set(record)

    def get_refresh(self, token_hash: str) -> Optional[dict[str, Any]]:
        snap = self._refresh.document(token_hash).get()
        if not snap.exists:
            return None
        record = snap.to_dict() or {}
        if float(record.get("exp") or 0) < _now():
            snap.reference.delete()
            return None
        return record

    def delete_refresh(self, token_hash: str) -> None:
        self._refresh.document(token_hash).delete()


_store: Optional[Any] = None
_store_lock = threading.Lock()


def reset_oauth_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None


def get_oauth_store():
    global _store
    with _store_lock:
        if _store is not None:
            return _store

        storage_mode = (os.environ.get("CHAT_STORAGE_MODE") or "").strip().lower()
        project_id = (
            os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_PROJECT_ID") or ""
        ).strip()

        if storage_mode == "memory" or (storage_mode != "firestore" and not project_id):
            _store = MemoryOAuthStore()
        else:
            try:
                _store = FirestoreOAuthStore(project_id)
            except Exception:
                _store = MemoryOAuthStore()
        return _store
