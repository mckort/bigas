from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
import os
from datetime import datetime

import openai

from bigas.resources.product.create_release_notes.jira_client import JiraClient, JiraConfig, JiraError
from bigas.resources.product.progress_updates.prompts import (
    PROGRESS_UPDATES_SYSTEM_PROMPT,
    build_progress_updates_user_prompt,
)

logger = logging.getLogger(__name__)


class ProgressUpdatesError(RuntimeError):
    pass


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

    return {
        "key": issue.get("key", ""),
        "summary": (fields.get("summary") or "").strip(),
        "issue_type": (fields.get("issuetype") or {}).get("name") or "Task",
        "assignee": assignee_display,
    }


def _aggregate_stats(normalized: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute total, by_type, and by_assignee from normalized done issues."""
    by_type: Dict[str, int] = {}
    by_assignee: Dict[str, int] = {}
    for i in normalized:
        t = i.get("issue_type") or "Task"
        by_type[t] = by_type.get(t, 0) + 1
        a = i.get("assignee") or "Unassigned"
        by_assignee[a] = by_assignee.get(a, 0) + 1
    return {
        "total": len(normalized),
        "by_type": by_type,
        "by_assignee": by_assignee,
    }


def _format_done_issues_for_prompt(normalized: List[Dict[str, Any]]) -> str:
    """Format the list of done issues as readable text for the LLM."""
    if not normalized:
        return "(No issues moved to Done in this period.)"
    lines = []
    for i in normalized:
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

        api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProgressUpdatesError("OPENAI_API_KEY environment variable not set.")

        self._openai_client = openai.OpenAI(api_key=api_key)
        self._model = openai_model or os.environ.get("OPENAI_MODEL") or "gpt-4"

    def run(
        self,
        *,
        days: int = 14,
        biweekly_skip: bool = False,
    ) -> Dict[str, Any]:
        """
        Fetch issues done in the last `days` days and generate a coach message.
        Caller is responsible for posting the returned message to Discord if desired.
        If biweekly_skip is True, only run on "cheer weeks" (even ISO week); otherwise return skipped.
        """
        if biweekly_skip:
            iso_week = datetime.utcnow().isocalendar()[1]
            if iso_week % 2 != 0:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "not_cheer_week",
                    "iso_week": iso_week,
                }

        try:
            raw_issues = self._jira.search_issues_done_in_last_n_days(days=days)
        except JiraError as e:
            raise ProgressUpdatesError(str(e))
        except ValueError as e:
            raise ProgressUpdatesError(str(e))

        normalized = [_normalize_done_issue(i) for i in raw_issues]
        stats = _aggregate_stats(normalized)
        done_issues_text = _format_done_issues_for_prompt(normalized)

        # Build and call OpenAI
        user_prompt = build_progress_updates_user_prompt(
            stats=stats,
            done_issues_text=done_issues_text,
            days=days,
        )
        try:
            completion = self._openai_client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": PROGRESS_UPDATES_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=800,
                temperature=0.4,
            )
            message = (completion.choices[0].message.content or "").strip()
        except Exception as e:
            logger.error("Progress updates OpenAI call failed", exc_info=True)
            raise ProgressUpdatesError(f"OpenAI request failed: {e}") from e

        return {
            "ok": True,
            "skipped": False,
            "stats": stats,
            "done_issues": normalized,
            "message": message,
        }
