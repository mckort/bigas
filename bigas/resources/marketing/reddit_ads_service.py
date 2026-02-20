"""
Reddit Ads reporting service (Ads API v3).

Uses OAuth2 refresh token to mint access tokens, then calls:
- Ad accounts: Query/List via Ads API v3
- Reports: Create report (POST), then Get A Report (poll until ready)

Required env vars:
- REDDIT_CLIENT_ID
- REDDIT_CLIENT_SECRET
- REDDIT_REFRESH_TOKEN

Optional env vars:
- REDDIT_AD_ACCOUNT_ID (default account for reports)

API base: https://ads-api.reddit.com/api/v3
OAuth token: https://www.reddit.com/api/v1/access_token (refresh_token grant)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

REDDIT_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_ADS_API_BASE = "https://ads-api.reddit.com/api/v3"

# Reddit recommends a unique User-Agent; rate limits are stricter for generic ones.
REDDIT_USER_AGENT = "bigas:bigas-core:1.0 (by /u/bigas)"


def _normalize_row_keys(row: Dict[str, Any]) -> Dict[str, Any]:
    """Lowercase keys so downstream code finds campaign_id, impressions, etc."""
    if not isinstance(row, dict):
        return row
    return {str(k).lower(): v for k, v in row.items()}


def _filter_audience_by_campaign(
    rows: List[Dict[str, Any]], campaign_id: Optional[str]
) -> List[Dict[str, Any]]:
    """Filter audience rows to one campaign and drop campaign_id/campaign_name from each row."""
    if not campaign_id or not rows:
        return rows
    out = []
    cid_str = str(campaign_id).strip()
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        if str(r.get("campaign_id") or "").strip() != cid_str:
            continue
        r = dict(r)
        r.pop("campaign_id", None)
        r.pop("campaign_name", None)
        out.append(r)
    return out


def _extract_report_rows(
    report_resp: Dict[str, Any],
    get_url_fn: Optional[Any] = None,
    log: Optional[logging.Logger] = None,
) -> List[Dict[str, Any]]:
    """
    Extract list of row dicts from Reddit report response. Tries common shapes:
    - data as list, data.rows, data.data, data.records, data.results; top-level rows.
    If data contains a download URL, get_url_fn(url) is called and the result parsed.
    """

    def _rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [_normalize_row_keys(r) for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            for key in ("rows", "data", "records", "results"):
                arr = payload.get(key)
                if isinstance(arr, list):
                    return [_normalize_row_keys(r) for r in arr if isinstance(r, dict)]
        return []

    log = log or logger
    data = report_resp.get("data")
    if isinstance(data, list):
        return _rows_from_payload(data)
    if isinstance(data, dict):
        rows = _rows_from_payload(data)
        if rows:
            return rows
        # Reddit v3 can return data.metrics as list of rows or aggregate dict
        metrics = data.get("metrics")
        if isinstance(metrics, list):
            return [_normalize_row_keys(r) for r in metrics if isinstance(r, dict)]
        if isinstance(metrics, dict):
            return [_normalize_row_keys(metrics)]
        url = data.get("url") or data.get("download_url") or data.get("file_url")
        if url and get_url_fn and callable(get_url_fn):
            try:
                downloaded = get_url_fn(url)
                if isinstance(downloaded, list):
                    return [_normalize_row_keys(r) for r in downloaded if isinstance(r, dict)]
                if isinstance(downloaded, dict):
                    return _rows_from_payload(downloaded)
            except Exception as e:
                log.warning("Reddit report download failed: %s", e)
    for key in ("rows", "report_data", "report_rows"):
        arr = report_resp.get(key)
        if isinstance(arr, list):
            return [_normalize_row_keys(r) for r in arr if isinstance(r, dict)]
    if data is not None:
        log.info(
            "Reddit report complete but no list found; top-level keys=%s, data keys=%s",
            list(report_resp.keys()),
            list(data.keys()) if isinstance(data, dict) else None,
        )
    return []


class RedditAuthError(RuntimeError):
    def __init__(self, message: str, detail: Optional[str] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.detail = detail
        self.response_body = response_body


class RedditApiError(RuntimeError):
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
        self.operation = operation


@dataclass(frozen=True)
class RedditConfig:
    client_id: str
    client_secret: str
    refresh_token: str


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
    """Load a cached access token from GCS if present (stored by reddit_exchange_code)."""
    try:
        from bigas.resources.marketing.storage_service import StorageService

        storage = StorageService()
        obj = storage.get_json("secrets/reddit/access_token.json") or {}
        token = (obj.get("access_token") or "").strip()
        expires_at = int(obj.get("expires_at") or 0)
        if not token or not expires_at:
            logger.info(
                "Reddit GCS token cache miss or incomplete at secrets/reddit/access_token.json"
            )
            return None

        _gcs_token_cache["access_token"] = token
        _gcs_token_cache["expires_at"] = expires_at
        logger.info("Reddit access token loaded from GCS cache; expires_at=%s", expires_at)
        return token
    except Exception as e:
        logger.warning("Failed to load Reddit access token from GCS: %s", str(e))
        return None


class RedditAdsService:
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ):
        client_id = (client_id or os.environ.get("REDDIT_CLIENT_ID") or "").strip()
        client_secret = (
            client_secret or os.environ.get("REDDIT_CLIENT_SECRET") or ""
        ).strip()
        refresh_token = (
            refresh_token or os.environ.get("REDDIT_REFRESH_TOKEN") or ""
        ).strip()

        if not client_id:
            raise ValueError("REDDIT_CLIENT_ID is required")
        if not client_secret:
            raise ValueError("REDDIT_CLIENT_SECRET is required")
        if not refresh_token:
            raise ValueError("REDDIT_REFRESH_TOKEN is required")

        self.config = RedditConfig(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )

    def _mint_access_token(self) -> str:
        """Mint a Reddit access token using refresh token. Caches in-memory and optionally GCS."""
        manual_token = (os.environ.get("REDDIT_ACCESS_TOKEN") or "").strip()
        if manual_token:
            return manual_token

        now = int(time.time())
        cached = _token_cache.get("access_token")
        expires_at = int(_token_cache.get("expires_at") or 0)
        if cached and expires_at - now > 60:
            return cached

        gcs_cached = _gcs_token_cache.get("access_token")
        gcs_expires_at = int(_gcs_token_cache.get("expires_at") or 0)
        last_checked = int(_gcs_token_cache.get("last_checked_at") or 0)
        if gcs_cached and gcs_expires_at - now > 60:
            return gcs_cached
        if now - last_checked > 60:
            _gcs_token_cache["last_checked_at"] = now
            token = _load_access_token_from_gcs()
            if token and int(_gcs_token_cache.get("expires_at") or 0) - now > 60:
                return token

        basic_auth = base64.b64encode(
            f"{self.config.client_id}:{self.config.client_secret}".encode()
        ).decode()
        # Reddit OAuth2 wiki: refresh request has ONLY grant_type and refresh_token (no redirect_uri).
        resp = requests.post(
            REDDIT_OAUTH_TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": REDDIT_USER_AGENT,
                "Authorization": f"Basic {basic_auth}",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.config.refresh_token,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            body = (resp.text or "").strip()
            logger.error(
                "Reddit token mint failed: status=%s body=%s",
                resp.status_code,
                body[:2000],
            )
            detail = None
            if body:
                try:
                    err_json = json.loads(body)
                    if isinstance(err_json, dict):
                        err_code = err_json.get("error")
                        err_desc = err_json.get("error_description") or err_json.get("message")
                        if err_desc:
                            detail = err_desc if isinstance(err_desc, str) else str(err_desc)
                        if err_code is not None and detail is None:
                            detail = str(err_code) if isinstance(err_code, str) else f"error_code:{err_code}"
                        if detail is None:
                            detail = json.dumps({k: v for k, v in err_json.items() if k in ("error", "error_description", "message")})[:500]
                except Exception:
                    detail = body[:500] if len(body) <= 500 else body[:500] + "..."
            safe_body = body[:800] if body else None
            raise RedditAuthError(
                f"Failed to mint Reddit access token ({resp.status_code})",
                detail=detail,
                response_body=safe_body,
            )

        payload = resp.json()
        access_token = payload.get("access_token")
        expires_in = int(payload.get("expires_in") or 0)
        if not access_token:
            raise RedditAuthError("Reddit token response missing access_token")

        _token_cache["access_token"] = access_token
        _token_cache["expires_at"] = now + max(expires_in, 0)
        return access_token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._mint_access_token()}",
            "User-Agent": REDDIT_USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{REDDIT_ADS_API_BASE}{path}"
        resp = requests.get(
            url,
            headers=self._headers(),
            params=params,
            timeout=60,
        )
        if resp.status_code >= 400:
            raise RedditApiError(
                f"Reddit Ads API error ({resp.status_code})",
                status_code=resp.status_code,
                response_text=resp.text,
                operation=path,
            )
        return resp.json() if resp.text else {}

    def _post(self, path: str, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{REDDIT_ADS_API_BASE}{path}"
        resp = requests.post(
            url,
            headers=self._headers(),
            json=json_body or {},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise RedditApiError(
                f"Reddit Ads API error ({resp.status_code})",
                status_code=resp.status_code,
                response_text=resp.text,
                operation=path,
            )
        return resp.json() if resp.text else {}

    def _get_url(self, url: str) -> Dict[str, Any]:
        """GET absolute URL with same auth (e.g. report download URL)."""
        resp = requests.get(url, headers=self._headers(), timeout=120)
        if resp.status_code >= 400:
            raise RedditApiError(
                f"Reddit report download error ({resp.status_code})",
                status_code=resp.status_code,
                response_text=resp.text,
                operation=url,
            )
        return resp.json() if resp.text else {}

    def get_me(self) -> Dict[str, Any]:
        """Get current user/profile (v3 Get Me). Tries /me then /users/me."""
        try:
            return self._get("/me")
        except RedditApiError as e:
            if e.status_code == 404:
                return self._get("/users/me")
            raise

    def list_my_businesses(self) -> Dict[str, Any]:
        """List businesses for the current user (v3)."""
        return self._get("/me/businesses")

    def list_ad_accounts_by_business(self, business_id: str) -> Dict[str, Any]:
        """List ad accounts for a business (v3 List Ad Accounts By Business)."""
        return self._get(f"/businesses/{business_id}/ad_accounts")

    def list_ad_accounts(self, business_id: Optional[str] = None) -> Dict[str, Any]:
        """
        List ad accounts. If business_id is set, list by business; otherwise
        get /me, then /me/businesses, then list ad accounts for first business.
        """
        if business_id:
            return self.list_ad_accounts_by_business(business_id)
        # v3: no standalone GET /ad_accounts; use Get Me -> list_my_businesses -> list by business
        me = self.get_me()
        businesses_resp = self.list_my_businesses()
        businesses = businesses_resp.get("data") or businesses_resp.get("businesses") or []
        if not isinstance(businesses, list) or not businesses:
            return {"data": [], "me": me, "businesses": []}
        first_biz = businesses[0] if isinstance(businesses[0], dict) else {"id": businesses[0]}
        biz_id = first_biz.get("id") if isinstance(first_biz, dict) else str(businesses[0])
        if not biz_id:
            return {"data": [], "me": me, "businesses": businesses}
        return self.list_ad_accounts_by_business(str(biz_id))

    def get_performance_report(
        self,
        account_id: str,
        start_date: date,
        end_date: date,
        dimensions: Optional[List[str]] = None,
        metrics: Optional[List[str]] = None,
        poll_interval_sec: int = 10,
        max_wait_sec: int = 300,
    ) -> Dict[str, Any]:
        """
        Request a performance report for the given ad account and date range,
        then poll until ready and return the report data.

        Reddit Ads API v3: create report (POST), then GET report until status is complete.
        Default dimensions/metrics align with normalized schema (impressions, clicks, spend, etc.).

        Returns a dict with keys suitable for normalization:
          - data: list of rows, each with segment/dimension fields and metric fields
          - raw_response: full API response for storage
        """
        # Reddit Ads API v3 expects uppercase field names from their enum (not all metrics exist).
        dimensions = dimensions or ["CAMPAIGN_ID", "AD_ID"]
        metrics = metrics or [
            "IMPRESSIONS",
            "CLICKS",
            "SPEND",
            "REACH",
        ]
        # Normalize to uppercase if caller passed lowercase.
        dimensions = [str(d).upper() for d in dimensions]
        metrics = [str(m).upper() for m in metrics]

        # Reddit Ads API v3 report: POST to create report job. Date-times must be hourly: YYYY-MM-DDTHH:00:00Z.
        # Include breakdowns so the API returns per-dimension rows (e.g. per campaign, per ad) instead of a single aggregate.
        from datetime import datetime as dt
        starts_at = dt.combine(start_date, dt.min.time()).strftime("%Y-%m-%dT00:00:00Z")
        ends_at = dt.combine(end_date, dt.min.time()).strftime("%Y-%m-%dT23:00:00Z")
        fields = list(dimensions) + list(metrics)
        body = {
            "data": {
                "starts_at": starts_at,
                "ends_at": ends_at,
                "fields": fields,
                "breakdowns": dimensions,
            }
        }
        create_resp = self._post(f"/ad_accounts/{account_id}/reports", json_body=body)

        report_id = (
            create_resp.get("id")
            or create_resp.get("report_id")
            or create_resp.get("data", {}).get("id")
        )
        if not report_id:
            logger.warning(
                "Reddit report create response missing id/report_id: %s",
                list(create_resp.keys()),
            )
            # If the API returns report data inline (sync), e.g. data.metrics list/dict, extract it.
            rows = _extract_report_rows(create_resp, log=logger)
            return {"data": rows, "raw_response": create_resp}

        # Poll until report is ready.
        started = time.time()
        while time.time() - started < max_wait_sec:
            report_resp = self._get(
                f"/ad_accounts/{account_id}/reports/{report_id}"
            )
            status = (
                report_resp.get("status")
                or report_resp.get("state")
                or report_resp.get("data", {}).get("status")
            )
            if status in ("complete", "completed", "ready", "done"):
                data = _extract_report_rows(report_resp, get_url_fn=self._get_url, log=logger)
                return {"data": data, "raw_response": report_resp}
            if status in ("failed", "error", "cancelled"):
                raise RedditApiError(
                    f"Reddit report failed with status={status}",
                    response_text=str(report_resp),
                    operation="get_report",
                )
            time.sleep(poll_interval_sec)

        raise RedditApiError(
            "Reddit report did not complete within max_wait_sec",
            operation="get_report",
        )

    def get_audience_report(
        self,
        account_id: str,
        start_date: date,
        end_date: date,
        breakdowns: List[str],
        fields: Optional[List[str]] = None,
        campaign_id: Optional[str] = None,
        append_breakdowns_to_fields: bool = True,
        poll_interval_sec: int = 10,
        max_wait_sec: int = 300,
    ) -> Dict[str, Any]:
        """
        Request an audience/demographics report with breakdowns (e.g. INTEREST, COMMUNITY, COUNTRY, REGION, DMA).
        If campaign_id is set, breakdowns include CAMPAIGN_ID so results are per-campaign; rows are then
        filtered to that campaign so the UI (single-campaign audience) matches.
        """
        from datetime import datetime as dt

        breakdowns = [str(b).upper() for b in breakdowns]
        if campaign_id:
            breakdowns = ["CAMPAIGN_ID"] + breakdowns
        fields = fields or ["REACH", "IMPRESSIONS", "CLICKS", "SPEND"]
        fields = [str(f).upper() for f in fields]
        # Some breakdown enums (e.g. SUBREDDIT) are valid in `breakdowns` but NOT valid in `fields`.
        # Only append breakdowns to fields when explicitly requested.
        if append_breakdowns_to_fields:
            for b in breakdowns:
                if b not in fields:
                    fields.append(b)

        starts_at = dt.combine(start_date, dt.min.time()).strftime("%Y-%m-%dT00:00:00Z")
        ends_at = dt.combine(end_date, dt.min.time()).strftime("%Y-%m-%dT23:00:00Z")
        body = {
            "data": {
                "starts_at": starts_at,
                "ends_at": ends_at,
                "fields": fields,
                "breakdowns": breakdowns,
            }
        }
        create_resp = self._post(f"/ad_accounts/{account_id}/reports", json_body=body)

        report_id = (
            create_resp.get("id")
            or create_resp.get("report_id")
            or create_resp.get("data", {}).get("id")
        )
        if not report_id:
            rows = _extract_report_rows(create_resp, log=logger)
            filtered = _filter_audience_by_campaign(rows, campaign_id)
            return {"data": filtered, "raw_response": create_resp}

        started = time.time()
        while time.time() - started < max_wait_sec:
            report_resp = self._get(
                f"/ad_accounts/{account_id}/reports/{report_id}"
            )
            status = (
                report_resp.get("status")
                or report_resp.get("state")
                or report_resp.get("data", {}).get("status")
            )
            if status in ("complete", "completed", "ready", "done"):
                data = _extract_report_rows(report_resp, get_url_fn=self._get_url, log=logger)
                filtered = _filter_audience_by_campaign(data, campaign_id)
                return {"data": filtered, "raw_response": report_resp}
            if status in ("failed", "error", "cancelled"):
                raise RedditApiError(
                    f"Reddit audience report failed with status={status}",
                    response_text=str(report_resp),
                    operation="get_audience_report",
                )
            time.sleep(poll_interval_sec)

        raise RedditApiError(
            "Reddit audience report did not complete within max_wait_sec",
            operation="get_audience_report",
        )
