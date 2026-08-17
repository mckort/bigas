"""Ephemeral X-post drafts (GCS in production, in-memory for tests)."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Protocol

logger = logging.getLogger(__name__)

DRAFT_PREFIX = "x_drafts/"
DEFAULT_TTL_HOURS = 48


class DraftStore(Protocol):
    def save(self, draft_id: str, payload: Dict[str, Any]) -> str: ...
    def load(self, draft_id: str) -> Optional[Dict[str, Any]]: ...
    def delete(self, draft_id: str) -> None: ...
    def cleanup_expired(
        self, *, ttl_hours: int = DEFAULT_TTL_HOURS, max_to_delete: int = 50
    ) -> int: ...


def draft_blob_name(draft_id: str) -> str:
    key = (draft_id or "").strip()
    if not key:
        raise ValueError("draft_id is required")
    return f"{DRAFT_PREFIX}{key}.json"


def parse_created_at(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_expired(
    payload: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> bool:
    created = parse_created_at(payload.get("created_at"))
    if created is None:
        return False
    current = now or datetime.now(timezone.utc)
    age_hours = (current - created).total_seconds() / 3600.0
    return age_hours > max(1, int(ttl_hours))


class InMemoryDraftStore:
    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}

    def save(self, draft_id: str, payload: Dict[str, Any]) -> str:
        key = (draft_id or "").strip()
        if not key:
            raise ValueError("draft_id is required")
        self._data[key] = payload
        return draft_blob_name(key)

    def load(self, draft_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get((draft_id or "").strip())

    def delete(self, draft_id: str) -> None:
        self._data.pop((draft_id or "").strip(), None)

    def cleanup_expired(
        self, *, ttl_hours: int = DEFAULT_TTL_HOURS, max_to_delete: int = 50
    ) -> int:
        deleted = 0
        now = datetime.now(timezone.utc)
        for key, payload in list(self._data.items()):
            if deleted >= max_to_delete:
                break
            if not isinstance(payload, dict):
                continue
            if is_expired(payload, now=now, ttl_hours=ttl_hours):
                self._data.pop(key, None)
                deleted += 1
        return deleted


class GcsDraftStore:
    """Object-only GCS access — does not call buckets.get / create."""

    def __init__(
        self,
        storage: Any = None,
        *,
        bucket_name: Optional[str] = None,
        bucket: Any = None,
    ) -> None:
        self._storage = storage
        self._bucket = bucket
        if self._storage is None and self._bucket is None:
            name = (bucket_name or os.environ.get("STORAGE_BUCKET_NAME") or "").strip()
            if not name:
                raise RuntimeError("STORAGE_BUCKET_NAME is required to store X drafts")
            from google.cloud import storage as gcs

            self._bucket = gcs.Client().bucket(name)

    def save(self, draft_id: str, payload: Dict[str, Any]) -> str:
        blob_name = draft_blob_name(draft_id)
        if self._storage is not None:
            return self._storage.store_json(blob_name, payload)
        blob = self._bucket.blob(blob_name)
        blob.upload_from_string(
            json.dumps(payload, indent=2),
            content_type="application/json",
        )
        return blob_name

    def load(self, draft_id: str) -> Optional[Dict[str, Any]]:
        blob_name = draft_blob_name(draft_id)
        if self._storage is not None:
            return self._storage.get_json(blob_name)
        blob = self._bucket.blob(blob_name)
        try:
            if not blob.exists():
                return None
            content = blob.download_as_text()
            return json.loads(content) if content else None
        except Exception:
            logger.warning("Failed to load X draft %s", blob_name, exc_info=True)
            return None

    def delete(self, draft_id: str) -> None:
        blob_name = draft_blob_name(draft_id)
        if self._storage is not None:
            deleter = getattr(self._storage, "delete_blob", None)
            if callable(deleter):
                deleter(blob_name)
                return
            blob = self._storage.bucket.blob(blob_name)
        else:
            blob = self._bucket.blob(blob_name)
        try:
            blob.delete()
        except Exception:
            logger.warning("Failed to delete X draft %s", blob_name, exc_info=True)

    def _list_draft_blobs(self):
        bucket = self._bucket
        if bucket is None and self._storage is not None:
            bucket = getattr(self._storage, "bucket", None)
        if bucket is None:
            return []
        return bucket.list_blobs(prefix=DRAFT_PREFIX)

    def cleanup_expired(
        self, *, ttl_hours: int = DEFAULT_TTL_HOURS, max_to_delete: int = 50
    ) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(ttl_hours)))
        deleted = 0
        try:
            blobs = list(self._list_draft_blobs())
        except Exception:
            logger.warning("Failed to list X drafts for cleanup", exc_info=True)
            return 0
        for blob in blobs:
            if deleted >= max_to_delete:
                break
            created = getattr(blob, "time_created", None) or getattr(blob, "updated", None)
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created > cutoff:
                continue
            try:
                blob.delete()
                deleted += 1
            except Exception:
                logger.warning(
                    "Failed to delete expired X draft %s",
                    getattr(blob, "name", ""),
                    exc_info=True,
                )
        return deleted
