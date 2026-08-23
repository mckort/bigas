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

_BACKWARD_TRANSITION_KEYWORDS = frozenset(
    {"back", "reopen", "undo", "previous", "return", "won't do", "wont do", "cancel"}
)


from bigas.jira_exceptions import JiraError


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


_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def normalize_parent_epic_key(
    raw: Optional[str],
    *,
    project_key: Optional[str] = None,
) -> Optional[str]:
    """Return a Jira issue key suitable as an Epic parent, or None.

    Standalone Tasks/Bugs omit parent. Project keys, empty values, and
    non-issue tokens are dropped so agents can create tickets without a parent.
    """
    key = normalize_issue_key(raw)
    if not key:
        return None
    proj = (project_key or "").strip().upper()
    if proj and key == proj:
        return None
    return key


def is_invalid_parent_error(exc: BaseException) -> bool:
    """True when Jira rejected the parent/Epic link on issue create."""
    err_str = str(exc).lower()
    if "valid parent" in err_str:
        return True
    field = _epic_link_field_name().lower()
    return field in err_str or "does not exist" in err_str


def normalize_issue_key(raw: Optional[str]) -> Optional[str]:
    """Return a canonical Jira issue key (e.g. GPWW-3) or None."""
    key = (raw or "").strip().upper()
    if not key or not _ISSUE_KEY_RE.match(key):
        return None
    return key


def _epic_link_field_name() -> str:
    return (os.environ.get("JIRA_EPIC_LINK_FIELD") or "parent").strip() or "parent"


def _browse_url(base_url: str, issue_key: str) -> str:
    key = (issue_key or "").strip()
    root = (base_url or "").rstrip("/")
    if not key or not root:
        return ""
    return f"{root}/browse/{key}"


def _issue_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    fields = raw.get("fields")
    return fields if isinstance(fields, dict) else {}


