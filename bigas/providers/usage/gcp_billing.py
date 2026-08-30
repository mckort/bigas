"""GCP invoice costs from Cloud Billing export to BigQuery.

One event per project per service in the window. Gemini on the invoice is
``gcp.gemini_invoice`` so it is not mixed with ``llm_logs`` list-price.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from bigas.providers.usage.base import UsageEvent, UsageProvider, usage_provider_enabled
from bigas.providers.usage.llm_logs import _project_id, _usage_project_ids

logger = logging.getLogger(__name__)

_BQ_QUERIES_URL = "https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
_BQ_DATASETS_URL = (
    "https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets"
)
_BQ_TABLES_URL = (
    "https://bigquery.googleapis.com/bigquery/v2/projects/{project}"
    "/datasets/{dataset}/tables"
)
_EXPORT_TABLE_PREFIXES = (
    "gcp_billing_export_v1_",
    "gcp_billing_export_resource_v1_",
)

_SERVICE_FEATURES: Tuple[Tuple[str, str], ...] = (
    ("cloud firestore", "gcp.firestore"),
    ("firebase firestore", "gcp.firestore"),
    ("cloud datastore", "gcp.firestore"),
    ("cloud run", "gcp.cloud_run"),
    ("cloud logging", "gcp.logging"),
    ("stackdriver logging", "gcp.logging"),
    ("artifact registry", "gcp.artifact_registry"),
    ("cloud storage", "gcp.storage"),
    ("secret manager", "gcp.secret_manager"),
    ("cloud scheduler", "gcp.cloud_scheduler"),
    ("cloud build", "gcp.cloud_build"),
    ("cloud pub/sub", "gcp.pubsub"),
    ("vertex ai", "gcp.gemini_invoice"),
    ("generative language", "gcp.gemini_invoice"),
    ("gemini api", "gcp.gemini_invoice"),
    ("app engine", "gcp.app_engine"),
    ("cloud functions", "gcp.functions"),
    ("compute engine", "gcp.compute"),
    ("cloud monitoring", "gcp.monitoring"),
    ("bigquery", "gcp.bigquery"),
    ("networking", "gcp.networking"),
)


def _dataset_id() -> str:
    return (os.environ.get("BIGAS_GCP_BILLING_DATASET") or "gcp_billing").strip()


def _billing_account_table_suffix() -> Optional[str]:
    raw = (os.environ.get("BIGAS_BILLING_ACCOUNT") or "").strip()
    if not raw:
        return None
    return raw.replace("-", "_")


def service_feature(description: str) -> str:
    """Map a Cloud Billing service description to a stable feature id."""
    text = (description or "").strip()
    low = text.lower()
    for needle, feature in _SERVICE_FEATURES:
        if needle in low:
            return feature
    slug = re.sub(r"[^a-z0-9]+", "_", low).strip("_")[:48] or "other"
    return f"gcp.{slug}"


def _feature_matches(feature: str, prefix: Optional[str]) -> bool:
    if not prefix:
        return True
    p = prefix.strip()
    if not p:
        return True
    return feature.startswith(p) or feature == p.rstrip("_")


def _rows_to_events(
    rows: Sequence[Dict[str, Any]],
    *,
    started_at: str,
) -> List[UsageEvent]:
    events: List[UsageEvent] = []
    for row in rows:
        project = str(row.get("project_id") or "").strip() or "unknown"
        service = str(row.get("service") or "").strip() or "Unknown"
        try:
            cost = float(row.get("net_cost") if row.get("net_cost") is not None else 0)
        except (TypeError, ValueError):
            continue
        if abs(cost) < 1e-9:
            continue
        feature = service_feature(service)
        events.append(
            UsageEvent(
                provider="gcp_billing",
                source_id=f"gcp:{project}:{feature}",
                started_at=started_at,
                feature=feature,
                est_cost_usd=round(cost, 6),
                cost_estimate=False,
                meta={
                    "app": "vcfieldassistant" if project == "vcfieldassistant" else "bigas",
                    "gcp_project": project,
                    "gcp_service": service,
                    "cost_kind": "invoice",
                },
            )
        )
    return events


class GcpBillingUsageProvider(UsageProvider):
    name = "gcp_billing"
    display_name = "GCP Cloud Billing"

    @classmethod
    def is_configured(cls) -> bool:
        return usage_provider_enabled("gcp_billing") and bool(_project_id())

    def __init__(self) -> None:
        self._project = _project_id()
        if not self._project:
            raise ValueError("GOOGLE_CLOUD_PROJECT is required for GcpBillingUsageProvider")

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

        table = self._resolve_table()
        projects = _usage_project_ids(self._project)
        sql = (
            f"SELECT project.id AS project_id, service.description AS service, "
            f"SUM(cost) + SUM((SELECT IFNULL(SUM(c.amount), 0) FROM UNNEST(credits) AS c)) "
            f"AS net_cost "
            f"FROM `{table}` "
            f"WHERE DATE(usage_start_time) >= @start_date "
            f"AND DATE(usage_start_time) <= @end_date "
            f"AND project.id IN UNNEST(@projects) "
            f"GROUP BY 1, 2"
        )
        params = [
            {
                "name": "start_date",
                "parameterType": {"type": "DATE"},
                "parameterValue": {"value": start.date().isoformat()},
            },
            {
                "name": "end_date",
                "parameterType": {"type": "DATE"},
                "parameterValue": {"value": end.date().isoformat()},
            },
            {
                "name": "projects",
                "parameterType": {
                    "type": "ARRAY",
                    "arrayType": {"type": "STRING"},
                },
                "parameterValue": {
                    "arrayValues": [{"value": p} for p in projects]
                },
            },
        ]
        payload = self._run_query(sql, params)
        rows = list(_parse_query_rows(payload))
        events = _rows_to_events(rows, started_at=start.isoformat())
        return [e for e in events if _feature_matches(e.feature, feature_prefix)]

    def _resolve_table(self) -> str:
        explicit = (os.environ.get("BIGAS_GCP_BILLING_TABLE") or "").strip()
        if explicit:
            return explicit.replace(":", ".")
        dataset = _dataset_id()
        suffix = _billing_account_table_suffix()
        found = self._find_export_table(dataset, suffix=suffix)
        if found:
            return found
        # Export may have been pointed at another dataset in the same project.
        for other in self._list_dataset_ids():
            if other == dataset:
                continue
            found = self._find_export_table(other, suffix=suffix)
            if found:
                logger.info(
                    "Using Cloud Billing export table %s (preferred dataset %s empty)",
                    found,
                    dataset,
                )
                return found
        raise RuntimeError(
            f"Cloud Billing export table not found in {self._project}.{dataset}. "
            "Enable Standard usage cost export to that dataset in Cloud Console "
            "(Billing → Billing export). First rows can take a few hours; EU "
            "datasets backfill the current and previous month."
        )

    def _find_export_table(
        self, dataset: str, *, suffix: Optional[str]
    ) -> Optional[str]:
        try:
            names = self._list_table_ids(dataset)
        except Exception as e:
            logger.info(
                "Could not list BigQuery dataset %s.%s: %s",
                self._project,
                dataset,
                e,
            )
            return None
        if suffix:
            preferred = tuple(f"{prefix}{suffix}" for prefix in _EXPORT_TABLE_PREFIXES)
            for name in preferred:
                if name in names:
                    return f"{self._project}.{dataset}.{name}"
        for name in names:
            if any(name.startswith(prefix) for prefix in _EXPORT_TABLE_PREFIXES):
                return f"{self._project}.{dataset}.{name}"
        return None

    def _list_dataset_ids(self) -> List[str]:
        url = _BQ_DATASETS_URL.format(project=self._project)
        try:
            payload = self._get_json(url)
        except Exception as e:
            logger.info("Could not list BigQuery datasets in %s: %s", self._project, e)
            return []
        ids: List[str] = []
        for ds in payload.get("datasets") or []:
            ref = (ds.get("datasetReference") or {}).get("datasetId")
            if ref:
                ids.append(str(ref))
        return ids

    def _list_table_ids(self, dataset: str) -> List[str]:
        url = _BQ_TABLES_URL.format(project=self._project, dataset=dataset)
        try:
            payload = self._get_json(url)
        except Exception as e:
            raise RuntimeError(
                f"Could not list BigQuery dataset {self._project}.{dataset}: {e}"
            ) from e
        ids: List[str] = []
        for table in payload.get("tables") or []:
            ref = (table.get("tableReference") or {}).get("tableId")
            if ref:
                ids.append(str(ref))
        return ids

    def _run_query(self, sql: str, params: List[Dict[str, Any]]) -> Dict[str, Any]:
        url = _BQ_QUERIES_URL.format(project=self._project)
        body = {
            "query": sql,
            "useLegacySql": False,
            "timeoutMs": 60000,
            "parameterMode": "NAMED",
            "queryParameters": params,
        }
        token = self._access_token()
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=90,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"BigQuery jobs.query error {resp.status_code}: {resp.text[:400]}"
            )
        data = resp.json() if resp.text else {}
        if not isinstance(data, dict):
            raise RuntimeError("BigQuery returned unexpected payload")
        if data.get("jobComplete") is False:
            raise RuntimeError("BigQuery jobs.query timed out")
        return data

    def _get_json(self, url: str) -> Dict[str, Any]:
        token = self._access_token()
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"BigQuery API error {resp.status_code}: {resp.text[:400]}")
        data = resp.json() if resp.text else {}
        if not isinstance(data, dict):
            raise RuntimeError("BigQuery API returned unexpected payload")
        return data

    def _access_token(self) -> str:
        try:
            import google.auth
            import google.auth.transport.requests
        except ImportError as e:
            raise RuntimeError("google-auth is required to query BigQuery") from e
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/bigquery"]
        )
        creds.refresh(google.auth.transport.requests.Request())
        if not creds.token:
            raise RuntimeError("Failed to obtain Google access token for BigQuery")
        return creds.token


def _parse_query_rows(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    schema = ((payload.get("schema") or {}).get("fields")) or []
    names = [str(f.get("name") or "") for f in schema if isinstance(f, dict)]
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        cells = row.get("f") or []
        out: Dict[str, Any] = {}
        for i, name in enumerate(names):
            cell = cells[i] if i < len(cells) and isinstance(cells[i], dict) else {}
            out[name] = cell.get("v")
        yield out
