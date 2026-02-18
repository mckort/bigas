"""
Meta Ads portfolio report: fetch campaign insights and optionally store in GCS.
"""

from __future__ import annotations

import os
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from bigas.resources.marketing.meta_ads_service import MetaAdsService
from bigas.resources.marketing.storage_service import StorageService

logger = logging.getLogger(__name__)


def _parse_date(value: Optional[str], default: Optional[date] = None) -> Optional[date]:
    if not value:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return default


def run_meta_campaign_portfolio(
    start_date_s: Optional[str],
    end_date_s: Optional[str],
    account_id: Optional[str] = None,
    store_raw: bool = False,
    store_enriched: bool = False,
    storage: Optional[StorageService] = None,
) -> Dict[str, Any]:
    """
    Run a Meta Ads campaign-level insights report and optionally store raw/enriched data in GCS.

    Returns a dict with:
      - status
      - request_metadata
      - summary
      - rows
      - optional storage section with blob paths
    """
    today = date.today()
    default_end = today
    default_start = today - timedelta(days=29)

    start_d = _parse_date(start_date_s, default=default_start)
    end_d = _parse_date(end_date_s, default=default_end)

    if not start_d or not end_d:
        raise ValueError("start_date and end_date must be valid YYYY-MM-DD strings")

    account_id = (account_id or os.environ.get("META_AD_ACCOUNT_ID") or "").replace("act_", "").strip()
    if not account_id:
        raise ValueError("account_id is required (or set META_AD_ACCOUNT_ID).")

    service = MetaAdsService()
    raw_rows = service.get_campaign_insights(account_id=account_id, start_date=start_d, end_date=end_d)
    norm = MetaAdsService.normalize_campaign_daily_rows(raw_rows)
    rows = norm["rows"]
    summary = dict(norm["summary"])
    if not summary.get("currency"):
        account_currency = service.get_account_currency(account_id)
        if account_currency:
            summary["currency"] = account_currency

    storage = storage or (StorageService() if (store_raw or store_enriched) else None)
    raw_blob = None
    enriched_blob = None

    if storage and (store_raw or store_enriched):
        report_date = end_d.isoformat()
        meta = {
            "account_id": account_id,
            "range_start": start_d.isoformat(),
            "range_end": end_d.isoformat(),
        }
        if store_raw:
            raw_blob = storage.store_raw_ads_report(
                platform="meta",
                report_data={"data": raw_rows},
                report_date=report_date,
                filename="campaign_insights.json",
                metadata=meta,
            )
        if store_enriched:
            enriched_payload = {
                "summary": summary,
                "rows": rows,
                "context": {
                    "account_id": account_id,
                    "start_date": start_d.isoformat(),
                    "end_date": end_d.isoformat(),
                },
            }
            enriched_blob = storage.store_json(
                blob_name=f"enriched_ads/meta/{report_date}/campaign_daily.json",
                data={"metadata": meta, "payload": {"enriched_response": enriched_payload}},
            )

    result: Dict[str, Any] = {
        "status": "success",
        "request_metadata": {
            "account_id": account_id,
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
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