def _named(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return str(value or "").strip()


def compact_parent_from_fields(
    fields: Dict[str, Any],
    *,
    base_url: str = "",
) -> Optional[Dict[str, Any]]:
    """Extract a compact parent/Epic from Jira issue fields."""
    parent = fields.get("parent")
    if isinstance(parent, dict) and (parent.get("key") or "").strip():
        pfields = parent.get("fields") if isinstance(parent.get("fields"), dict) else {}
        key = str(parent.get("key") or "").strip().upper()
        return {
            "key": key,
            "summary": str(pfields.get("summary") or "").strip(),
            "issue_type": _named(pfields.get("issuetype")),
            "status": _named(pfields.get("status")),
            "url": _browse_url(base_url, key),
        }
    field = _epic_link_field_name()
    if field.lower() == "parent":
        return None
    raw = fields.get(field)
    if isinstance(raw, dict) and (raw.get("key") or "").strip():
        key = str(raw.get("key") or "").strip().upper()
        return {
            "key": key,
            "summary": str(raw.get("summary") or "").strip(),
            "issue_type": _named(raw.get("issuetype")) or "Epic",
            "status": _named(raw.get("status")),
            "url": _browse_url(base_url, key),
        }
    if isinstance(raw, str) and normalize_issue_key(raw):
        key = normalize_issue_key(raw) or ""
        return {
            "key": key,
            "summary": "",
            "issue_type": "Epic",
            "status": "",
            "url": _browse_url(base_url, key),
        }
    return None


def compact_jira_issue(
    raw: Dict[str, Any],
    *,
    base_url: str = "",
) -> Dict[str, Any]:
    """Shrink a Jira issue payload to keys an agent can reason about."""
    fields = _issue_fields(raw)
    key = str(raw.get("key") or fields.get("key") or "").strip().upper()
    project = fields.get("project") if isinstance(fields.get("project"), dict) else {}
    out: Dict[str, Any] = {
        "key": key,
        "summary": str(fields.get("summary") or "").strip(),
        "issue_type": _named(fields.get("issuetype")),
        "status": _named(fields.get("status")),
        "project_key": str(project.get("key") or "").strip().upper(),
        "url": _browse_url(base_url, key),
    }
    parent = compact_parent_from_fields(fields, base_url=base_url)
    if parent:
        out["parent"] = parent
        if (parent.get("issue_type") or "").strip().lower() == "epic":
            out["parent_epic_key"] = parent["key"]
    return out


def issue_lookup_fields() -> List[str]:
    fields = ["summary", "status", "issuetype", "parent", "project"]
    extra = _epic_link_field_name()
    if extra.lower() != "parent" and extra not in fields:
        fields.append(extra)
    return fields


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

    def get_issue(
        self,
        issue_key: str,
        *,
        fields: Optional[List[str]] = None,
        expand: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch a single issue by key."""
        key = (issue_key or "").strip()
        if not key:
            raise JiraError("issue_key is required")
        params: Dict[str, str] = {}
        if fields:
            params["fields"] = ",".join(fields)
        if expand:
            params["expand"] = expand
        url = f"{self._config.base_url}/rest/api/3/issue/{key}"
        return self._request_with_retry_429("GET", url, params=params)

    def search_issues_by_keys(
        self,
        issue_keys: Sequence[str],
        *,
        fields: Optional[List[str]] = None,
        max_results_per_page: int = 50,
        max_pages: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch multiple issues in one JQL search."""
        keys = [k for k in (normalize_issue_key(key) for key in issue_keys) if k]
        if not keys:
            return []
        quoted = ", ".join(f'"{key}"' for key in keys)
        jql = f"issueKey in ({quoted}) ORDER BY key ASC"
        return self._search_jql(
            jql=jql,
            fields=fields or issue_lookup_fields(),
            max_results_per_page=max_results_per_page,
            max_pages=max_pages,
        )

    def epic_jql_clause(self, epic_key: str) -> str:
        """Build a JQL fragment linking child issues to an Epic."""
        key = (epic_key or "").strip()
        if not key:
            raise JiraError("epic_key is required")
        field = (os.environ.get("JIRA_EPIC_JQL_FIELD") or "parent").strip() or "parent"
        if field.lower() == "parent":
            return f'parent = "{key}"'
        return f'"{field}" = "{key}"'

    def _apply_epic_link(self, fields: Dict[str, Any], parent_epic_key: Optional[str]) -> None:
        epic_key = (parent_epic_key or "").strip()
        if not epic_key:
            return
        field = _epic_link_field_name()
        if field.lower() == "parent":
            fields["parent"] = {"key": epic_key}
        else:
            fields[field] = epic_key

    def _drop_epic_link(self, fields: Dict[str, Any]) -> None:
        field = _epic_link_field_name()
        fields.pop("parent", None)
        fields.pop("parentId", None)
        if field.lower() != "parent":
            fields.pop(field, None)

    def get_epics_by_statuses(
        self,
        *,
        statuses: Sequence[str],
        project_keys: Optional[Union[str, Sequence[str]]] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch Epic issues whose workflow status is in ``statuses``."""
        status_names = [str(s).strip() for s in (statuses or []) if str(s).strip()]
        if not status_names:
            return []
        if fields is None:
            fields = ["key", "summary", "status", "description", "project", "issuetype"]
        keys = self._resolve_project_keys(project_keys)
        quoted_statuses = ", ".join(f'"{s}"' for s in status_names)
        jql = (
            f'{project_jql_clause(keys)} '
            f'AND issuetype = Epic '
            f"AND status in ({quoted_statuses}) "
            f"ORDER BY updated DESC, key ASC"
        )
        return self._search_jql(
            jql=jql,
            fields=fields,
            max_results_per_page=50,
            max_pages=20,
        )

    def list_open_epics(
        self,
        project_keys: Optional[Union[str, Sequence[str]]] = None,
        *,
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """Open (non-Done) Epics in a project, compacted for agents."""
        keys = self._resolve_project_keys(project_keys)
        jql = (
            f"{project_jql_clause(keys)} "
            "AND issuetype = Epic "
            "AND statusCategory != Done "
            "ORDER BY key ASC"
        )
        raw = self._search_jql(
            jql=jql,
            fields=["summary", "status", "issuetype", "project"],
            max_results_per_page=max(1, min(int(max_results), 50)),
            max_pages=1,
        )
        return [
            compact_jira_issue(issue, base_url=self._config.base_url) for issue in raw
        ]

    def get_issues_for_epic(
        self,
        epic_key: str,
        *,
        status_clause: str = "",
        updated_since_days: Optional[int] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch issues linked to an Epic via parent or Epic Link (see env JIRA_EPIC_JQL_FIELD).

        status_clause: optional JQL fragment, e.g. 'AND status != Done'.
        updated_since_days: when set, restrict to issues updated in the last N days.
        """
        if fields is None:
            fields = ["key", "summary", "status", "issuetype", "resolutiondate", "updated"]
        jql = self.epic_jql_clause(epic_key)
        if (status_clause or "").strip():
            jql = f"{jql} {(status_clause or '').strip()}"
        if updated_since_days is not None:
            days = int(updated_since_days)
            if days < 1 or days > 365:
                raise ValueError("updated_since_days must be between 1 and 365")
            jql = f"{jql} AND updated >= -{days}d"
        jql = f"{jql} ORDER BY updated DESC, key ASC"
        return self._search_jql(
            jql=jql,
            fields=fields,
            max_results_per_page=50,
            max_pages=50,
        )

    def create_issue(
        self,
        *,
        summary: str,
        description_markdown: str = "",
        project_key: Optional[str] = None,
        issue_type: str = "Task",
        labels: Optional[List[str]] = None,
        parent_epic_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a Jira issue and return key + browse URL."""
        title = (summary or "").strip()
        if not title:
            raise JiraError("summary is required")
        keys = self._resolve_project_keys(project_key)
        if not keys:
            raise JiraError("project_key is required")
        proj = keys[0]
        url = f"{self._config.base_url}/rest/api/3/issue"
        fields: Dict[str, Any] = {
            "project": {"key": proj},
            "summary": title[:255],
            "issuetype": {"name": (issue_type or "Task").strip() or "Task"},
            "description": markdown_to_adf(description_markdown or ""),
        }
        if labels:
            fields["labels"] = [str(l).strip() for l in labels if str(l).strip()]
        epic_key = normalize_parent_epic_key(parent_epic_key, project_key=proj)
        self._apply_epic_link(fields, epic_key)
        payload = {
            "fields": fields,
        }
        parent_dropped = False
        try:
            data = self._request_with_retry_429("POST", url, json=payload)
        except JiraError as e:
            if not epic_key or not is_invalid_parent_error(e):
                raise
            logger.warning(
                "Invalid Jira parent %s; creating issue without parent", epic_key
            )
            self._drop_epic_link(fields)
            data = self._request_with_retry_429("POST", url, json={"fields": fields})
            parent_dropped = True
        issue_key = (data.get("key") or "").strip()
        issue_id = (data.get("id") or "").strip()
        browse_url = f"{self._config.base_url}/browse/{issue_key}" if issue_key else ""
        out: Dict[str, Any] = {
            "ok": True,
            "key": issue_key,
            "id": issue_id,
            "url": browse_url,
            "self": data.get("self"),
        }
        if epic_key and not parent_dropped:
            out["parent_epic_key"] = epic_key
        if parent_dropped:
            out["parent_dropped"] = True
        return out

    def add_comment(self, issue_key: str, body_text: str) -> Dict[str, Any]:
        """Add a plain-text comment (ADF doc with one paragraph)."""
        key = (issue_key or "").strip()
        text = (body_text or "").strip()
        if not key:
            raise JiraError("issue_key is required")
        if not text:
            raise JiraError("comment body is required")
        url = f"{self._config.base_url}/rest/api/3/issue/{key}/comment"
        payload = {"body": _text_to_adf(text)}
        return self._request_with_retry_429("POST", url, json=payload)

    def list_comments(
        self,
        issue_key: str,
        *,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return issue comments (oldest first), up to max_results."""
        key = (issue_key or "").strip()
        if not key:
            raise JiraError("issue_key is required")
        url = f"{self._config.base_url}/rest/api/3/issue/{key}/comment"
        data = self._request_with_retry_429(
            "GET",
            url,
            params={"maxResults": str(max(1, min(int(max_results), 100)))},
        )
        return list(data.get("comments") or [])

    def update_description(self, issue_key: str, description_markdown: str) -> None:
        """Replace issue description with markdown converted to ADF."""
        key = (issue_key or "").strip()
        if not key:
            raise JiraError("issue_key is required")
        url = f"{self._config.base_url}/rest/api/3/issue/{key}"
        payload = {
            "fields": {
                "description": markdown_to_adf(description_markdown or ""),
            }
        }
        self._request_with_retry_429("PUT", url, json=payload, expect_json=False)

    def list_transitions(self, issue_key: str) -> List[Dict[str, Any]]:
        key = (issue_key or "").strip()
        if not key:
            raise JiraError("issue_key is required")
        url = f"{self._config.base_url}/rest/api/3/issue/{key}/transitions"
        data = self._request_with_retry_429("GET", url)
        return list(data.get("transitions") or [])

    def transition_issue_to_next(self, issue_key: str) -> Dict[str, Any]:
        """
        Move an issue to the next logical workflow column.

        Picks the first forward transition, skipping obvious backward moves
        (reopen, undo, etc.). Falls back to the first available transition.
        """
        key = (issue_key or "").strip()
        if not key:
            raise JiraError("issue_key is required")

        issue = self.get_issue(key, fields=["status", "summary"])
        fields = issue.get("fields") or {}
        current_status = ((fields.get("status") or {}).get("name") or "").strip()
        summary = (fields.get("summary") or key).strip()

        transitions = self.list_transitions(key)
        if not transitions:
            raise JiraError(f"No transitions available for {key}")

        def is_backward(transition: Dict[str, Any]) -> bool:
            name = (transition.get("name") or "").lower()
            return any(kw in name for kw in _BACKWARD_TRANSITION_KEYWORDS)

        forward = [t for t in transitions if not is_backward(t)]
        chosen = forward[0] if forward else transitions[0]
        to_status = ((chosen.get("to") or {}).get("name") or "").strip()
        if not to_status:
            raise JiraError(f"Could not resolve target status for {key}")

        transition_id = str(chosen.get("id") or "")
        if not transition_id:
            raise JiraError(f"Invalid transition for {key}")

        payload: Dict[str, Any] = {"transition": {"id": transition_id}}
        url = f"{self._config.base_url}/rest/api/3/issue/{key}/transitions"
        self._request_with_retry_429("POST", url, json=payload, expect_json=False)

        browse_url = f"{self._config.base_url}/browse/{key}"
        return {
            "ok": True,
            "issue_key": key,
            "summary": summary,
            "url": browse_url,
            "previous_status": current_status,
            "new_status": to_status,
            "message": f"Moved {key} to {to_status}",
        }

    def transition_issue(
        self,
        issue_key: str,
        *,
        to_status_name: str,
        comment: Optional[str] = None,
    ) -> None:
        """
        Transition an issue to a status by matching transition target status name
        (case-insensitive). Optionally adds a comment in the same transition.
        """
        key = (issue_key or "").strip()
        target = (to_status_name or "").strip()
        if not key or not target:
            raise JiraError("issue_key and to_status_name are required")

        transitions = self.list_transitions(key)
        match = None
        target_l = target.lower()
        for t in transitions:
            to_status = ((t.get("to") or {}).get("name") or "").strip()
            name = (t.get("name") or "").strip()
            if to_status.lower() == target_l or name.lower() == target_l:
                match = t
                break
        if not match:
            available = [
                f"{(t.get('name') or '?')} -> {((t.get('to') or {}).get('name') or '?')}"
                for t in transitions
            ]
            raise JiraError(
                f"No transition to status {target!r} for {key}. Available: {available}"
            )

        payload: Dict[str, Any] = {"transition": {"id": str(match["id"])}}
        if (comment or "").strip():
            payload["update"] = {
                "comment": [
                    {
                        "add": {
                            "body": _text_to_adf(comment.strip()),
                        }
                    }
                ]
            }
        url = f"{self._config.base_url}/rest/api/3/issue/{key}/transitions"
        self._request_with_retry_429("POST", url, json=payload, expect_json=False)

    def _post_with_retry_429(self, url: str, *, json: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_with_retry_429("POST", url, json=json)

    def _request_with_retry_429(
        self,
        method: str,
        url: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        expect_json: bool = True,
    ) -> Dict[str, Any]:
        backoff_s = 1.0
        max_attempts = 5

        for attempt in range(1, max_attempts + 1):
            resp = self._session.request(
                method,
                url,
                json=json,
                params=params,
                timeout=self._timeout_s,
            )

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

            if not expect_json or resp.status_code == 204 or not (resp.text or "").strip():
                return {}

            try:
                return resp.json()
            except Exception as e:
                raise JiraError(f"Failed to parse Jira response as JSON: {e}")

        raise JiraError("Jira API rate limit persisted (429). Please try again later.")


def adf_to_plain_text(node: Any) -> str:
    """
    Extract markdown-ish plain text from an Atlassian Document Format node.

    Preserves heading markers (`#` / `##` / `###`) and bullet prefixes (`- `)
    so description section contracts (e.g. `## Brief`) survive Jira round-trips.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node

    def inline_text(n: Any) -> str:
        if isinstance(n, str):
            return n
        if isinstance(n, list):
            return "".join(inline_text(c) for c in n)
        if not isinstance(n, dict):
            return ""
        t = n.get("type")
        if t == "text":
            return n.get("text") or ""
        if t == "hardBreak":
            return "\n"
        return "".join(inline_text(c) for c in (n.get("content") or []))

    def list_item_lines(
        item: Any, *, ordered: bool = False, index: int = 1
    ) -> List[str]:
        if not isinstance(item, dict):
            return []
        prefix = f"{index}. " if ordered else "- "
        content = item.get("content") or []
        lines: List[str] = []
        first = True
        for child in content:
            if not isinstance(child, dict):
                continue
            ct = child.get("type")
            if ct == "paragraph":
                text = inline_text(child.get("content") or []).strip()
                if first:
                    lines.append(f"{prefix}{text}" if text else prefix.rstrip())
                    first = False
                elif text:
                    lines.append(f"  {text}")
            elif ct in ("bulletList", "orderedList"):
                nested = block_lines(child)
                lines.extend(f"  {ln}" if ln else ln for ln in nested)
            else:
                nested = block_lines(child)
                if first and nested:
                    lines.append(f"{prefix}{nested[0].lstrip()}")
                    lines.extend(nested[1:])
                    first = False
                else:
                    lines.extend(nested)
        if first:
            lines.append(prefix.rstrip())
        return lines

    def block_lines(n: Any) -> List[str]:
        if isinstance(n, list):
            out: List[str] = []
            for c in n:
                out.extend(block_lines(c))
            return out
        if not isinstance(n, dict):
            return []
        t = n.get("type")
        content = n.get("content") or []
        if t == "doc":
            return block_lines(content)
        if t == "paragraph":
            text = inline_text(content).rstrip()
            return [text]
        if t == "heading":
            level = int((n.get("attrs") or {}).get("level") or 1)
            level = max(1, min(level, 6))
            prefix = "#" * level + " "
            return [prefix + inline_text(content).strip()]
        if t == "bulletList":
            lines: List[str] = []
            for item in content:
                lines.extend(list_item_lines(item, ordered=False))
            return lines
        if t == "orderedList":
            lines = []
            for i, item in enumerate(content, 1):
                lines.extend(list_item_lines(item, ordered=True, index=i))
            return lines
        if t == "listItem":
            return list_item_lines(n, ordered=False)
        if t == "blockquote":
            return [f"> {ln}" if ln else ">" for ln in block_lines(content)]
        if t == "codeBlock":
            body = inline_text(content)
            return ["```", body, "```"] if body else ["```", "```"]
        if t == "rule":
            return ["---"]
        return block_lines(content)

    lines = block_lines(node)
    # Collapse runs of blank lines to a single blank line for stable briefs
    cleaned: List[str] = []
    prev_blank = False
    for ln in lines:
        blank = not (ln or "").strip()
        if blank and prev_blank:
            continue
        cleaned.append(ln)
        prev_blank = blank
    return "\n".join(cleaned).strip()


def _text_to_adf(text: str) -> Dict[str, Any]:
    """Single-paragraph ADF doc (for comments)."""
    lines = (text or "").split("\n")
    content: List[Dict[str, Any]] = []
    for line in lines:
        content.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}] if line else [],
            }
        )
    if not content:
        content = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": content}


def markdown_to_adf(markdown: str) -> Dict[str, Any]:
    """
    Minimal markdown → ADF for descriptions (headings, paragraphs, bullets).
    Good enough for Brief + AI sections; not a full markdown parser.
    """
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    content: List[Dict[str, Any]] = []
    bullet_items: List[Dict[str, Any]] = []

    def flush_bullets() -> None:
        nonlocal bullet_items
        if not bullet_items:
            return
        content.append({"type": "bulletList", "content": bullet_items})
        bullet_items = []

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_bullets()
            continue
        if line.startswith("### "):
            flush_bullets()
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": line[4:].strip()}],
                }
            )
        elif line.startswith("## "):
            flush_bullets()
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": line[3:].strip()}],
                }
            )
        elif line.startswith("# "):
            flush_bullets()
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": line[2:].strip()}],
                }
            )
        elif line.lstrip().startswith("- ") or line.lstrip().startswith("* "):
            item_text = line.lstrip()[2:].strip()
            bullet_items.append(
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": item_text}],
                        }
                    ],
                }
            )
        else:
            flush_bullets()
            content.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": line}],
                }
            )

    flush_bullets()
    if not content:
        content = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": content}
