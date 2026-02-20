"""
Meta (Facebook/Instagram) Marketing API client for campaign-level insights.

Uses a long-lived system user access token (ads_management, ads_read).
Account ID is the numeric ad account ID (act_ prefix added in requests).
"""

from __future__ import annotations

import json
import os
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

META_GRAPH_API_VERSION = "v21.0"
META_GRAPH_BASE = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}"


class MetaAdsApiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


def _safe_float(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        if isinstance(val, (int, float)):
            return float(val)
        return float(str(val).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _safe_int(val: Any) -> int:
    if val is None:
        return 0
    try:
        if isinstance(val, int):
            return val
        return int(float(str(val).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _extract_conversions_from_actions(actions: Any) -> tuple[float, float]:
    """Sum conversion-like action values from Meta actions array. Returns (count, value)."""
    total_count = 0.0
    total_value = 0.0
    if not isinstance(actions, list):
        return total_count, total_value
    # Common conversion action types
    conversion_types = {"purchase", "lead", "omni_purchase", "complete_registration", "offsite_conversion.fb_pixel"}
    for item in actions:
        if not isinstance(item, dict):
            continue
        atype = (item.get("action_type") or "").strip().lower()
        if not atype or atype not in conversion_types:
            continue
        total_count += _safe_float(item.get("value"))
        total_value += _safe_float(item.get("value"))  # same for count; action_values has value
    return total_count, total_value


def _extract_value_from_action_values(action_values: Any) -> float:
    """Sum value from action_values (e.g. purchase value)."""
    total = 0.0
    if not isinstance(action_values, list):
        return total
    for item in action_values:
        if not isinstance(item, dict):
            continue
        total += _safe_float(item.get("value"))
    return total


class MetaAdsService:
    """
    Minimal Meta Marketing API client for campaign insights.
    """

    def __init__(self, access_token: Optional[str] = None):
        token = (access_token or os.environ.get("META_ACCESS_TOKEN") or "").strip()
        if not token:
            raise ValueError("META_ACCESS_TOKEN is required for Meta Ads API calls.")
        self.access_token = token

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{META_GRAPH_BASE}{path}"
        p = dict(params or {})
        p["access_token"] = self.access_token
        resp = requests.get(url, params=p, timeout=60)
        if resp.status_code >= 400:
            logger.error(
                "Meta Ads API error: status=%s path=%s body=%s",
                resp.status_code,
                path,
                (resp.text or "")[:2000],
            )
            raise MetaAdsApiError(
                f"Meta Ads API error ({resp.status_code})",
                status_code=resp.status_code,
                response_text=resp.text,
            )
        return resp.json() if resp.text else {}

    def get_account_currency(self, account_id: str) -> Optional[str]:
        """
        Fetch ad account currency (e.g. SEK, EUR) from Graph API.
        account_id: numeric, no act_ prefix.
        """
        aid = (account_id or "").replace("act_", "").strip()
        if not aid:
            return None
        data = self._get(f"/act_{aid}", params={"fields": "currency"})
        return (data.get("currency") or "").strip().upper() or None

    def get_campaign_insights(
        self,
        account_id: str,
        start_date: date,
        end_date: date,
        level: str = "campaign",
        breakdowns: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
        time_increment: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Fetch insights for the account and date range.
        account_id: numeric, no act_ prefix (we add it).
        level: campaign | adset | ad
        breakdowns: optional Meta breakdown dimensions (e.g. age,gender,country)
        Returns list of insight objects (typically one row per entity/day/segment).
        """
        aid = (account_id or "").replace("act_", "").strip()
        if not aid:
            raise ValueError("account_id is required for Meta campaign insights.")
        path = f"/act_{aid}/insights"
        time_range = json.dumps({"since": start_date.isoformat(), "until": end_date.isoformat()})
        level = (level or "campaign").strip().lower()
        if level not in {"campaign", "adset", "ad"}:
            raise ValueError("level must be one of: campaign, adset, ad")
        fields = fields or [
            "campaign_id",
            "campaign_name",
            "adset_id",
            "adset_name",
            "ad_id",
            "ad_name",
            "date_start",
            "date_stop",
            "impressions",
            "clicks",
            "reach",
            "frequency",
            "spend",
            "actions",
            "action_values",
        ]
        all_rows: List[Dict[str, Any]] = []
        params = {
            "time_range": time_range,
            "time_increment": time_increment,
            "level": level,
            "fields": ",".join(fields),
            "limit": 500,
        }
        if breakdowns:
            clean_breakdowns = [str(b).strip() for b in breakdowns if str(b).strip()]
            if clean_breakdowns:
                params["breakdowns"] = ",".join(clean_breakdowns)
        while True:
            data = self._get(path, params)
            for item in data.get("data") or []:
                all_rows.append(item)
            paging = data.get("paging") or {}
            next_url = paging.get("next")
            if not next_url:
                break
            # next is full URL; we could parse and call _get with same base path + cursor, but simpler: follow next
            resp = requests.get(next_url, timeout=60)
            if resp.status_code >= 400:
                raise MetaAdsApiError(
                    f"Meta Ads API paging error ({resp.status_code})",
                    status_code=resp.status_code,
                    response_text=resp.text,
                )
            data = resp.json() if resp.text else {}
            if not data.get("data"):
                break
            for item in data.get("data") or []:
                all_rows.append(item)
            next_url = (data.get("paging") or {}).get("next")
            if not next_url:
                break
        return all_rows

    def get_ad_insights(
        self,
        account_id: str,
        start_date: date,
        end_date: date,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch ad-level insights for creative performance."""
        return self.get_campaign_insights(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            level="ad",
            fields=fields,
        )

    def get_audience_breakdown_insights(
        self,
        account_id: str,
        start_date: date,
        end_date: date,
        breakdowns: List[str],
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch campaign-level insights broken down by audience dimensions."""
        return self.get_campaign_insights(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            level="campaign",
            breakdowns=breakdowns,
            fields=fields,
        )

    def get_adsets_targeting(self, account_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Fetch ad set targeting configuration for additional audience context.
        """
        aid = (account_id or "").replace("act_", "").strip()
        if not aid:
            return []
        path = f"/act_{aid}/adsets"
        params = {
            "fields": "id,name,campaign_id,targeting,optimization_goal,billing_event,status",
            "limit": max(1, min(int(limit or 100), 500)),
        }
        rows: List[Dict[str, Any]] = []
        while True:
            data = self._get(path, params)
            rows.extend([item for item in (data.get("data") or []) if isinstance(item, dict)])
            paging = data.get("paging") or {}
            next_url = paging.get("next")
            if not next_url:
                break
            resp = requests.get(next_url, timeout=60)
            if resp.status_code >= 400:
                raise MetaAdsApiError(
                    f"Meta Ads API adsets paging error ({resp.status_code})",
                    status_code=resp.status_code,
                    response_text=resp.text,
                )
            data = resp.json() if resp.text else {}
            if not data.get("data"):
                break
            rows.extend([item for item in (data.get("data") or []) if isinstance(item, dict)])
            if not (data.get("paging") or {}).get("next"):
                break
        return rows

    @staticmethod
    def _flatten_insight_row(
        row: Dict[str, Any],
        level: str = "campaign",
        breakdowns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Normalize one Meta insight row to our schema (entity metadata + metrics + derived)."""
        if not isinstance(row, dict):
            return {}
        level = (level or "campaign").strip().lower()
        date_start = row.get("date_start") or row.get("date_stop")
        campaign_id = row.get("campaign_id")
        campaign_name = row.get("campaign_name")
        adset_id = row.get("adset_id")
        adset_name = row.get("adset_name")
        ad_id = row.get("ad_id")
        ad_name = row.get("ad_name")
        impressions = _safe_int(row.get("impressions"))
        clicks = _safe_int(row.get("clicks"))
        reach = _safe_int(row.get("reach"))
        frequency = _safe_float(row.get("frequency"))
        spend = _safe_float(row.get("spend"))
        currency = (row.get("account_currency") or row.get("currency") or "").strip().upper()
        conv_count, _ = _extract_conversions_from_actions(row.get("actions"))
        conv_value = _extract_value_from_action_values(row.get("action_values"))
        if conv_value == 0 and conv_count > 0:
            conv_value = conv_count  # fallback

        ctr_pct = (100.0 * clicks / impressions) if impressions > 0 else 0.0
        cpc = (spend / clicks) if clicks > 0 else 0.0
        cpa = (spend / conv_count) if conv_count > 0 else 0.0
        roas = (conv_value / spend) if spend > 0 else 0.0

        out = {
            "date": date_start,
            "report_level": level,
            "metrics": {
                "impressions": impressions,
                "clicks": clicks,
                "reach": reach,
                "frequency": round(frequency, 4) if frequency > 0 else 0.0,
                "cost": round(spend, 4),
                "conversions": round(conv_count, 4),
                "conversions_value": round(conv_value, 4),
                "currency": currency or None,
            },
            "derived": {
                "ctr_pct": round(ctr_pct, 2),
                "cpc": round(cpc, 4),
                "cpa": round(cpa, 4),
                "roas": round(roas, 4),
            },
        }
        if level == "campaign":
            out["campaign_id"] = campaign_id
            out["campaign_name"] = campaign_name
        elif level == "ad":
            out["campaign_id"] = campaign_id
            out["campaign_name"] = campaign_name
            out["adset_id"] = adset_id
            out["adset_name"] = adset_name
            out["ad_id"] = ad_id
            out["ad_name"] = ad_name
        else:
            out["campaign_id"] = campaign_id
            out["campaign_name"] = campaign_name
            if adset_id:
                out["adset_id"] = adset_id
            if adset_name:
                out["adset_name"] = adset_name
            if ad_id:
                out["ad_id"] = ad_id
            if ad_name:
                out["ad_name"] = ad_name

        if breakdowns:
            segments: Dict[str, Any] = {}
            for b in breakdowns:
                key = str(b).strip()
                if key and row.get(key) is not None:
                    segments[key] = row.get(key)
            if segments:
                out["segments"] = segments
        return out

    @staticmethod
    def normalize_campaign_daily_rows(
        raw_rows: List[Dict[str, Any]],
        level: str = "campaign",
        breakdowns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Normalize Meta insight rows into rows + summary (same shape as Google Ads)."""
        out_rows: List[Dict[str, Any]] = []
        tot_impr = 0
        tot_clicks = 0
        tot_reach = 0
        tot_cost = 0.0
        tot_conv = 0.0
        tot_conv_val = 0.0

        for r in raw_rows or []:
            if not isinstance(r, dict):
                continue
            flat = MetaAdsService._flatten_insight_row(r, level=level, breakdowns=breakdowns)
            if not flat:
                continue
            out_rows.append(flat)
            m = flat.get("metrics") or {}
            tot_impr += int(m.get("impressions") or 0)
            tot_clicks += int(m.get("clicks") or 0)
            tot_reach += int(m.get("reach") or 0)
            tot_cost += _safe_float(m.get("cost"))
            tot_conv += _safe_float(m.get("conversions"))
            tot_conv_val += _safe_float(m.get("conversions_value"))

        currency_values = {
            (row.get("metrics") or {}).get("currency")
            for row in out_rows
            if isinstance(row, dict) and (row.get("metrics") or {}).get("currency")
        }
        if not currency_values:
            summary_currency = None
        elif len(currency_values) == 1:
            summary_currency = next(iter(currency_values))
        else:
            summary_currency = "MIXED"

        summary_ctr = (100.0 * tot_clicks / tot_impr) if tot_impr > 0 else 0.0
        summary_freq = (tot_impr / tot_reach) if tot_reach > 0 else 0.0
        summary_cpc = (tot_cost / tot_clicks) if tot_clicks > 0 else 0.0
        summary_cpa = (tot_cost / tot_conv) if tot_conv > 0 else 0.0
        summary_roas = (tot_conv_val / tot_cost) if tot_cost > 0 else 0.0

        summary = {
            "total_impressions": tot_impr,
            "total_clicks": tot_clicks,
            "total_reach": tot_reach,
            "avg_frequency": round(summary_freq, 4),
            "total_cost": round(tot_cost, 2),
            "total_conversions": round(tot_conv, 4),
            "total_conversions_value": round(tot_conv_val, 4),
            "ctr_pct": round(summary_ctr, 2),
            "cpc": round(summary_cpc, 4),
            "cpa": round(summary_cpa, 4),
            "roas": round(summary_roas, 4),
            "currency": summary_currency,
            "report_level": level,
        }
        if breakdowns:
            summary["breakdowns"] = [str(b).strip() for b in breakdowns if str(b).strip()]
        return {"rows": out_rows, "summary": summary}
