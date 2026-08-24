"""Read-only Jira JQL search for chat agents."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from bigas.portfolio import DEFAULT_PROJECT_ALIASES, jira_project_keys
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    compact_jira_issue,
    issue_lookup_fields,
)

DEFAULT_MAX_RESULTS = 25
MAX_RESULTS_CAP = 50

_PROJECT_EQ_RE = re.compile(
    r"""\bproject\s*=\s*["']?([A-Z][A-Z0-9]+)["']?""",
    re.IGNORECASE,
)
_PROJECT_IN_RE = re.compile(
    r"""\bproject\s+in\s*\(([^)]+)\)""",
    re.IGNORECASE,
)
_ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)


class SearchJiraError(RuntimeError):
    pass


def allowed_project_keys() -> List[str]:
    keys = list(jira_project_keys())
    for key in DEFAULT_PROJECT_ALIASES:
        if key not in keys:
            keys.append(key)
    return keys


def extract_jql_project_keys(jql: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for match in _PROJECT_EQ_RE.finditer(jql or ""):
        key = match.group(1).upper()
        if key not in seen:
            seen.add(key)
            found.append(key)
    for match in _PROJECT_IN_RE.finditer(jql or ""):
        for part in match.group(1).split(","):
            token = part.strip().strip("\"'").upper()
            if token and token not in seen:
                seen.add(token)
                found.append(token)
    return found


def scope_jql_to_portfolio(
    jql: str,
    allowed: Optional[Sequence[str]] = None,
) -> str:
    """Ensure JQL is limited to portfolio projects. Wraps unscoped queries."""
    query = (jql or "").strip()
    if not query:
        raise SearchJiraError("jql is required")
    allowed_keys = [k.strip().upper() for k in (allowed or allowed_project_keys()) if k]
    if not allowed_keys:
        raise SearchJiraError("No Jira projects are configured")
    mentioned = extract_jql_project_keys(query)
    unknown = [key for key in mentioned if key not in set(allowed_keys)]
    if unknown:
        raise SearchJiraError(
            "JQL references projects outside the portfolio: " + ", ".join(unknown)
        )
    clause = ", ".join(allowed_keys)
    order_match = _ORDER_BY_RE.search(query)
    if order_match:
        body = query[: order_match.start()].strip()
        order = query[order_match.start() :].strip()
        return f"({body}) AND project in ({clause}) {order}"
    return f"({query}) AND project in ({clause})"


def _clamp_max_results(max_results: Any) -> int:
    try:
        value = int(max_results)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_RESULTS
    return max(1, min(value, MAX_RESULTS_CAP))


class SearchJiraService:
    """Run a scoped JQL search and return compact issue rows."""

    def search(
        self,
        *,
        jql: str,
        max_results: Any = DEFAULT_MAX_RESULTS,
    ) -> Dict[str, Any]:
        from bigas.tickets.config import use_internal_board

        if use_internal_board():
            raise SearchJiraError("search_jira requires Jira Cloud")

        scoped = scope_jql_to_portfolio(jql)
        limit = _clamp_max_results(max_results)
        try:
            client = JiraClient(JiraConfig.from_env())
            raw_issues = client.search_jql(
                jql=scoped,
                fields=issue_lookup_fields(),
                max_results_per_page=limit,
                max_pages=1,
            )
        except JiraError as exc:
            raise SearchJiraError(str(exc)) from exc

        issues = [
            compact_jira_issue(raw, base_url=client._config.base_url)
            for raw in raw_issues
        ]
        issues = [row for row in issues if row.get("key")]
        return {
            "ok": True,
            "jql": scoped,
            "issues": issues,
            "count": len(issues),
        }
