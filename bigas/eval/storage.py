"""GCS storage for eval fixtures, run artifacts, and state."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

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
        blob = self.bucket.blob(blob_name)
        blob.upload_from_string(
            json.dumps(data, indent=2, ensure_ascii=False),
            content_type="application/json",
        )
        logger.info("Stored eval artifact at gs://%s/%s", self.bucket_name, blob_name)
        return blob_name

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
