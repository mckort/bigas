from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    compact_jira_issue,
    issue_lookup_fields,
    normalize_issue_key,
    normalize_project_keys,
)

MAX_LOOKUP_KEYS = 40

_ISSUE_KEY_SEARCH_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b", re.IGNORECASE)
_RANGE_RE = re.compile(
    r"\b([A-Z][A-Z0-9]+)-(\d+)\s*(?:to|through|–|—|-|\.\.)\s*(?:([A-Z][A-Z0-9]+)-)?(\d+)\b",
    re.IGNORECASE,
)


class LookupJiraError(RuntimeError):
    pass


def _format_jira_error(exc: JiraError, *, issue_key: str = "", project_key: str = "") -> str:
    msg = str(exc)
    lower = msg.lower()
    if "404" in msg or "does not exist" in lower or "not found" in lower:
        if issue_key:
            return f"Issue {issue_key!r} not found or not accessible."
        if project_key:
            return f"Project key {project_key!r} not found or not accessible."
    if "401" in msg or "403" in msg or "authentication" in lower or "authorization" in lower:
        return (
            "Jira authentication or authorization failed. "
            "Check JIRA_EMAIL and JIRA_API_TOKEN."
        )
    return msg


def _flatten_key_values(*values: Any) -> List[str]:
    texts: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            texts.append(value)
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                texts.extend(_flatten_key_values(item))
    return texts


def parse_issue_keys(*values: Any, max_keys: int = MAX_LOOKUP_KEYS) -> List[str]:
    """Extract Jira keys from strings/lists, expanding ranges like BIG-15 to BIG-18."""
    found: List[str] = []
    seen = set()

    def _add(key: Optional[str]) -> None:
        normalized = normalize_issue_key(key)
        if not normalized or normalized in seen or len(found) >= max_keys:
            return
        seen.add(normalized)
        found.append(normalized)

    for text in _flatten_key_values(*values):
        for match in _RANGE_RE.finditer(text):
            project_a = match.group(1)
            start = int(match.group(2))
            project_b = match.group(3) or project_a
            end = int(match.group(4))
            if project_a.upper() != project_b.upper():
                continue
            lo, hi = (start, end) if start <= end else (end, start)
            if hi - lo + 1 > max_keys:
                hi = lo + max_keys - 1
            for number in range(lo, hi + 1):
                _add(f"{project_a}-{number}")
        for match in _ISSUE_KEY_SEARCH_RE.finditer(text):
            _add(match.group(1))
    return found


class LookupJiraService:
    """Read-only Jira lookup for chat agents: issues (with parent) and/or open Epics."""

    def lookup(
        self,
        *,
        issue_key: Optional[str] = None,
        issue_keys: Optional[Union[str, Sequence[str]]] = None,
        project_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        keys = parse_issue_keys(issue_key, issue_keys)
        projects = normalize_project_keys(project_key)
        explicit_project = bool(projects)
        if not keys and not projects:
            raise LookupJiraError("issue_key or project_key is required")

        from bigas.tickets.config import use_internal_board

        parent_guidance = (
            "A referenced ticket's parent is context only. "
            "Set parent_epic_key on create_jira_issue only if the new work "
            "belongs under that Epic's goal; otherwise create a standalone Task/Bug."
        )

        if use_internal_board():
            from bigas.tickets.service import TicketService

            service = TicketService()
            out: Dict[str, Any] = {"ok": True, "parent_guidance": parent_guidance}
            if keys:
                issues = service.lookup_tickets(keys)
                if issues:
                    out["issues"] = issues
                    out["issue"] = issues[0]
                missing = [k for k in keys if k not in {i["key"] for i in issues}]
                if missing:
                    out["missing"] = missing
            if projects and (explicit_project or len(keys) <= 1):
                proj = projects[0]
                out["project_key"] = proj
                out["epics"] = service.list_epics(proj)
            return out

        try:
            client = JiraClient(JiraConfig.from_env())
            out = {"ok": True, "parent_guidance": parent_guidance}
            if keys:
                issues, missing = self._load_issues(client, keys)
                if issues:
                    out["issues"] = issues
                    out["issue"] = issues[0]
                    if issues[0].get("parent"):
                        out["parent"] = issues[0]["parent"]
                    if not explicit_project:
                        proj = str(issues[0].get("project_key") or "").strip()
                        if proj:
                            projects.append(proj)
                if missing:
                    out["missing"] = missing
            # Open Epics are for filing context. Skip them on a multi-issue status lookup
            # unless the caller asked for a project explicitly.
            if projects and (explicit_project or len(keys) <= 1):
                proj = projects[0]
                out["project_key"] = proj
                out["epics"] = client.list_open_epics(proj)
            return out
        except JiraError as e:
            raise LookupJiraError(
                _format_jira_error(
                    e,
                    issue_key=(keys[0] if keys else ""),
                    project_key=(projects[0] if projects else ""),
                )
            ) from e

    def _load_issues(
        self,
        client: JiraClient,
        keys: Iterable[str],
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        key_list = [k for k in keys if normalize_issue_key(k)]
        if not key_list:
            return [], []

        fields = issue_lookup_fields()
        raw_issues = client.search_issues_by_keys(key_list, fields=fields)
        by_key: Dict[str, Dict[str, Any]] = {}
        for raw in raw_issues:
            compact = compact_jira_issue(raw, base_url=client._config.base_url)
            key = compact.get("key")
            if key:
                by_key[key] = compact

        issues: List[Dict[str, Any]] = []
        missing: List[str] = []
        for key in key_list:
            compact = by_key.get(normalize_issue_key(key) or "")
            if compact:
                issues.append(compact)
            else:
                missing.append(key)
        return issues, missing
