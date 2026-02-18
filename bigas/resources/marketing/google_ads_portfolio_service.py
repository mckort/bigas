from __future__ import annotations

import os
import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from bigas.resources.marketing.google_ads_service import GoogleAdsService
from bigas.resources.marketing.storage_service import StorageService

logger = logging.getLogger(__name__)


def _parse_date(value: Optional[str], default: Optional[date] = None) -> Optional[date]:
    if not value:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return default


def run_google_ads_campaign_portfolio(
    start_date_s: Optional[str],
    end_date_s: Optional[str],
    customer_id: Optional[str],
    login_customer_id: Optional[str] = None,
    store_raw: bool = False,
    store_enriched: bool = False,
    storage: Optional[StorageService] = None,
) -> Dict[str, Any]:
    """
    Run a Google Ads campaign-level daily performance report and optionally store raw/enriched data in GCS.

    Returns a dict with:
      - status
      - request_metadata
      - summary
      - rows
      - optional storage section with blob paths
    """
    today = date.today()
    # Default: last 30 days ending today.
    default_end = today
    default_start = date.fromordinal(max(default_end.toordinal() - 29, 1))

    start_d = _parse_date(start_date_s, default=default_start)
    end_d = _parse_date(end_date_s, default=default_end)

    if not start_d or not end_d:
        raise ValueError("start_date and end_date must be valid YYYY-MM-DD strings")

    customer_id = (customer_id or os.environ.get("GOOGLE_ADS_CUSTOMER_ID") or "").replace("-", "").strip()
    if not customer_id:
        raise ValueError("customer_id is required (or set GOOGLE_ADS_CUSTOMER_ID).")

    login_customer_id = (login_customer_id or os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or "").strip() or None

    service = GoogleAdsService(login_customer_id=login_customer_id)
    query = GoogleAdsService.build_campaign_daily_performance_query(start_d, end_d)

    raw_rows, raw_chunks = service.search_stream(customer_id=customer_id, query=query)
    norm = GoogleAdsService.normalize_campaign_daily_rows(raw_rows)
    rows = norm["rows"]
    summary = norm["summary"]

    storage = storage or (StorageService() if (store_raw or store_enriched) else None)
    raw_blob = None
    enriched_blob = None

    if storage and (store_raw or store_enriched):
        report_date = end_d.isoformat()
        meta = {
            "customer_id": customer_id,
            "login_customer_id": login_customer_id,
            "range_start": start_d.isoformat(),
            "range_end": end_d.isoformat(),
            "query": query,
        }
        if store_raw:
            raw_blob = storage.store_raw_ads_report(
                platform="google_ads",
                report_data={"chunks": raw_chunks},
                report_date=report_date,
                filename="search_stream.json",
                metadata=meta,
            )
        if store_enriched:
            enriched_payload = {
                "summary": summary,
                "rows": rows,
                "context": {
                    "customer_id": customer_id,
                    "login_customer_id": login_customer_id,
                    "start_date": start_d.isoformat(),
                    "end_date": end_d.isoformat(),
                },
            }
            enriched_blob = storage.store_json(
                blob_name=f"enriched_ads/google_ads/{report_date}/campaign_daily.json",
                data={"metadata": meta, "payload": {"enriched_response": enriched_payload}},
            )

    result: Dict[str, Any] = {
        "status": "success",
        "request_metadata": {
            "customer_id": customer_id,
            "login_customer_id": login_customer_id,
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "query": query,
        },
        "summary": summary,
        "rows": rows,
    }
    if raw_blob or enriched_blob:
        result["storage"] = {
            "raw_blob": raw_blob,
            "enriched_blob": enriched_blob,
        }

    return result

