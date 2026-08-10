from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
import os

from bigas.llm.factory import get_llm_client

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    normalize_project_keys,
)
from bigas.resources.product.progress_updates.prompts import (
    PROGRESS_UPDATES_SYSTEM_PROMPT,
    build_progress_updates_user_prompt,
)

logger = logging.getLogger(__name__)


class ProgressUpdatesError(RuntimeError):
    pass


def _project_key_from_issue_key(issue_key: str) -> str:
    """Return Jira project key from an issue key like VFA-12."""
    raw = (issue_key or "").strip()
    if "-" not in raw:
        return raw or "UNKNOWN"
    return raw.split("-", 1)[0].strip().upper() or "UNKNOWN"


def _normalize_done_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    """Extract key, summary, issue_type, assignee from a raw Jira issue."""
    fields = issue.get("fields", {}) or {}
    assignee = fields.get("assignee")
    if assignee is None:
        assignee_display = "Unassigned"
    elif isinstance(assignee, dict):
        assignee_display = assignee.get("displayName") or assignee.get("name") or "Unknown"
    else:
        assignee_display = str(assignee)

    key = issue.get("key", "") or ""
    return {
        "key": key,
        "project_key": _project_key_from_issue_key(key),
        "summary": (fields.get("summary") or "").strip(),
        "issue_type": (fields.get("issuetype") or {}).get("name") or "Task",
        "assignee": assignee_display,
    }


def _aggregate_stats(
    normalized: List[Dict[str, Any]],
    *,
    project_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compute total, by_type, by_assignee, and by_project from normalized done issues."""
    by_type: Dict[str, int] = {}
    by_assignee: Dict[str, int] = {}
    by_project: Dict[str, int] = {}
    for key in project_keys or []:
        k = (key or "").strip().upper()
        if k:
            by_project[k] = 0
    for i in normalized:
        t = i.get("issue_type") or "Task"
        by_type[t] = by_type.get(t, 0) + 1
        a = i.get("assignee") or "Unassigned"
        by_assignee[a] = by_assignee.get(a, 0) + 1
        p = (i.get("project_key") or "UNKNOWN").strip().upper() or "UNKNOWN"
        by_project[p] = by_project.get(p, 0) + 1
    return {
        "total": len(normalized),
        "by_type": by_type,
        "by_assignee": by_assignee,
        "by_project": by_project,
    }


def _format_done_issues_for_prompt(normalized: List[Dict[str, Any]]) -> str:
    """Format the list of done issues as readable text for the LLM, grouped by project."""
    if not normalized:
        return "(No issues moved to Done in this period.)"
    by_project: Dict[str, List[Dict[str, Any]]] = {}
    for i in normalized:
        p = (i.get("project_key") or "UNKNOWN").strip().upper() or "UNKNOWN"
        by_project.setdefault(p, []).append(i)

    lines: List[str] = []
    for project in sorted(by_project.keys()):
        lines.append(f"### {project}")
        for i in by_project[project]:
            key = i.get("key", "")
            summary = i.get("summary", "")
            issue_type = i.get("issue_type", "Task")
            assignee = i.get("assignee", "Unassigned")
            lines.append(f"- [{key}] {issue_type}: {summary} ({assignee})")
    return "\n".join(lines)


class ProgressUpdatesService:
    def __init__(
        self,
        *,
        jira_client: Optional[JiraClient] = None,
        openai_api_key: Optional[str] = None,
        openai_model: Optional[str] = None,
    ):
        if jira_client is None:
            jira_client = JiraClient(JiraConfig.from_env())
        self._jira = jira_client

        # Use shared LLM abstraction; ignore openai_api_key in favor of env-based config.
        # Model resolution order:
        #   1) openai_model argument
        #   2) BIGAS_PROGRESS_UPDATES_MODEL
        #   3) LLM_MODEL
        #   4) "gemini-3.1-pro-preview" (factory default)
        self._llm, self._model = get_llm_client(
            feature="progress_updates",
            explicit_model=openai_model,
        )

    def run(
        self,
        *,
        days: int = 7,
        jql_extra: str = "",
        project_keys: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Fetch issues done in the last `days` days and generate a coach message.
        Caller is responsible for posting the returned message to Discord if desired.
        jql_extra: optional JQL fragment (e.g. "AND statusCategory = Done") to narrow the query.
        project_keys: optional override of configured Jira project keys (str or list).
        """
        try:
            resolved_keys = normalize_project_keys(project_keys)
            if not resolved_keys:
                resolved_keys = list(JiraConfig.from_env().project_keys)
            raw_issues = self._jira.search_issues_done_in_last_n_days(
                days=days,
                jql_extra=(jql_extra or "").strip(),
                project_keys=resolved_keys,
            )
        except JiraError as e:
            raise ProgressUpdatesError(str(e))
        except ValueError as e:
            raise ProgressUpdatesError(str(e))

        normalized = [_normalize_done_issue(i) for i in raw_issues]
        stats = _aggregate_stats(normalized, project_keys=resolved_keys)
        done_issues_text = _format_done_issues_for_prompt(normalized)

        # Build and call LLM
        user_prompt = build_progress_updates_user_prompt(
            stats=stats,
            done_issues_text=done_issues_text,
            days=days,
        )
        try:
            message = self._llm.complete(
                messages=[
                    {"role": "system", "content": PROGRESS_UPDATES_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1500,
                temperature=0.8,
            )
        except Exception as e:
            logger.error("Progress updates LLM call failed", exc_info=True)
            raise ProgressUpdatesError(f"LLM request failed: {e}") from e

        # Fallback: if the LLM returns an empty message, use a deterministic summary
        if not (message or "").strip():
            logger.warning(
                "Progress updates LLM returned empty message; falling back to deterministic summary. days=%s, stats=%s",
                days,
                stats,
            )
            message = (
                f"Here’s what the team completed in the last {days} days:\n\n"
                f"{done_issues_text}"
            )

        return {
            "ok": True,
            "stats": stats,
            "done_issues": normalized,
            "message": message,
        }
