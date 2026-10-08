"""GCS storage for eval fixtures, run artifacts, and state."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def eval_bucket_name() -> str:
    return (
        (os.environ.get("EVAL_STORAGE_BUCKET") or "").strip()
        or (os.environ.get("STORAGE_BUCKET_NAME") or "").strip()
        or "bigas-analytics-reports"
    )


class EvalStorage:
    """Persist eval artifacts in Bigas GCS only."""

    def __init__(self, bucket_name: Optional[str] = None):
        from google.cloud import storage

        self.bucket_name = bucket_name or eval_bucket_name()
        self.client = storage.Client()
        self.bucket = self.client.bucket(self.bucket_name)

    def store_json(self, blob_name: str, data: Dict[str, Any]) -> str:
        return self.store_text(
            blob_name,
            json.dumps(data, indent=2, ensure_ascii=False),
            content_type="application/json",
        )

    def store_text(self, blob_name: str, text: str, *, content_type: str = "text/plain") -> str:
        blob = self.bucket.blob(blob_name)
        blob.upload_from_string(text, content_type=content_type)
        logger.info("Stored eval artifact at gs://%s/%s", self.bucket_name, blob_name)
        return blob_name

    def get_text(self, blob_name: str) -> Optional[str]:
        try:
            blob = self.bucket.blob(blob_name)
            if not blob.exists():
                return None
            return blob.download_as_text()
        except Exception as exc:
            logger.warning("Failed to load gs://%s/%s: %s", self.bucket_name, blob_name, exc)
            return None

    def get_json(self, blob_name: str) -> Optional[Dict[str, Any]]:
        try:
            blob = self.bucket.blob(blob_name)
            if not blob.exists():
                return None
            content = blob.download_as_text()
            return json.loads(content) if content else None
        except Exception as exc:
            logger.warning("Failed to load gs://%s/%s: %s", self.bucket_name, blob_name, exc)
            return None

    def gcs_uri(self, blob_name: str) -> str:
        return f"gs://{self.bucket_name}/{blob_name}"

    def list_eval_runs(self, use_case: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List eval run artifacts stored in GCS.

        Args:
            use_case: Optional use case filter. If None, lists all use cases.

        Returns:
            List of dicts with use_case, run_id, and blob metadata.
        """
        prefix = f"eval/{use_case}/" if use_case else "eval/"
        try:
            blobs = list(self.bucket.list_blobs(prefix=prefix))
            runs = []
            for blob in blobs:
                parts = blob.name.split("/")
                if len(parts) >= 3 and parts[0] == "eval":
                    runs.append({
                        "use_case": parts[1],
                        "run_id": parts[2] if len(parts) > 2 else "",
                        "blob_name": blob.name,
                        "size": blob.size,
                        "updated": blob.updated,
                    })
            return runs
        except Exception as exc:
            logger.warning("Failed to list eval runs: %s", exc)
            return []

    def delete_old_eval_reports(
        self, keep_days: int = 90, max_to_delete: int = 100
    ) -> int:
        """
        Delete eval report artifacts older than the specified number of days.

        Eval reports are stored under eval/{use_case}/{run_id}/ and can
        accumulate storage costs over time.

        Args:
            keep_days: Number of days to keep eval reports (default: 90)
            max_to_delete: Maximum number of blobs to delete in one operation (default: 100)

        Returns:
            int: Number of blobs deleted
        """
        try:
            cutoff = datetime.now() - timedelta(days=keep_days)
            blobs = list(self.bucket.list_blobs(prefix="eval/"))
            deleted_count = 0

            old_blobs = []
            for blob in blobs:
                updated = getattr(blob, "updated", None) or getattr(blob, "time_created", None)
                if updated is None:
                    continue
                if updated.replace(tzinfo=None) < cutoff:
                    old_blobs.append((blob, updated))

            old_blobs.sort(key=lambda x: x[1])
            blobs_to_delete = old_blobs[:max_to_delete]

            for blob, updated in blobs_to_delete:
                blob.delete()
                deleted_count += 1
                logger.info("Deleted old eval artifact: %s", blob.name)

            logger.info(
                "Deleted %d old eval artifacts (limited to %d)", deleted_count, max_to_delete
            )
            return deleted_count

        except Exception as exc:
            logger.error("Error deleting old eval reports: %s", exc)
            return 0
