"""
Google Ads reporting service (REST) using Application Default Credentials (ADC).

Design goals:
- Fit Bigas' existing Google Cloud pattern (GA4 + GCS already use ADC).
- Avoid storing OAuth refresh tokens locally by default.
- Provide simple, curl-triggerable reporting endpoints.

Requirements:
- The runtime service account (Cloud Run / local ADC user) must be granted access
  to the Google Ads account (or a manager account) in Google Ads UI.
- A Google Ads API developer token is required in every call.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import requests
import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest

logger = logging.getLogger(__name__)


ADWORDS_SCOPE = "https://www.googleapis.com/auth/adwords"
GOOGLE_ADS_API_VERSION = "v23"
GOOGLE_ADS_API_BASE = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"


class GoogleAdsAuthError(RuntimeError):
    pass


class GoogleAdsApiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


@dataclass(frozen=True)
class GoogleAdsConfig:
    developer_token: str
    login_customer_id: Optional[str] = None


def _safe_float(val: Any) -> float:
    try:
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        return float(str(val))
    except (TypeError, ValueError):
        return 0.0


class GoogleAdsService:
    """
    Minimal Google Ads REST client for reporting via GAQL SearchStream.

    Uses ADC credentials at runtime (Cloud Run service account / gcloud user).
    """

    def __init__(self, developer_token: Optional[str] = None, login_customer_id: Optional[str] = None):
        developer_token = developer_token or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
        if not developer_token or not developer_token.strip():
            raise ValueError("GOOGLE_ADS_DEVELOPER_TOKEN is required for Google Ads API calls.")

        login_customer_id = (login_customer_id or os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or "").strip() or None

        self.config = GoogleAdsConfig(
            developer_token=developer_token.strip(),
            login_customer_id=login_customer_id,
        )

    def _get_access_token(self) -> str:
        """
        Get an OAuth2 access token using Application Default Credentials.
        """
        try:
            credentials, _project_id = google.auth.default(scopes=[ADWORDS_SCOPE])
            credentials.refresh(GoogleAuthRequest())
            if not getattr(credentials, "token", None):
                raise GoogleAdsAuthError("ADC refresh succeeded but no access token was produced.")
            return credentials.token
        except Exception as e:
            raise GoogleAdsAuthError(f"Failed to obtain Google Ads access token via ADC: {e}") from e

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Authorization": f"Bearer {self._get_access_token()}",
            "developer-token": self.config.developer_token,
            "Content-Type": "application/json",
        }
        if self.config.login_customer_id:
            # Must be hyphen-free per Google Ads docs.
            headers["login-customer-id"] = self.config.login_customer_id.replace("-", "")
        return headers

    def list_accessible_customers(self) -> List[str]:
        """
        Calls CustomerService.ListAccessibleCustomers (REST).
        Useful as a smoke test: does not require a customer_id path parameter.
        """
        url = f"{GOOGLE_ADS_API_BASE}/customers:listAccessibleCustomers"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        if resp.status_code >= 400:
            request_id = resp.headers.get("request-id") or resp.headers.get("google-ads-request-id")
            logger.error(
                "Google Ads listAccessibleCustomers failed: status=%s request_id=%s body=%s",
                resp.status_code,
                request_id,
                (resp.text or "")[:2000],
            )
            raise GoogleAdsApiError(
                f"Google Ads API error calling listAccessibleCustomers ({resp.status_code})",
                status_code=resp.status_code,
                response_text=resp.text,
            )
        data = resp.json() if resp.text else {}
        return data.get("resourceNames", []) or []

    @staticmethod
    def build_performance_query(
        start: date,
        end: date,
        report_level: str = "campaign",
        breakdowns: Optional[List[str]] = None,
        include_optional_reach_metrics: bool = True,
    ) -> str:
        """
        Build GAQL for campaign/ad/audience-like performance.
        report_level:
          - campaign
          - ad
          - audience_breakdown (campaign with selected segment breakdown columns)
        """
        start_s = start.isoformat()
        end_s = end.isoformat()
        level = (report_level or "campaign").strip().lower()
        if level not in {"campaign", "ad", "audience_breakdown"}:
            raise ValueError("report_level must be one of: campaign, ad, audience_breakdown")

        base_select = [
            "segments.date",
            "campaign.id",
            "campaign.name",
            "customer.currency_code",
            "metrics.impressions",
            "metrics.clicks",
            "metrics.cost_micros",
            "metrics.conversions",
            "metrics.conversions_value",
        ]
        if include_optional_reach_metrics:
            # Not all account/resource combinations support these; caller can fallback without them.
            base_select += [
                "metrics.unique_users",
                "metrics.average_impression_frequency_per_user",
            ]

        from_resource = "campaign"
        if level == "ad":
            from_resource = "ad_group_ad"
            base_select += [
                "ad_group.id",
                "ad_group.name",
                "ad_group_ad.ad.id",
                "ad_group_ad.ad.name",
            ]
        elif level == "audience_breakdown":
            breakdown_map = {
                "device": "segments.device",
                "network": "segments.ad_network_type",
                "day_of_week": "segments.day_of_week",
            }
            for b in (breakdowns or []):
                key = str(b).strip().lower()
                col = breakdown_map.get(key)
                if col and col not in base_select:
                    base_select.append(col)

        select_clause = ", ".join(base_select)
        return (
            f"SELECT {select_clause} "
            f"FROM {from_resource} "
            f"WHERE segments.date BETWEEN '{start_s}' AND '{end_s}' "
            "ORDER BY segments.date"
        )

    @staticmethod
    def build_campaign_daily_performance_query(start: date, end: date) -> str:
        """Backward-compatible wrapper for campaign-level query."""
        return GoogleAdsService.build_performance_query(start=start, end=end, report_level="campaign")

    def search_stream(self, customer_id: str, query: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Run GAQL via GoogleAdsService.SearchStream and return:
        - flattened results rows (best-effort, as returned by REST)
        - raw response chunks for storage/auditing
        """
        customer_id = (customer_id or "").replace("-", "").strip()
        if not customer_id:
            raise ValueError("customer_id is required (GOOGLE_ADS_CUSTOMER_ID or request payload).")

        url = f"{GOOGLE_ADS_API_BASE}/customers/{customer_id}/googleAds:searchStream"
        payload = {"query": query}

        resp = requests.post(url, headers=self._headers(), json=payload, timeout=60)
        if resp.status_code >= 400:
            request_id = resp.headers.get("request-id") or resp.headers.get("google-ads-request-id")
            logger.error(
                "Google Ads searchStream failed: status=%s request_id=%s body=%s",
                resp.status_code,
                request_id,
                (resp.text or "")[:2000],
            )
            raise GoogleAdsApiError(
                f"Google Ads API error calling searchStream ({resp.status_code})",
                status_code=resp.status_code,
                response_text=resp.text,
            )

        chunks = resp.json() if resp.text else []
        rows: List[Dict[str, Any]] = []
        if isinstance(chunks, list):
            for chunk in chunks:
                for row in chunk.get("results", []) or []:
                    rows.append(row)
        else:
            # Unexpected but handle gracefully.
            rows = chunks.get("results", []) or []
            chunks = [chunks]

        return rows, chunks

    @staticmethod
    def _flatten_campaign_daily_row(
        row: Dict[str, Any],
        report_level: str = "campaign",
        breakdowns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Flatten a single Google Ads SearchStream row into a simple, stable dict.

        Expected row shape (as returned by REST SearchStream):
          {
            "segments": {"date": "..."},
            "campaign": {"id": "...", "name": "..."},
            "metrics": {
              "impressions": "...",
              "clicks": "...",
              "cost_micros": "...",
              "conversions": "...",
              "conversions_value": "..."
            }
          }
        """
        if not isinstance(row, dict):
            return {}

        report_level = (report_level or "campaign").strip().lower()
        segments = row.get("segments") or {}
        campaign = row.get("campaign") or {}
        ad_group = row.get("ad_group") or {}
        ad_group_ad = row.get("ad_group_ad") or {}
        ad = ad_group_ad.get("ad") if isinstance(ad_group_ad, dict) else {}
        customer = row.get("customer") or {}
        metrics = row.get("metrics") or {}

        date_s = segments.get("date")
        cid = campaign.get("id")
        cname = campaign.get("name")
        currency_code = (customer.get("currency_code") or "").strip().upper()

        impressions = int(_safe_float(metrics.get("impressions")))
        clicks = int(_safe_float(metrics.get("clicks")))
        cost_micros = _safe_float(metrics.get("cost_micros"))
        conversions = _safe_float(metrics.get("conversions"))
        conv_value = _safe_float(metrics.get("conversions_value"))
        unique_users = int(_safe_float(metrics.get("unique_users"))) if metrics.get("unique_users") is not None else None
        avg_freq = _safe_float(metrics.get("average_impression_frequency_per_user"))

        cost = cost_micros / 1_000_000.0 if cost_micros else 0.0

        ctr_pct = (100.0 * clicks / impressions) if impressions > 0 else 0.0
        if not avg_freq and impressions > 0 and unique_users and unique_users > 0:
            avg_freq = float(impressions) / float(unique_users)
        cpc = (cost / clicks) if clicks > 0 else 0.0
        cpa = (cost / conversions) if conversions > 0 else 0.0
        roas = (conv_value / cost) if cost > 0 else 0.0

        out = {
            "date": date_s,
            "campaign_id": cid,
            "campaign_name": cname,
            "report_level": report_level,
            "currency_code": currency_code or None,
            "metrics": {
                "impressions": impressions,
                "clicks": clicks,
                "reach": unique_users,
                "frequency": round(avg_freq, 4) if avg_freq else 0.0,
                "cost": round(cost, 4),
                "conversions": round(conversions, 4),
                "conversions_value": round(conv_value, 4),
            },
            "derived": {
                "ctr_pct": round(ctr_pct, 2),
                "cpc": round(cpc, 4),
                "cpa": round(cpa, 4),
                "roas": round(roas, 4),
            },
        }
        if report_level == "ad":
            out["ad_group_id"] = ad_group.get("id")
            out["ad_group_name"] = ad_group.get("name")
            out["ad_id"] = ad.get("id") if isinstance(ad, dict) else None
            out["ad_name"] = ad.get("name") if isinstance(ad, dict) else None
        if report_level == "audience_breakdown":
            segments_out: Dict[str, Any] = {}
            for b in (breakdowns or []):
                bk = str(b).strip().lower()
                if bk == "device" and segments.get("device") is not None:
                    segments_out["device"] = segments.get("device")
                elif bk == "network" and segments.get("ad_network_type") is not None:
                    segments_out["network"] = segments.get("ad_network_type")
                elif bk == "day_of_week" and segments.get("day_of_week") is not None:
                    segments_out["day_of_week"] = segments.get("day_of_week")
            if segments_out:
                out["segments"] = segments_out
        return out

    @staticmethod
    def normalize_campaign_daily_rows(
        raw_rows: List[Dict[str, Any]],
        report_level: str = "campaign",
        breakdowns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Normalize SearchStream results into:
          - 'rows': flattened per-day per-campaign dicts
          - 'summary': aggregate metrics and derived KPIs
        """
        out_rows: List[Dict[str, Any]] = []
        tot_impr = 0
        tot_clicks = 0
        tot_reach = 0
        tot_cost = 0.0
        tot_conv = 0.0
        tot_conv_val = 0.0
        currency_code: Optional[str] = None

        for r in raw_rows or []:
            if not isinstance(r, dict):
                continue
            flat = GoogleAdsService._flatten_campaign_daily_row(r, report_level=report_level, breakdowns=breakdowns)
            if not flat:
                continue
            out_rows.append(flat)
            if currency_code is None and flat.get("currency_code"):
                currency_code = flat.get("currency_code")

            m = flat.get("metrics") or {}
            tot_impr += int(m.get("impressions") or 0)
            tot_clicks += int(m.get("clicks") or 0)
            tot_reach += int(m.get("reach") or 0)
            tot_cost += _safe_float(m.get("cost"))
            tot_conv += _safe_float(m.get("conversions"))
            tot_conv_val += _safe_float(m.get("conversions_value"))

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
            "currency": currency_code,
            "report_level": report_level,
        }
        if breakdowns:
            summary["breakdowns"] = [str(b).strip() for b in breakdowns if str(b).strip()]

        return {"rows": out_rows, "summary": summary}

