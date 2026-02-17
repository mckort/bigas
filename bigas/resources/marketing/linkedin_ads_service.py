"""
LinkedIn Ads reporting service (versioned /rest endpoints).

Uses OAuth2 refresh token to mint access tokens, then calls:
- Ad accounts: GET https://api.linkedin.com/rest/adAccounts?q=search
- Ad analytics: GET https://api.linkedin.com/rest/adAnalytics?q=analytics ...

Required env vars:
- LINKEDIN_CLIENT_ID
- LINKEDIN_CLIENT_SECRET
- LINKEDIN_REFRESH_TOKEN

Optional env vars:
- LINKEDIN_VERSION (default: 202601)
"""

from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

import requests
from urllib.parse import quote

logger = logging.getLogger(__name__)


LINKEDIN_OAUTH_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com/rest"
DEFAULT_LINKEDIN_VERSION = "202601"


class LinkedInAuthError(RuntimeError):
    pass


class LinkedInApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_text: Optional[str] = None,
        operation: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        # Optional short label describing which LinkedIn operation failed,
        # e.g. "adAnalytics statistics" or "titles get".
        self.operation = operation


@dataclass(frozen=True)
class LinkedInConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    linkedin_version: str


_token_cache: Dict[str, Any] = {
    "access_token": None,
    "expires_at": 0,
}

_gcs_token_cache: Dict[str, Any] = {
    "access_token": None,
    "expires_at": 0,
    "last_checked_at": 0,
}


def _load_access_token_from_gcs() -> Optional[str]:
    """
    Best-effort: load a cached access token from GCS if present.

    Stored by the linkedin_exchange_code endpoint at:
      secrets/linkedin/access_token.json
    """
    try:
        from bigas.resources.marketing.storage_service import StorageService

        storage = StorageService()
        obj = storage.get_json("secrets/linkedin/access_token.json") or {}
        token = (obj.get("access_token") or "").strip()
        expires_at = int(obj.get("expires_at") or 0)
        if not token or not expires_at:
            logger.info(
                "LinkedIn GCS token cache miss or incomplete payload at secrets/linkedin/access_token.json"
            )
            return None

        _gcs_token_cache["access_token"] = token
        _gcs_token_cache["expires_at"] = expires_at
        logger.info(
            "LinkedIn access token loaded from GCS cache; expires_at=%s", expires_at
        )
        return token
    except Exception as e:
        # Best-effort only: log at warning level so operational issues are visible,
        # but never fail the caller because of GCS problems.
        logger.warning(
            "Failed to load LinkedIn access token from GCS cache: %s", str(e)
        )
        return None

