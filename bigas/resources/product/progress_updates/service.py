from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence
import logging
import os

from bigas.llm.factory import get_llm_client

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    normalize_project_keys,
)
from bigas.resources.product.progress_updates.github_commits import (
    fetch_commits_for_projects,
    format_commits_for_prompt,
)
from bigas.resources.product.progress_updates.prompts import (
    PROGRESS_UPDATES_SYSTEM_PROMPT,
    build_progress_updates_user_prompt,
)
from bigas.tickets.labels import normalize_label, normalize_labels

logger = logging.getLogger(__name__)

UNLABELED_GROUP = "Unlabeled"
DEFAULT_IGNORE_LABELS = ("customer-request",)
VALID_GROUP_BY = frozenset({"project", "label"})


class ProgressUpdatesError(RuntimeError):
    pass


def _project_key_from_issue_key(issue_key: str) -> str:
    """Return Jira project key from an issue key like VFA-12."""
    raw = (issue_key or "").strip()
    if "-" not in raw:
        return raw or "UNKNOWN"
    return raw.split("-", 1)[0].strip().upper() or "UNKNOWN"


def normalize_ignore_labels(raw: Optional[Any] = None) -> List[str]:
    """Normalize ignore-label input. None → default customer-request; [] → ignore none."""
    if raw is None:
        raw = list(DEFAULT_IGNORE_LABELS)
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    out: List[str] = []
    seen: set[str] = set()
    for item in raw or []:
        label = normalize_label(item)
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def normalize_group_by(raw: Optional[str] = None) -> str:
    value = (raw or "project").strip().lower()
    if value not in VALID_GROUP_BY:
        raise ProgressUpdatesError("group_by must be 'project' or 'label'")
    return value


def _remaining_labels(labels: Iterable[Any], ignore: Sequence[str]) -> List[str]:
    ignored = {normalize_label(item) for item in ignore}
    remaining: List[str] = []
    seen: set[str] = set()
    for label in normalize_labels(labels):
        if label in ignored or label in seen:
            continue
        seen.add(label)
        remaining.append(label)
    return remaining


