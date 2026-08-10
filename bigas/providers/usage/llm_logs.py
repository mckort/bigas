"""
LLM usage from Cloud Run / Cloud Logging ``llm_usage`` JSON lines.

Covers Gemini, OpenAI, and future Claude calls that go through ``bigas.llm``
and emit ``event: llm_usage`` structured logs.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from bigas.providers.usage.base import UsageEvent, UsageProvider

logger = logging.getLogger(__name__)

_LOGGING_ENTRIES_URL = "https://logging.googleapis.com/v2/entries:list"


def _project_id() -> str:
    return (
        (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
        or (os.environ.get("GOOGLE_PROJECT_ID") or "").strip()
        or (os.environ.get("GCP_PROJECT") or "").strip()
    )


def _parse_log_payload(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    jp = entry.get("jsonPayload")
    if isinstance(jp, dict) and jp.get("event") == "llm_usage":
        return jp

    # logger.info(json.dumps(...)) often lands in textPayload as a JSON string.
    tp = entry.get("textPayload")
    if isinstance(tp, str) and tp.strip():
        text = tp.strip()
        # Prefer a full-line JSON object.
        if text.startswith("{") and '"llm_usage"' in text:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("event") == "llm_usage":
                    return data
            except json.JSONDecodeError:
                pass
        # Sometimes the JSON is embedded after a log prefix.
        # Use re.DOTALL so .* matches newlines in multi-line JSON payloads.
        m = re.search(r"\{.*\"event\"\s*:\s*\"llm_usage\".*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict) and data.get("event") == "llm_usage":
                    return data
            except json.JSONDecodeError:
                pass
    return None


def _feature_matches(feature: str, prefix: Optional[str]) -> bool:
    if not prefix:
        return True
    p = prefix.strip()
    if not p:
        return True
    return feature.startswith(p) or feature == p.rstrip("_")


class CloudRunLlmUsageProvider(UsageProvider):
    name = "llm_logs"
    display_name = "LLM Cloud Logging"

    @classmethod
    def is_configured(cls) -> bool:
        return bool(_project_id())

    def __init__(self) -> None:
        self._project = _project_id()
        if not self._project:
            raise ValueError(
                "GOOGLE_CLOUD_PROJECT (or GOOGLE_PROJECT_ID) is required "
                "for CloudRunLlmUsageProvider"
            )

    def fetch_usage(
        self,
        *,
        start: datetime,
        end: datetime,
        feature_prefix: Optional[str] = None,
    ) -> List[UsageEvent]:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)

        extra = (os.environ.get("BIGAS_CTO_USAGE_LOG_FILTER") or "").strip()
        # Match both structured jsonPayload and textPayload JSON lines.
        filter_parts = [
            f'timestamp>="{start.isoformat().replace("+00:00", "Z")}"',
            f'timestamp<="{end.isoformat().replace("+00:00", "Z")}"',
            '(jsonPayload.event="llm_usage" OR textPayload:"llm_usage")',
        ]
        if extra:
            filter_parts.append(f"({extra})")
        filter_str = " AND ".join(filter_parts)

        events: List[UsageEvent] = []
        page_token: Optional[str] = None
        pages = 0
        max_pages = 20

        while pages < max_pages:
            pages += 1
            body: Dict[str, Any] = {
                "resourceNames": [f"projects/{self._project}"],
                "filter": filter_str,
                "orderBy": "timestamp desc",
                "pageSize": 1000,
            }
            if page_token:
                body["pageToken"] = page_token

            try:
                payload = self._list_entries(body)
            except Exception as e:
                logger.warning("Cloud Logging entries:list failed: %s", e)
                break

            for entry in payload.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                data = _parse_log_payload(entry)
                if not data:
                    continue
                feature = str(data.get("feature") or "").strip() or "llm"
                if not _feature_matches(feature, feature_prefix):
                    continue
                # Prefer review totals over per-attempt rows when present.
                attempt = data.get("attempt")
                if attempt not in (None, "total") and str(attempt).isdigit():
                    # Skip per-attempt lines; keep totals (and single-attempt logs
                    # that never emit a separate total — those have no attempt key
                    # or attempt==0 only when it's the only line).
                    # Keep attempt 0 only if there is no "total" companion — hard
                    # without grouping; skip numeric attempts and keep totals +
                    # logs without attempt.
                    continue

                ts = (entry.get("timestamp") or start.isoformat()).strip()
                source_id = (
                    (entry.get("insertId") or "").strip()
                    or f"{feature}:{ts}:{data.get('model')}"
                )
                try:
                    prompt = int(data["prompt_tokens"]) if data.get("prompt_tokens") is not None else None
                except (TypeError, ValueError):
                    prompt = None
                try:
                    candidates = (
                        int(data["candidates_tokens"])
                        if data.get("candidates_tokens") is not None
                        else None
                    )
                except (TypeError, ValueError):
                    candidates = None
                try:
                    total = int(data["total_tokens"]) if data.get("total_tokens") is not None else None
                except (TypeError, ValueError):
                    total = None
                est = data.get("est_cost_usd")
                try:
                    est_f = float(est) if est is not None else None
                except (TypeError, ValueError):
                    est_f = None

                events.append(
                    UsageEvent(
                        provider=self.name,
                        source_id=source_id,
                        started_at=ts,
                        feature=feature,
                        model=(str(data.get("model")).strip() if data.get("model") else None),
                        input_tokens=prompt,
                        output_tokens=candidates,
                        total_tokens=total,
                        est_cost_usd=est_f,
                        cost_estimate=bool(data.get("cost_estimate")) or est_f is not None,
                        meta={
                            "phase": data.get("phase"),
                            "attempts": data.get("attempts"),
                            "attempt": data.get("attempt"),
                        },
                    )
                )

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        return events

    def _list_entries(self, body: Dict[str, Any]) -> Dict[str, Any]:
        token = self._access_token()
        resp = requests.post(
            _LOGGING_ENTRIES_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Cloud Logging API error {resp.status_code}: {resp.text[:400]}"
            )
        data = resp.json() if resp.text else {}
        if not isinstance(data, dict):
            raise RuntimeError("Cloud Logging API returned unexpected payload")
        return data

    def _access_token(self) -> str:
        try:
            import google.auth
            import google.auth.transport.requests
        except ImportError as e:
            raise RuntimeError(
                "google-auth is required to query Cloud Logging"
            ) from e

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/logging.read"]
        )
        creds.refresh(google.auth.transport.requests.Request())
        if not creds.token:
            raise RuntimeError("Failed to obtain Google access token for Logging")
        return creds.token
