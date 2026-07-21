from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union
import os
import re
import time
import logging

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class JiraError(RuntimeError):
    pass


def parse_project_keys(raw: Optional[str]) -> List[str]:
    """
    Parse one or more Jira project keys from env/request strings.
    Accepts comma / semicolon / whitespace separators, e.g. "VFA,WAYW" or "VFA WAYW".
    """
    if not raw:
        return []
    parts = re.split(r"[\s,;]+", raw.strip())
    keys: List[str] = []
    seen = set()
    for part in parts:
        key = part.strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def normalize_project_keys(
    value: Optional[Union[str, Sequence[str]]],
) -> List[str]:
    """Normalize request-body project_key / project_keys into a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return parse_project_keys(value)
    keys: List[str] = []
    seen = set()
    for item in value:
        for key in parse_project_keys(str(item)):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def project_jql_clause(project_keys: Sequence[str]) -> str:
    """Build a JQL project clause for one or many keys."""
    keys = [k.strip() for k in project_keys if (k or "").strip()]
    if not keys:
        raise JiraError("At least one Jira project key is required.")
    if len(keys) == 1:
        return f'project = "{keys[0]}"'
    quoted = ", ".join(f'"{k}"' for k in keys)
    return f"project in ({quoted})"


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    api_token: str
    project_keys: tuple[str, ...]

    @property
    def project_key(self) -> str:
        """First configured project key (backward-compatible single-project accessor)."""
        return self.project_keys[0]

    @staticmethod
    def from_env() -> "JiraConfig":
        base_url = (os.environ.get("JIRA_BASE_URL") or "").strip().rstrip("/")
        email = (os.environ.get("JIRA_EMAIL") or "").strip()
        api_token = (os.environ.get("JIRA_API_TOKEN") or "").strip()
        # Prefer JIRA_PROJECT_KEYS; fall back to JIRA_PROJECT_KEY (supports "VFA,WAYW").
        raw_keys = (
            os.environ.get("JIRA_PROJECT_KEYS")
            or os.environ.get("JIRA_PROJECT_KEY")
            or ""
        ).strip()
        project_keys = tuple(parse_project_keys(raw_keys))

        missing = [k for k, v in {
            "JIRA_BASE_URL": base_url,
            "JIRA_EMAIL": email,
            "JIRA_API_TOKEN": api_token,
            "JIRA_PROJECT_KEY": ",".join(project_keys),
        }.items() if not v]

        if missing:
            raise JiraError(f"Missing required Jira env vars: {', '.join(missing)}")

        return JiraConfig(
            base_url=base_url,
            email=email,
            api_token=api_token,
            project_keys=project_keys,
        )


class JiraClient:
    def __init__(self, config: JiraConfig, *, timeout_s: int = 30):
        self._config = config
        self._timeout_s = timeout_s
        self._session = requests.Session()
        self._session.auth = HTTPBasicAuth(config.email, config.api_token)

        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _resolve_project_keys(
        self,
        project_keys: Optional[Union[str, Sequence[str]]] = None,
    ) -> List[str]:
        override = normalize_project_keys(project_keys)
        if override:
            return override
        return list(self._config.project_keys)

    def search_issues_by_fix_version(
        self,
        *,
        fix_version: str,
        jql_extra: str = "",
        project_keys: Optional[Union[str, Sequence[str]]] = None,
        fields: Optional[List[str]] = None,
        max_results_per_page: int = 50,
        max_pages: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Query Jira Cloud search API for issues matching fixVersion.

        Note: Jira Cloud deprecated and removed /rest/api/3/search (410).
        We use /rest/api/3/search/jql which paginates using nextPageToken.

        jql_extra: optional JQL fragment appended to the query (e.g. "AND statusCategory = Done").
        project_keys: optional override; defaults to configured JIRA_PROJECT_KEY(S).
        """
        if fields is None:
            fields = [
                "key",
                "summary",
                "issuetype",
                "priority",
                "components",
                "labels",
                "fixVersions",
                "status",
                "resolutiondate",
            ]

        keys = self._resolve_project_keys(project_keys)
        jql = (
            f'{project_jql_clause(keys)} '
            f'AND fixVersion = "{fix_version}" '
        )
        if (jql_extra or "").strip():
            jql = f"{jql} {(jql_extra or '').strip()} "

        jql = f"{jql} ORDER BY issuetype ASC, priority DESC, key ASC"

        return self._search_jql(
            jql=jql,
            fields=fields,
            max_results_per_page=max_results_per_page,
            max_pages=max_pages,
        )

    def search_issues_done_in_last_n_days(
        self,
        *,
        days: int = 14,
        jql_extra: str = "",
        project_keys: Optional[Union[str, Sequence[str]]] = None,
        fields: Optional[List[str]] = None,
        max_results_per_page: int = 50,
        max_pages: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Query Jira for issues that are in Done and were resolved (or updated) in the last N days.
        Uses resolutiondate when set; otherwise falls back to updated date.

        jql_extra: optional JQL fragment appended to the query (e.g. "AND statusCategory = Done").
        project_keys: optional override; defaults to configured JIRA_PROJECT_KEY(S).
        """
        if days < 1 or days > 365:
            raise ValueError("days must be between 1 and 365")
        if fields is None:
            fields = [
                "key",
                "summary",
                "issuetype",
                "status",
                "resolutiondate",
                "assignee",
                "updated",
            ]

        keys = self._resolve_project_keys(project_keys)
        # resolutiondate is set when issue is resolved/done; fallback to updated for boards that don't set it
        jql = (
            f'{project_jql_clause(keys)} '
            f'AND status = Done '
            f'AND (resolutiondate >= -{days}d OR (resolutiondate is EMPTY AND updated >= -{days}d)) '
        )
        if (jql_extra or "").strip():
            jql = f"{jql} {(jql_extra or '').strip()} "
        jql = f"{jql} ORDER BY resolutiondate DESC, updated DESC, key ASC"

        return self._search_jql(
            jql=jql,
            fields=fields,
            max_results_per_page=max_results_per_page,
            max_pages=max_pages,
        )

    def _search_jql(
        self,
        *,
        jql: str,
        fields: List[str],
        max_results_per_page: int,
        max_pages: int,
    ) -> List[Dict[str, Any]]:
        url = f"{self._config.base_url}/rest/api/3/search/jql"
        all_issues: List[Dict[str, Any]] = []
        next_page_token: Optional[str] = None

        for _page in range(max_pages):
            payload = {
                "jql": jql,
                "maxResults": max_results_per_page,
                "fields": fields,
            }
            if next_page_token:
                payload["nextPageToken"] = next_page_token

            data = self._post_with_retry_429(url, json=payload)

            issues = data.get("issues", []) or []
            all_issues.extend(issues)

            is_last = bool(data.get("isLast", False))
            next_page_token = data.get("nextPageToken") or None
            if is_last or not next_page_token or not issues:
                break

        return all_issues

    def _post_with_retry_429(self, url: str, *, json: Dict[str, Any]) -> Dict[str, Any]:
        backoff_s = 1.0
        max_attempts = 5

        for attempt in range(1, max_attempts + 1):
            resp = self._session.post(url, json=json, timeout=self._timeout_s)

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                sleep_s = float(retry_after) if retry_after and retry_after.isdigit() else backoff_s
                sleep_s = min(sleep_s, 10.0)
                logger.warning(f"Jira rate limited (429). Sleeping {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                time.sleep(sleep_s)
                backoff_s = min(backoff_s * 2, 10.0)
                continue

            if resp.status_code in (401, 403):
                raise JiraError("Jira authentication/authorization failed (check JIRA_EMAIL/JIRA_API_TOKEN).")

            if resp.status_code >= 400:
                # Avoid leaking payload (may include project key / version); keep concise
                raise JiraError(f"Jira API error {resp.status_code}: {resp.text[:500]}")

            try:
                return resp.json()
            except Exception as e:
                raise JiraError(f"Failed to parse Jira response as JSON: {e}")

        raise JiraError("Jira API rate limit persisted (429). Please try again later.")