def group_issues_by_label(
    issues: List[Dict[str, Any]],
    *,
    ignore_labels: Optional[Sequence[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Group Done issues by remaining labels. Tickets with none go to Unlabeled."""
    ignore = list(ignore_labels) if ignore_labels is not None else normalize_ignore_labels(None)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    unlabeled: List[Dict[str, Any]] = []
    for issue in issues:
        remaining = _remaining_labels(issue.get("labels") or [], ignore)
        if not remaining:
            unlabeled.append(issue)
            continue
        for label in remaining:
            grouped.setdefault(label, []).append(issue)
    ordered = {key: grouped[key] for key in sorted(grouped)}
    if unlabeled:
        ordered[UNLABELED_GROUP] = unlabeled
    return ordered


def _default_issue_client() -> Any:
    from bigas.tickets.config import use_internal_board

    if use_internal_board():
        from bigas.tickets.jira_adapter import TicketJiraAdapter

        return TicketJiraAdapter()
    return JiraClient(JiraConfig.from_env())


def _configured_project_keys(client: Any) -> List[str]:
    from bigas.portfolio import jira_project_keys

    config = getattr(client, "_config", None)
    keys = getattr(config, "project_keys", None) if config is not None else None
    if keys:
        return list(keys)
    return jira_project_keys()


def _normalize_done_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    """Extract key, summary, issue_type, assignee, labels from a raw Jira/board issue."""
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
        "labels": normalize_labels(fields.get("labels") or []),
    }


def _aggregate_stats(
    normalized: List[Dict[str, Any]],
    *,
    project_keys: Optional[List[str]] = None,
    ignore_labels: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Compute total, by_type, by_assignee, by_project, and by_label from Done issues."""
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

    grouped = group_issues_by_label(normalized, ignore_labels=ignore_labels)
    by_label = {label: len(items) for label, items in grouped.items()}
    return {
        "total": len(normalized),
        "by_type": by_type,
        "by_assignee": by_assignee,
        "by_project": by_project,
        "by_label": by_label,
    }


def _format_issue_line(
    issue: Dict[str, Any],
    *,
    ignore_labels: Optional[Sequence[str]] = None,
) -> str:
    key = issue.get("key", "")
    summary = issue.get("summary", "")
    issue_type = issue.get("issue_type", "Task")
    assignee = issue.get("assignee", "Unassigned")
    remaining = _remaining_labels(issue.get("labels") or [], ignore_labels or [])
    label_suffix = f" labels={','.join(remaining)}" if remaining else " unlabeled"
    return f"- [{key}] {issue_type}: {summary} ({assignee}){label_suffix}"


def _format_done_issues_for_prompt(
    normalized: List[Dict[str, Any]],
    *,
    group_by: str = "project",
    ignore_labels: Optional[Sequence[str]] = None,
) -> str:
    """Format Done issues for the LLM, grouped by project or remaining labels."""
    if not normalized:
        return "(No issues moved to Done in this period.)"
    if group_by == "label":
        grouped = group_issues_by_label(normalized, ignore_labels=ignore_labels)
        lines: List[str] = []
        for label, issues in grouped.items():
            lines.append(f"### {label}")
            for issue in issues:
                lines.append(_format_issue_line(issue, ignore_labels=ignore_labels))
        return "\n".join(lines)

    by_project: Dict[str, List[Dict[str, Any]]] = {}
    for i in normalized:
        p = (i.get("project_key") or "UNKNOWN").strip().upper() or "UNKNOWN"
        by_project.setdefault(p, []).append(i)

    lines = []
    for project in sorted(by_project.keys()):
        lines.append(f"### {project}")
        for i in by_project[project]:
            lines.append(_format_issue_line(i))
    return "\n".join(lines)


def _inactive_project_keys(stats: Dict[str, Any], git_stats: Dict[str, Any]) -> List[str]:
    by_project = stats.get("by_project") or {}
    keys = sorted(set(list(by_project.keys()) + list(git_stats.keys())))
    inactive = []
    for key in keys:
        jira_n = int((by_project.get(key) if by_project else 0) or 0)
        git_n = int(((git_stats.get(key) or {}).get("total")) or 0)
        if jira_n <= 0 and git_n <= 0:
            inactive.append(key)
    return inactive


class ProgressUpdatesService:
    def __init__(
        self,
        *,
        jira_client: Optional[Any] = None,
        openai_api_key: Optional[str] = None,
        openai_model: Optional[str] = None,
        github_token: Optional[str] = None,
        include_git: bool = True,
    ):
        if jira_client is None:
            jira_client = _default_issue_client()
        self._jira = jira_client
        self._github_token = (
            (github_token or "").strip()
            or (os.environ.get("GITHUB_TOKEN") or "").strip()
            or None
        )
        self._include_git = include_git

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
        group_by: str = "project",
        ignore_labels: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Fetch Done issues (+ optional git commits) for the last `days` days
        and generate a coach message.
        Caller is responsible for posting the returned message to Discord if desired.
        """
        grouping = normalize_group_by(group_by)
        ignored = normalize_ignore_labels(
            ignore_labels if grouping == "label" else (ignore_labels or [])
        )
        try:
            resolved_keys = normalize_project_keys(project_keys)
            if not resolved_keys:
                resolved_keys = normalize_project_keys(_configured_project_keys(self._jira))
            raw_issues = self._jira.search_issues_done_in_last_n_days(
                days=days,
                jql_extra=(jql_extra or "").strip(),
                project_keys=resolved_keys or None,
            )
        except JiraError as e:
            raise ProgressUpdatesError(str(e))
        except ValueError as e:
            raise ProgressUpdatesError(str(e))

        normalized = [_normalize_done_issue(i) for i in raw_issues]
        stats = _aggregate_stats(
            normalized,
            project_keys=resolved_keys,
            ignore_labels=ignored,
        )
        done_issues_text = _format_done_issues_for_prompt(
            normalized,
            group_by=grouping,
            ignore_labels=ignored,
        )

        git_payload: Dict[str, Any] = {
            "by_project": {},
            "stats": {},
            "errors": [],
        }
        if self._include_git:
            git_payload = fetch_commits_for_projects(
                project_keys=resolved_keys,
                days=days,
                token=self._github_token,
            )
            if git_payload.get("errors"):
                logger.warning(
                    "Git commit fetch had errors: %s", git_payload.get("errors")
                )

        git_stats = git_payload.get("stats") or {}
        git_commits_text = format_commits_for_prompt(
            git_payload.get("by_project") or {},
            stats=git_stats,
        )

        user_prompt = build_progress_updates_user_prompt(
            stats=stats,
            done_issues_text=done_issues_text,
            days=days,
            git_commits_text=git_commits_text,
            git_stats=git_stats,
            group_by=grouping,
            ignore_labels=ignored,
        )
        try:
            message = self._llm.complete(
                messages=[
                    {"role": "system", "content": PROGRESS_UPDATES_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=2000,
                temperature=0.8,
            )
        except Exception as e:
            logger.error("Progress updates LLM call failed", exc_info=True)
            raise ProgressUpdatesError(f"LLM request failed: {e}") from e

        if not (message or "").strip():
            logger.warning(
                "Progress updates LLM returned empty message; falling back to deterministic summary. days=%s, stats=%s",
                days,
                stats,
            )
            inactive = _inactive_project_keys(stats, git_stats)
            empty_line = (
                f"\nNo activity: {', '.join(inactive)}" if inactive else ""
            )
            message = (
                f"Here’s what the team completed in the last {days} days:\n\n"
                f"{done_issues_text}\n\nGit:\n{git_commits_text}{empty_line}"
            )

        return {
            "ok": True,
            "stats": stats,
            "done_issues": normalized,
            "group_by": grouping,
            "ignore_labels": ignored,
            "git_stats": git_stats,
            "git_errors": git_payload.get("errors") or [],
            "message": message,
        }
