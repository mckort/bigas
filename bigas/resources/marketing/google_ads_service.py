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
    def build_campaign_daily_performance_query(start: date, end: date) -> str:
        """
        Simple GAQL query for daily campaign performance.
        """
        start_s = start.isoformat()
        end_s = end.isoformat()
        return (
            "SELECT "
            "segments.date, "
            "campaign.id, "
            "campaign.name, "
            "metrics.impressions, "
            "metrics.clicks, "
            "metrics.cost_micros, "
            "metrics.conversions, "
            "metrics.conversions_value "
            "FROM campaign "
            f"WHERE segments.date BETWEEN '{start_s}' AND '{end_s}' "
            "ORDER BY segments.date"
        )

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

