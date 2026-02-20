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
    report_level: str = "campaign",
    breakdowns: Optional[list[str]] = None,
    store_raw: bool = False,
    store_enriched: bool = False,
    storage: Optional[StorageService] = None,
) -> Dict[str, Any]:
    """
    Run a Google Ads performance report and optionally store raw/enriched data in GCS.

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
    report_level = (report_level or "campaign").strip().lower()
    if report_level not in {"campaign", "ad", "audience_breakdown"}:
        raise ValueError("report_level must be one of: campaign, ad, audience_breakdown")
    clean_breakdowns = [str(b).strip() for b in (breakdowns or []) if str(b).strip()]
    if report_level == "audience_breakdown" and not clean_breakdowns:
        clean_breakdowns = ["device"]

    service = GoogleAdsService(login_customer_id=login_customer_id)
    query = GoogleAdsService.build_performance_query(
        start=start_d,
        end=end_d,
        report_level=report_level,
        breakdowns=clean_breakdowns,
        include_optional_reach_metrics=True,
    )
    fallback_query = GoogleAdsService.build_performance_query(
        start=start_d,
        end=end_d,
        report_level=report_level,
        breakdowns=clean_breakdowns,
        include_optional_reach_metrics=False,
    )

    try:
        raw_rows, raw_chunks = service.search_stream(customer_id=customer_id, query=query)
        used_query = query
    except Exception:
        logger.warning("Google Ads primary query failed, retrying fallback without optional reach metrics")
        raw_rows, raw_chunks = service.search_stream(customer_id=customer_id, query=fallback_query)
        used_query = fallback_query

    norm = GoogleAdsService.normalize_campaign_daily_rows(raw_rows, report_level=report_level, breakdowns=clean_breakdowns)
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
            "report_level": report_level,
            "breakdowns": clean_breakdowns,
            "range_start": start_d.isoformat(),
            "range_end": end_d.isoformat(),
            "query": used_query,
        }
        if store_raw:
            raw_blob = storage.store_raw_ads_report(
                platform="google_ads",
                report_data={"chunks": raw_chunks},
                report_date=report_date,
                filename=f"{report_level}_search_stream.json",
                metadata=meta,
            )
        if store_enriched:
            enriched_payload = {
                "summary": summary,
                "rows": rows,
                "context": {
                    "customer_id": customer_id,
                    "login_customer_id": login_customer_id,
                    "report_level": report_level,
                    "breakdowns": clean_breakdowns,
                    "start_date": start_d.isoformat(),
                    "end_date": end_d.isoformat(),
                },
            }
            enriched_blob = storage.store_json(
                blob_name=f"enriched_ads/google_ads/{report_date}/{report_level}_daily.json",
                data={"metadata": meta, "payload": {"enriched_response": enriched_payload}},
            )

    result: Dict[str, Any] = {
        "status": "success",
        "request_metadata": {
            "customer_id": customer_id,
            "login_customer_id": login_customer_id,
            "report_level": report_level,
            "breakdowns": clean_breakdowns,
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "query": used_query,
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

