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
import re
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

    # Long-standing type-specific detail fields. Keep this list conservative so
    # GAQL stays valid across Google Ads API versions we pin (currently v23).
    _RECOMMENDATION_DETAIL_FIELDS = (
        "recommendation.campaign_budget_recommendation",
        "recommendation.keyword_recommendation",
        "recommendation.text_ad_recommendation",
        "recommendation.target_cpa_opt_in_recommendation",
        "recommendation.maximize_conversions_opt_in_recommendation",
        "recommendation.enhanced_cpc_opt_in_recommendation",
        "recommendation.maximize_clicks_opt_in_recommendation",
        "recommendation.optimize_ad_rotation_recommendation",
        "recommendation.move_unused_budget_recommendation",
        "recommendation.target_roas_opt_in_recommendation",
        "recommendation.forecasting_campaign_budget_recommendation",
        "recommendation.responsive_search_ad_recommendation",
        "recommendation.marginal_roi_campaign_budget_recommendation",
        "recommendation.use_broad_match_keyword_recommendation",
        "recommendation.responsive_search_ad_asset_recommendation",
        "recommendation.responsive_search_ad_improve_ad_strength_recommendation",
        "recommendation.raise_target_cpa_bid_too_low_recommendation",
        "recommendation.callout_asset_recommendation",
        "recommendation.sitelink_asset_recommendation",
    )

    @classmethod
    def build_recommendations_query(
        cls,
        *,
        include_dismissed: bool = False,
        recommendation_types: Optional[List[str]] = None,
        include_details: bool = True,
    ) -> str:
        """
        GAQL for Google Ads Recommendations (Optimization Score recommendations).

        Read-only: list type, campaign, impact, and common type-specific details.
        Apply/dismiss is out of scope for this client.
        """
        select_fields = [
            "recommendation.resource_name",
            "recommendation.type",
            "recommendation.dismissed",
            "recommendation.campaign",
            "recommendation.ad_group",
            "recommendation.impact",
            "campaign.id",
            "campaign.name",
            "customer.descriptive_name",
        ]
        if include_details:
            select_fields.extend(cls._RECOMMENDATION_DETAIL_FIELDS)
        where_parts: List[str] = []
        if not include_dismissed:
            where_parts.append("recommendation.dismissed = FALSE")
        types = [str(t).strip().upper() for t in (recommendation_types or []) if str(t).strip()]
        # Enum literals in GAQL are unquoted identifiers, so reject anything
        # that is not a bare enum name before it is interpolated.
        if any(re.fullmatch(r"[A-Z0-9_]+", t) is None for t in types):
            raise ValueError(
                "recommendation_types must be enum names like KEYWORD or CAMPAIGN_BUDGET"
            )
        if types:
            type_list = ", ".join(types)
            where_parts.append(f"recommendation.type IN ({type_list})")

        query = f"SELECT {', '.join(select_fields)} FROM recommendation"
        if where_parts:
            query += " WHERE " + " AND ".join(where_parts)
        return query

    @staticmethod
    def build_optimization_score_query() -> str:
        return (
            "SELECT customer.id, customer.descriptive_name, customer.optimization_score "
            "FROM customer "
            "LIMIT 1"
        )

    @staticmethod
    def build_optimization_score_uplift_query() -> str:
        return (
            "SELECT segments.recommendation_type, "
            "metrics.optimization_score_uplift, "
            "metrics.optimization_score_url "
            "FROM customer"
        )

    @staticmethod
    def _normalize_impact_metrics(metrics: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(metrics, dict) or not metrics:
            return None
        impressions = int(_safe_float(metrics.get("impressions")))
        clicks = int(_safe_float(metrics.get("clicks")))
        cost_micros = _safe_float(metrics.get("costMicros", metrics.get("cost_micros")))
        conversions = _safe_float(metrics.get("conversions"))
        all_conversions = _safe_float(metrics.get("allConversions", metrics.get("all_conversions")))
        video_views = int(_safe_float(metrics.get("videoViews", metrics.get("video_views"))))
        return {
            "impressions": impressions,
            "clicks": clicks,
            "cost": round(cost_micros / 1_000_000.0, 4) if cost_micros else 0.0,
            "conversions": round(conversions, 4),
            "all_conversions": round(all_conversions, 4),
            "video_views": video_views,
        }

    @classmethod
    def _extract_recommendation_details(cls, recommendation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Pick the first non-empty *Recommendation payload from the REST row."""
        if not isinstance(recommendation, dict):
            return None
        for key, value in recommendation.items():
            if not key.endswith("Recommendation") and not key.endswith("_recommendation"):
                continue
            if value in (None, {}, []):
                continue
            detail_type = key
            if detail_type.endswith("Recommendation"):
                # keywordRecommendation -> keyword
                detail_type = detail_type[: -len("Recommendation")]
            elif detail_type.endswith("_recommendation"):
                detail_type = detail_type[: -len("_recommendation")]
            return {"kind": detail_type, "payload": value}
        return None

    @classmethod
    def normalize_recommendation_row(cls, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(row, dict):
            return None
        recommendation = row.get("recommendation") or {}
        if not isinstance(recommendation, dict) or not recommendation:
            return None
        campaign = row.get("campaign") or {}
        customer = row.get("customer") or {}
        impact = recommendation.get("impact") or {}
        base_metrics = cls._normalize_impact_metrics(
            impact.get("baseMetrics") or impact.get("base_metrics")
        )
        potential_metrics = cls._normalize_impact_metrics(
            impact.get("potentialMetrics") or impact.get("potential_metrics")
        )
        impact_out = None
        if base_metrics or potential_metrics:
            impact_out = {
                "base_metrics": base_metrics,
                "potential_metrics": potential_metrics,
            }
            if base_metrics and potential_metrics:
                impact_out["delta"] = {
                    "impressions": (potential_metrics["impressions"] - base_metrics["impressions"]),
                    "clicks": (potential_metrics["clicks"] - base_metrics["clicks"]),
                    "cost": round(potential_metrics["cost"] - base_metrics["cost"], 4),
                    "conversions": round(
                        potential_metrics["conversions"] - base_metrics["conversions"], 4
                    ),
                }

        campaign_resource = recommendation.get("campaign")
        campaign_id = campaign.get("id")
        if not campaign_id and isinstance(campaign_resource, str) and "/" in campaign_resource:
            campaign_id = campaign_resource.split("/")[-1]

        return {
            "resource_name": recommendation.get("resourceName") or recommendation.get("resource_name"),
            "type": recommendation.get("type"),
            "dismissed": bool(recommendation.get("dismissed")),
            "campaign_id": str(campaign_id) if campaign_id is not None else None,
            "campaign_name": campaign.get("name"),
            "campaign_resource": campaign_resource,
            "ad_group_resource": recommendation.get("adGroup") or recommendation.get("ad_group"),
            "account_name": customer.get("descriptiveName") or customer.get("descriptive_name"),
            "impact": impact_out,
            "details": cls._extract_recommendation_details(recommendation),
        }

    @classmethod
    def normalize_recommendation_rows(cls, raw_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        recommendations: List[Dict[str, Any]] = []
        by_type: Dict[str, int] = {}
        for row in raw_rows or []:
            flat = cls.normalize_recommendation_row(row)
            if not flat:
                continue
            recommendations.append(flat)
            rtype = str(flat.get("type") or "UNKNOWN")
            by_type[rtype] = by_type.get(rtype, 0) + 1

        recommendations.sort(
            key=lambda r: (
                -_safe_float(((r.get("impact") or {}).get("delta") or {}).get("conversions")),
                -_safe_float(((r.get("impact") or {}).get("delta") or {}).get("clicks")),
                str(r.get("type") or ""),
            )
        )
        return {
            "recommendations": recommendations,
            "count": len(recommendations),
            "by_type": dict(sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        }

    def fetch_recommendations(
        self,
        customer_id: str,
        *,
        include_dismissed: bool = False,
        recommendation_types: Optional[List[str]] = None,
        include_optimization_score: bool = True,
    ) -> Dict[str, Any]:
        """
        Fetch active Google Ads recommendations + optional optimization score/uplift.
        """
        customer_id = (customer_id or "").replace("-", "").strip()
        if not customer_id:
            raise ValueError("customer_id is required (GOOGLE_ADS_CUSTOMER_ID or request payload).")

        query = self.build_recommendations_query(
            include_dismissed=include_dismissed,
            recommendation_types=recommendation_types,
            include_details=True,
        )
        try:
            raw_rows, _chunks = self.search_stream(customer_id, query)
        except GoogleAdsApiError:
            # Fall back to core fields if a detail column is unavailable on this API version.
            query = self.build_recommendations_query(
                include_dismissed=include_dismissed,
                recommendation_types=recommendation_types,
                include_details=False,
            )
            raw_rows, _chunks = self.search_stream(customer_id, query)
        normalized = self.normalize_recommendation_rows(raw_rows)

        optimization: Optional[Dict[str, Any]] = None
        if include_optimization_score:
            optimization = {"score": None, "account_name": None, "uplift_by_type": []}
            try:
                score_rows, _ = self.search_stream(
                    customer_id, self.build_optimization_score_query()
                )
                if score_rows:
                    customer = (score_rows[0] or {}).get("customer") or {}
                    score_raw = customer.get("optimizationScore", customer.get("optimization_score"))
                    optimization["score"] = (
                        round(_safe_float(score_raw), 4) if score_raw is not None else None
                    )
                    optimization["account_name"] = (
                        customer.get("descriptiveName") or customer.get("descriptive_name")
                    )
            except GoogleAdsApiError as e:
                logger.warning(
                    "Google Ads optimization score query failed (continuing): status=%s body=%s",
                    e.status_code,
                    (e.response_text or "")[:500],
                )
                optimization["score_error"] = str(e)

            try:
                uplift_rows, _ = self.search_stream(
                    customer_id, self.build_optimization_score_uplift_query()
                )
                uplift_list: List[Dict[str, Any]] = []
                for row in uplift_rows or []:
                    segments = (row or {}).get("segments") or {}
                    metrics = (row or {}).get("metrics") or {}
                    rtype = segments.get("recommendationType") or segments.get("recommendation_type")
                    uplift = metrics.get("optimizationScoreUplift", metrics.get("optimization_score_uplift"))
                    url = metrics.get("optimizationScoreUrl", metrics.get("optimization_score_url"))
                    if rtype is None and uplift is None:
                        continue
                    uplift_list.append(
                        {
                            "recommendation_type": rtype,
                            "optimization_score_uplift": (
                                round(_safe_float(uplift), 4) if uplift is not None else None
                            ),
                            "optimization_score_url": url,
                        }
                    )
                uplift_list.sort(
                    key=lambda u: (-_safe_float(u.get("optimization_score_uplift")), str(u.get("recommendation_type") or ""))
                )
                optimization["uplift_by_type"] = uplift_list
            except GoogleAdsApiError as e:
                logger.warning(
                    "Google Ads optimization score uplift query failed (continuing): status=%s body=%s",
                    e.status_code,
                    (e.response_text or "")[:500],
                )
                optimization["uplift_error"] = str(e)

        return {
            "customer_id": customer_id,
            "include_dismissed": include_dismissed,
            "recommendation_types": recommendation_types or [],
            "optimization": optimization,
            **normalized,
        }

