"""In progress (AI) handler — launch Cursor cloud agent to implement + open PR."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

from bigas.resources.cto.autofix.cursor_client import (
    CursorCloudAgentClient,
    CursorCloudAgentError,
)
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraError,
    adf_to_plain_text,
)
from bigas.resources.product.jira_automation.comments import format_human_comments
from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER
from bigas.resources.product.jira_automation.description import (
    PLAN_HEADING,
    RESEARCH_HEADING,
    extract_brief,
    extract_section,
)

logger = logging.getLogger(__name__)


class ImplementHandlerError(RuntimeError):
    pass


def _slugify(text: str, *, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s or "work")[:max_len].strip("-") or "work"


def build_implement_prompt(
    *,
    issue_key: str,
    summary: str,
    brief: str,
    research: str,
    plan: str,
    comments_text: str,
    repo: str,
) -> str:
    return f"""You are implementing a Jira issue in repository {repo}.

Issue: {issue_key}
Summary: {summary}

## Human Brief
{brief or "(empty)"}

## AI Research
{research or "(empty)"}

## AI Plan
{plan or "(empty)"}

## Human follow-up comments
{comments_text or "(none)"}

## Instructions
1. Implement the issue according to the Brief, Research, Plan, and human comments.
2. Prefer the AI Plan for technical approach; treat human comments as clarifications that override open questions.
3. Keep scope tight — do not refactor unrelated code.
4. Add/update tests when reasonable.
5. Open a pull request when done (autoCreatePR is enabled).
6. PR title MUST start with `{issue_key}:` followed by a short summary.
7. PR body MUST include a line exactly: `Jira: {issue_key}` and a short summary of what changed.
8. Do not merge the PR.
"""


class ImplementHandler:
    def __init__(
        self,
        *,
        jira: JiraClient,
        cursor_api_key: Optional[str] = None,
        cursor_model: Optional[str] = None,
    ):
        self._jira = jira
        key = (
            (cursor_api_key or "").strip()
            or (os.environ.get("CURSOR_API_KEY") or "").strip()
        )
        if not key:
            raise ImplementHandlerError("CURSOR_API_KEY is required for implement")
        self._cursor = CursorCloudAgentClient(api_key=key)
        self._cursor_model = (
            (cursor_model or "").strip()
            or (os.environ.get("BIGAS_JIRA_IMPLEMENT_MODEL") or "").strip()
            or (os.environ.get("BIGAS_CTO_AUTOFIX_MODEL") or "").strip()
            or None
        )

    def run(
        self,
        *,
        issue_key: str,
        repo: str,
        base_branch: str = "main",
    ) -> Dict[str, Any]:
        try:
            issue = self._jira.get_issue(
                issue_key,
                fields=[
                    "summary",
                    "description",
                    "status",
                    "labels",
                    "issuelinks",
                    "parent",
                    "project",
                ],
            )
        except JiraError as e:
            raise ImplementHandlerError(str(e)) from e

        fields = issue.get("fields") or {}
        summary = (fields.get("summary") or "").strip()
        description_plain = adf_to_plain_text(fields.get("description"))
        brief = extract_brief(description_plain) or description_plain or summary
        research = extract_section(description_plain, RESEARCH_HEADING)
        plan = extract_section(description_plain, PLAN_HEADING)

        try:
            raw_comments = self._jira.list_comments(issue_key, max_results=50)
        except JiraError:
            logger.warning("Failed to load comments for %s", issue_key, exc_info=True)
            raw_comments = []
        comments_text = format_human_comments(raw_comments)

        if not plan.strip() and not research.strip():
            raise ImplementHandlerError(
                "No AI Plan / AI Research sections found — run Research and Design first."
            )

        prompt = build_implement_prompt(
            issue_key=issue_key,
            summary=summary,
            brief=brief,
            research=research,
            plan=plan,
            comments_text=comments_text,
            repo=repo,
        )
        repo_url = f"https://github.com/{repo}"
        agent_name = f"Bigas implement {issue_key} {_slugify(summary)}"[:100]

        try:
            launched = self._cursor.launch_implementation(
                repo_url=repo_url,
                prompt_text=prompt,
                starting_ref=base_branch,
                name=agent_name,
                model_id=self._cursor_model,
            )
        except CursorCloudAgentError as e:
            raise ImplementHandlerError(str(e)) from e

        agent_url = launched.get("agent_url") or ""
        agent_id = launched.get("agent_id") or ""
        run_id = launched.get("run_id") or ""

        comment = (
            f"{BIGAS_COMMENT_MARKER} Implementation started via Cursor cloud agent.\n"
            f"Repo: `{repo}` (base `{base_branch}`)\n"
            f"Agent: {agent_url or agent_id}\n"
            f"agent_id={agent_id} run_id={run_id}\n"
            f"Left in In Progress (AI) until the PR is ready to merge."
        )
        try:
            self._jira.add_comment(issue_key, comment)
        except JiraError as e:
            raise ImplementHandlerError(
                f"Agent launched but failed to comment on Jira: {e}"
            ) from e

        return {
            "ok": True,
            "handler": "implement",
            "issue_key": issue_key,
            "summary": summary,
            "repo": repo,
            "base_branch": base_branch,
            "agent_id": agent_id,
            "agent_url": agent_url,
            "run_id": run_id,
            "left_in_status": "In Progress (AI)",
            "had_plan_section": bool(plan.strip()),
            "had_research_section": bool(research.strip()),
            "human_comments_included": comments_text != "(none)",
        }