class LinkedInAdsService:
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        linkedin_version: Optional[str] = None,
    ):
        client_id = (client_id or os.environ.get("LINKEDIN_CLIENT_ID") or "").strip()
        client_secret = (client_secret or os.environ.get("LINKEDIN_CLIENT_SECRET") or "").strip()
        refresh_token = (refresh_token or os.environ.get("LINKEDIN_REFRESH_TOKEN") or "").strip()
        linkedin_version = (linkedin_version or os.environ.get("LINKEDIN_VERSION") or DEFAULT_LINKEDIN_VERSION).strip()

        if not client_id:
            raise ValueError("LINKEDIN_CLIENT_ID is required")
        if not client_secret:
            raise ValueError("LINKEDIN_CLIENT_SECRET is required")
        if not refresh_token:
            raise ValueError("LINKEDIN_REFRESH_TOKEN is required")

        self.config = LinkedInConfig(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            linkedin_version=linkedin_version,
        )

    def _mint_access_token(self) -> str:
        """
        Mint a LinkedIn access token using a refresh token.
        Caches token in-memory for the process lifetime.
        """
        logger.debug("LinkedIn _mint_access_token: start")
        # If an access token is explicitly provided (manual mode), prefer it.
        manual_token = (os.environ.get("LINKEDIN_ACCESS_TOKEN") or "").strip()
        if manual_token:
            logger.debug("LinkedIn _mint_access_token: using LINKEDIN_ACCESS_TOKEN env")
            return manual_token

        now = int(time.time())
        cached = _token_cache.get("access_token")
        expires_at = int(_token_cache.get("expires_at") or 0)
        if cached and expires_at - now > 60:
            logger.debug("LinkedIn _mint_access_token: using in-memory cache")
            return cached

        # Try GCS-stored token to avoid hitting OAuth endpoint (helps when LinkedIn rate limits refresh flow).
        gcs_cached = _gcs_token_cache.get("access_token")
        gcs_expires_at = int(_gcs_token_cache.get("expires_at") or 0)
        last_checked = int(_gcs_token_cache.get("last_checked_at") or 0)
        if gcs_cached and gcs_expires_at - now > 60:
            logger.debug("LinkedIn _mint_access_token: using GCS cache")
            return gcs_cached
        # Avoid checking GCS too frequently.
        if now - last_checked > 60:
            _gcs_token_cache["last_checked_at"] = now
            token = _load_access_token_from_gcs()
            if token and int(_gcs_token_cache.get("expires_at") or 0) - now > 60:
                return token

        logger.info("LinkedIn _mint_access_token: calling OAuth endpoint (refresh_token grant)")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.config.refresh_token,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        resp = requests.post(LINKEDIN_OAUTH_TOKEN_URL, data=data, timeout=30)
        if resp.status_code >= 400:
            retry_after = resp.headers.get("Retry-After")
            logger.error(
                "LinkedIn token mint failed: status=%s retry_after=%s body=%s",
                resp.status_code,
                retry_after,
                (resp.text or "")[:2000],
            )
            raise LinkedInAuthError(f"Failed to mint LinkedIn access token ({resp.status_code})")

        payload = resp.json()
        access_token = payload.get("access_token")
        expires_in = int(payload.get("expires_in") or 0)
        if not access_token:
            raise LinkedInAuthError("LinkedIn token response missing access_token")

        _token_cache["access_token"] = access_token
        _token_cache["expires_at"] = now + max(expires_in, 0)
        logger.info("LinkedIn _mint_access_token: OAuth done expires_in=%s", expires_in)
        return access_token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._mint_access_token()}",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": self.config.linkedin_version,
            "Accept": "application/json",
        }

    def _headers_v2(self) -> Dict[str, str]:
        """
        Headers for legacy /v2 standardized-data endpoints (no LinkedIn-Version required).
        """
        return {
            "Authorization": f"Bearer {self._mint_access_token()}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Accept": "application/json",
        }

    def get_title(self, title_id: str) -> Dict[str, Any]:
        url = f"https://api.linkedin.com/v2/titles/{title_id}"
        resp = requests.get(url, headers=self._headers_v2(), timeout=30)
        if resp.status_code >= 400:
            raise LinkedInApiError(
                "LinkedIn API error calling titles",
                status_code=resp.status_code,
                response_text=resp.text,
                operation="titles get",
            )
        return resp.json()

    def get_function(self, function_id: str) -> Dict[str, Any]:
        url = f"https://api.linkedin.com/v2/functions/{function_id}"
        resp = requests.get(url, headers=self._headers_v2(), timeout=30)
        if resp.status_code >= 400:
            raise LinkedInApiError(
                "LinkedIn API error calling functions",
                status_code=resp.status_code,
                response_text=resp.text,
                operation="functions get",
            )
        return resp.json()

    def get_industry(self, industry_id: str) -> Dict[str, Any]:
        url = f"https://api.linkedin.com/v2/industries/{industry_id}"
        resp = requests.get(url, headers=self._headers_v2(), timeout=30)
        if resp.status_code >= 400:
            raise LinkedInApiError(
                "LinkedIn API error calling industries",
                status_code=resp.status_code,
                response_text=resp.text,
                operation="industries get",
            )
        return resp.json()

    def get_seniority(self, seniority_id: str) -> Dict[str, Any]:
        url = f"https://api.linkedin.com/v2/seniorities/{seniority_id}"
        resp = requests.get(url, headers=self._headers_v2(), timeout=30)
        if resp.status_code >= 400:
            raise LinkedInApiError(
                "LinkedIn API error calling seniorities",
                status_code=resp.status_code,
                response_text=resp.text,
                operation="seniorities get",
            )
        return resp.json()

    def get_geo(self, geo_id: str) -> Dict[str, Any]:
        url = f"https://api.linkedin.com/v2/geo/{geo_id}"
        resp = requests.get(url, headers=self._headers_v2(), timeout=30)
        if resp.status_code >= 400:
            raise LinkedInApiError(
                "LinkedIn API error calling geo",
                status_code=resp.status_code,
                response_text=resp.text,
                operation="geo get",
            )
        return resp.json()

    def get_creative(self, *, ad_account_id: int, creative_urn: str) -> Dict[str, Any]:
        """
        Resolve a sponsored creative URN to creative metadata (including 'name') using the versioned Creatives API.

        Docs:
          GET https://api.linkedin.com/rest/adAccounts/{adAccountId}/creatives/{encodedCreativeUrn}
        """
        encoded = quote(creative_urn, safe="")
        url = f"{LINKEDIN_API_BASE}/adAccounts/{ad_account_id}/creatives/{encoded}"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        if resp.status_code >= 400:
            raise LinkedInApiError(
                "LinkedIn API error calling creatives",
                status_code=resp.status_code,
                response_text=resp.text,
                operation="creatives get",
            )
        return resp.json()

    def list_ad_accounts(self, start: int = 0, count: int = 10) -> Dict[str, Any]:
        """
        List accessible ad accounts.
        """
        params = {"q": "search", "start": start, "count": count}
        url = f"{LINKEDIN_API_BASE}/adAccounts"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        if resp.status_code >= 400:
            logger.error("LinkedIn adAccounts failed: status=%s body=%s", resp.status_code, (resp.text or "")[:2000])
            raise LinkedInApiError(
                "LinkedIn API error calling adAccounts",
                status_code=resp.status_code,
                response_text=resp.text,
                operation="adAccounts search",
            )
        return resp.json()

    @staticmethod
    def _list_param(urns: List[str]) -> str:
        """
        Build a List(...) param value where each URN is URL-encoded, but List punctuation is not.
        """
        encoded = [quote(u, safe="") for u in urns if u]
        return f"List({','.join(encoded)})"

    def ad_analytics(
        self,
        start_date: date,
        end_date: date,
        time_granularity: str,
        pivot: str,
        account_urns: List[str],
        campaign_urns: Optional[List[str]] = None,
        campaign_group_urns: Optional[List[str]] = None,
        creative_urns: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch ad analytics using the analytics finder with structured parameters.

        Common use cases:
        - Account-level rollup: pivot=ACCOUNT, accounts=List(urn:li:sponsoredAccount:...)
        - Creative performance for a campaign: pivot=CREATIVE, campaigns=List(urn:li:sponsoredCampaign:...)
        """
        if not account_urns:
            raise ValueError("account_urns is required")

        date_range = (
            f"(start:(year:{start_date.year},month:{start_date.month},day:{start_date.day}),"
            f"end:(year:{end_date.year},month:{end_date.month},day:{end_date.day}))"
        )

        query = (
            "q=analytics"
            f"&dateRange={date_range}"
            f"&timeGranularity={time_granularity}"
            f"&pivot={pivot}"
            f"&accounts={self._list_param(account_urns)}"
        )

        if campaign_urns:
            query += f"&campaigns={self._list_param(campaign_urns)}"
        if campaign_group_urns:
            query += f"&campaignGroups={self._list_param(campaign_group_urns)}"
        if creative_urns:
            query += f"&creatives={self._list_param(creative_urns)}"
        if fields:
            query += "&fields=" + ",".join(fields)

        url = f"{LINKEDIN_API_BASE}/adAnalytics?{query}"
        logger.info("LinkedIn ad_analytics: GET pivot=%s accounts=%s (timeout 60s)", pivot, len(account_urns))
        resp = requests.get(url, headers=self._headers(), timeout=60)
        data = resp.json() if resp.text else {}
        elements = data.get("elements", []) if isinstance(data, dict) else []
        logger.info("LinkedIn ad_analytics: response status=%s elements=%s", resp.status_code, len(elements) if isinstance(elements, list) else 0)
        if resp.status_code >= 400:
            logger.error("LinkedIn adAnalytics failed: status=%s body=%s", resp.status_code, (resp.text or "")[:2000])
            raise LinkedInApiError("LinkedIn API error calling adAnalytics", resp.status_code, resp.text)
        return data

    def ad_analytics_statistics(
        self,
        start_date: date,
        end_date: date,
        time_granularity: str,
        pivots: List[str],
        account_urns: List[str],
        campaign_urns: Optional[List[str]] = None,
        campaign_group_urns: Optional[List[str]] = None,
        creative_urns: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch ad analytics using the statistics finder (grouping by up to 3 elements).

        Example:
          pivots=["CREATIVE","MEMBER_JOB_TITLE"]
        """
        if not account_urns:
            raise ValueError("account_urns is required")
        if not pivots:
            raise ValueError("pivots is required")
        if len(pivots) > 3:
            raise ValueError("LinkedIn adAnalytics statistics supports up to 3 pivots")

        date_range = (
            f"(start:(year:{start_date.year},month:{start_date.month},day:{start_date.day}),"
            f"end:(year:{end_date.year},month:{end_date.month},day:{end_date.day}))"
        )

        pivots_clean = [p.strip().upper() for p in pivots if str(p).strip()]
        query = (
            "q=statistics"
            f"&dateRange={date_range}"
            f"&timeGranularity={time_granularity}"
            f"&pivots={self._list_param(pivots_clean)}"
            f"&accounts={self._list_param(account_urns)}"
        )

        if campaign_urns:
            query += f"&campaigns={self._list_param(campaign_urns)}"
        if campaign_group_urns:
            query += f"&campaignGroups={self._list_param(campaign_group_urns)}"
        if creative_urns:
            query += f"&creatives={self._list_param(creative_urns)}"
        if fields:
            query += "&fields=" + ",".join(fields)

        url = f"{LINKEDIN_API_BASE}/adAnalytics?{query}"
        logger.info("LinkedIn ad_analytics_statistics: GET pivots=%s (timeout 60s)", pivots_clean)
        resp = requests.get(url, headers=self._headers(), timeout=60)
        data = resp.json() if resp.text else {}
        elements = data.get("elements", []) if isinstance(data, dict) else []
        logger.info("LinkedIn ad_analytics_statistics: response status=%s elements=%s", resp.status_code, len(elements) if isinstance(elements, list) else 0)
        if resp.status_code >= 400:
            logger.error("LinkedIn adAnalytics (statistics) failed: status=%s body=%s", resp.status_code, (resp.text or "")[:2000])
            raise LinkedInApiError("LinkedIn API error calling adAnalytics statistics", resp.status_code, resp.text)
        return data

