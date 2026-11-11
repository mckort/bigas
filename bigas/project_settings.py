"""Per-project settings changed from the app (no redeploy needed).

Today: the release mode set by the cockpit's "Go to production" button.

``development``
    Work merges to the production branch (main/master); DevOps deploys it.
``production``
    Versioned releases: work for version x.y.z lands on ``<prefix>-x.y.z``,
    "Prepare deploy" reviews the cut and merges it to the production branch.

Env ``PROJECT_BRANCH_MAPPING`` still works and wins for keys it lists, so
existing setups (VFA) behave as before. Stored in Firestore collection
``bigas_project_settings`` (memory store in local mode). Reads are cached for
``CACHE_SECONDS`` because branch resolution runs on every automation step.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

CACHE_SECONDS = 30
DEFAULT_PREFIX = "staging"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryProjectSettingsStore:
    def __init__(self) -> None:
        self._items: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def all(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._items.items()}

    def put(self, key: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            item = {**self._items.get(key, {}), **fields, "project_key": key, "updated_at": _now()}
            self._items[key] = item
            return dict(item)


class FirestoreProjectSettingsStore:
    def __init__(self, project_id: str) -> None:
        from google.cloud import firestore

        self._col = firestore.Client(project=project_id).collection("bigas_project_settings")

    def all(self) -> Dict[str, Dict[str, Any]]:
        return {doc.id: doc.to_dict() or {} for doc in self._col.stream() if doc.exists}

    def put(self, key: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        payload = {**fields, "project_key": key, "updated_at": _now()}
        self._col.document(key).set(payload, merge=True)
        snap = self._col.document(key).get()
        return snap.to_dict() or payload


_store = None
_store_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def get_store():
    global _store
    with _store_lock:
        if _store is not None:
            return _store
        mode = (os.environ.get("CHAT_STORAGE_MODE") or "").strip().lower()
        project_id = (os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_PROJECT_ID") or "").strip()
        if mode == "memory" or (mode != "firestore" and not project_id):
            _store = MemoryProjectSettingsStore()
        else:
            try:
                _store = FirestoreProjectSettingsStore(project_id)
            except Exception:
                _store = MemoryProjectSettingsStore()
        return _store


def reset_for_tests() -> None:
    global _store
    with _store_lock:
        _store = MemoryProjectSettingsStore()
    _cache.update(at=0.0, value=None)


def all_settings() -> Dict[str, Dict[str, Any]]:
    cached = _cache["value"]
    if cached is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return cached
    try:
        value = get_store().all()
    except Exception:
        value = cached or {}
    _cache.update(at=time.time(), value=value)
    return value


def get_settings(project_key: str) -> Dict[str, Any]:
    return dict(all_settings().get((project_key or "").strip().upper()) or {})


def set_release_mode(project_key: str, mode: str, *, prefix: str = DEFAULT_PREFIX, by: str = "") -> Dict[str, Any]:
    if mode not in ("development", "production"):
        raise ValueError("mode must be development or production")
    key = (project_key or "").strip().upper()
    item = get_store().put(key, {"release_mode": mode, "release_prefix": prefix or DEFAULT_PREFIX, "updated_by": by})
    _cache.update(at=0.0, value=None)
    return item


def release_branch_overrides() -> Dict[str, str]:
    """``{KEY: prefix}`` for projects switched to production in the app."""
    out: Dict[str, str] = {}
    for key, item in all_settings().items():
        if (item or {}).get("release_mode") == "production":
            out[key.upper()] = (item.get("release_prefix") or DEFAULT_PREFIX).strip()
    return out


def development_keys() -> set:
    """Projects switched back to development in the app."""
    return {k.upper() for k, v in all_settings().items() if (v or {}).get("release_mode") == "development"}
