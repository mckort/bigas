"""Persistence for project releases (Jira-like Fix Versions on the internal board)."""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bigas.tickets.semver import SemverError, normalize_version_name, parse_semver


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sort_releases(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(item: Dict[str, Any]) -> tuple:
        try:
            parsed = parse_semver(str(item.get("name") or "0.0.0"))
        except SemverError:
            parsed = (0, 0, 0)
        released = 1 if item.get("released") else 0
        return (released, parsed)

    return sorted(items, key=key)


def _compose_release(
    *,
    release_id: str,
    project_key: str,
    name: str,
    released: bool = False,
    released_at: Optional[str] = None,
    git_sha: Optional[str] = None,
    git_tag: Optional[str] = None,
    is_default: bool = False,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    stamp = now or _utcnow_iso()
    return {
        "release_id": release_id,
        "project_key": project_key,
        "name": name,
        "released": bool(released),
        "released_at": (released_at or "").strip() or None,
        "git_sha": (git_sha or "").strip() or None,
        "git_tag": (git_tag or "").strip() or None,
        "is_default": bool(is_default),
        "created_at": stamp,
        "updated_at": stamp,
    }


class MemoryReleaseStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._releases: Dict[str, Dict[str, Any]] = {}

    def list_releases(self, project_key: str) -> List[Dict[str, Any]]:
        proj = (project_key or "").strip().upper()
        with self._lock:
            items = [
                dict(item)
                for item in self._releases.values()
                if item.get("project_key") == proj
            ]
        return _sort_releases(items)

    def get_release(self, release_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._releases.get(release_id)
            return dict(item) if item else None

    def get_release_by_name(self, project_key: str, name: str) -> Optional[Dict[str, Any]]:
        try:
            normalized = normalize_version_name(name)
        except SemverError:
            return None
        proj = (project_key or "").strip().upper()
        with self._lock:
            for item in self._releases.values():
                if item.get("project_key") == proj and item.get("name") == normalized:
                    return dict(item)
        return None

    def get_default_release(self, project_key: str) -> Optional[Dict[str, Any]]:
        for item in self.list_releases(project_key):
            if item.get("is_default") and not item.get("released"):
                return item
        return None

    def create_release(
        self,
        project_key: str,
        *,
        name: str,
        is_default: bool = False,
    ) -> Dict[str, Any]:
        proj = (project_key or "").strip().upper()
        if not proj:
            raise ValueError("project_key is required")
        normalized = normalize_version_name(name)
        if self.get_release_by_name(proj, normalized):
            raise ValueError(f"Release {normalized} already exists for {proj}")
        release_id = str(uuid.uuid4())
        item = _compose_release(
            release_id=release_id,
            project_key=proj,
            name=normalized,
            is_default=bool(is_default),
        )
        with self._lock:
            if is_default:
                self._clear_default_locked(proj)
            self._releases[release_id] = item
        return dict(item)

    def _clear_default_locked(self, project_key: str) -> None:
        for item in self._releases.values():
            if item.get("project_key") == project_key and item.get("is_default"):
                item["is_default"] = False
                item["updated_at"] = _utcnow_iso()

    def update_release(self, release_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._releases.get(release_id)
            if not item:
                return None
            allowed = {
                "released",
                "released_at",
                "git_sha",
                "git_tag",
                "is_default",
            }
            if fields.get("is_default"):
                self._clear_default_locked(item["project_key"])
            for key, value in fields.items():
                if key not in allowed:
                    continue
                item[key] = value
            item["updated_at"] = _utcnow_iso()
            return dict(item)

    def delete_release(self, release_id: str) -> bool:
        with self._lock:
            if release_id not in self._releases:
                return False
            del self._releases[release_id]
            return True


class FirestoreReleaseStore:
    def __init__(self, project_id: str) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=project_id)
        self._col = self._db.collection("ticket_releases")

    def list_releases(self, project_key: str) -> List[Dict[str, Any]]:
        proj = (project_key or "").strip().upper()
        docs = self._col.where("project_key", "==", proj).stream()
        items = [doc.to_dict() for doc in docs if doc.exists]
        return _sort_releases(items)

    def get_release(self, release_id: str) -> Optional[Dict[str, Any]]:
        snap = self._col.document(release_id).get()
        return snap.to_dict() if snap.exists else None

    def get_release_by_name(self, project_key: str, name: str) -> Optional[Dict[str, Any]]:
        try:
            normalized = normalize_version_name(name)
        except SemverError:
            return None
        proj = (project_key or "").strip().upper()
        docs = list(
            self._col.where("project_key", "==", proj)
            .where("name", "==", normalized)
            .limit(1)
            .stream()
        )
        if not docs:
            return None
        return docs[0].to_dict()

    def get_default_release(self, project_key: str) -> Optional[Dict[str, Any]]:
        for item in self.list_releases(project_key):
            if item.get("is_default") and not item.get("released"):
                return item
        return None

    def create_release(
        self,
        project_key: str,
        *,
        name: str,
        is_default: bool = False,
    ) -> Dict[str, Any]:
        proj = (project_key or "").strip().upper()
        if not proj:
            raise ValueError("project_key is required")
        normalized = normalize_version_name(name)
        if self.get_release_by_name(proj, normalized):
            raise ValueError(f"Release {normalized} already exists for {proj}")
        release_id = str(uuid.uuid4())
        item = _compose_release(
            release_id=release_id,
            project_key=proj,
            name=normalized,
            is_default=bool(is_default),
        )
        if is_default:
            self._clear_default(proj)
        self._col.document(release_id).set(item)
        return item

    def _clear_default(self, project_key: str) -> None:
        for item in self.list_releases(project_key):
            if item.get("is_default"):
                self._col.document(item["release_id"]).update(
                    {"is_default": False, "updated_at": _utcnow_iso()}
                )

    def update_release(self, release_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        ref = self._col.document(release_id)
        snap = ref.get()
        if not snap.exists:
            return None
        item = snap.to_dict() or {}
        allowed = {
            "released",
            "released_at",
            "git_sha",
            "git_tag",
            "is_default",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if updates.get("is_default"):
            self._clear_default(item.get("project_key") or "")
        if not updates:
            return item
        updates["updated_at"] = _utcnow_iso()
        ref.update(updates)
        item.update(updates)
        return item

    def delete_release(self, release_id: str) -> bool:
        ref = self._col.document(release_id)
        if not ref.get().exists:
            return False
        ref.delete()
        return True


_store: Optional[Any] = None
_store_lock = threading.Lock()


def get_release_store():
    global _store
    with _store_lock:
        if _store is not None:
            return _store
        storage_mode = (os.environ.get("CHAT_STORAGE_MODE") or "").strip().lower()
        project_id = (
            os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_PROJECT_ID") or ""
        ).strip()
        if storage_mode == "memory" or (storage_mode != "firestore" and not project_id):
            _store = MemoryReleaseStore()
        else:
            try:
                _store = FirestoreReleaseStore(project_id)
            except Exception:
                _store = MemoryReleaseStore()
        return _store


def reset_release_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = MemoryReleaseStore()
