"""Design and plan (AI) handler."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bigas.llm.factory import get_llm_client
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraError,
    adf_to_plain_text,
)
from bigas.resources.product.jira_automation.comments import format_human_comments
from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER
from bigas.resources.product.jira_automation.description import (
    RESEARCH_HEADING,
    extract_brief,
    extract_section,
    upsert_plan_section,
)
from bigas.resources.product.jira_automation.github_context import GitHubRepoContext
from bigas.resources.product.jira_automation.prompts import (
    DESIGN_SYSTEM_PROMPT,
    build_design_user_prompt,
)
from bigas.resources.product.jira_automation.research import (
    _format_linked_issues,
    _linked_issue_entries,
)

logger = logging.getLogger(__name__)


class DesignHandlerError(RuntimeError):
    pass


class DesignPlanHandler:
    def __init__(
        self,
        *,
        jira: JiraClient,
        github: Optional[GitHubRepoContext] = None,
        llm_model: Optional[str] = None,
    ):
        self._jira = jira
        self._github = github or GitHubRepoContext()
        self._llm, self._model = get_llm_client(
            feature="jira_design",
            explicit_model=llm_model,
        )

    def run(
        self,
        *,
        issue_key: str,
        repo: str,
        approval_status: str,
    ) -> Dict[str, Any]:
        try:
            issue = self._jira.get_issue(
                issue_key,
                fields=[
                    "summary",
                    "description",
                    "status",
                    "issuetype",
                    "labels",
                    "components",
                    "issuelinks",
                    "parent",
                    "project",
                ],
            )
        except JiraError as e:
            raise DesignHandlerError(str(e)) from e

        fields = issue.get("fields") or {}
        summary = (fields.get("summary") or "").strip()
        description_plain = adf_to_plain_text(fields.get("description"))
        brief = extract_brief(description_plain) or description_plain or summary
        research = extract_section(description_plain, RESEARCH_HEADING)

        linked_entries = _linked_issue_entries(fields)
        linked_keys = [e["key"] for e in linked_entries]
        linked_text = _format_linked_issues(self._jira, linked_keys, entries=linked_entries)

        try:
            raw_comments = self._jira.list_comments(issue_key, max_results=50)
        except JiraError:
            logger.warning("Failed to load comments for %s", issue_key, exc_info=True)
            raw_comments = []
        comments_text = format_human_comments(raw_comments)

        hints = [summary]
        labels = fields.get("labels") or []
        hints.extend(str(l) for l in labels[:5])
        if research:
            hints.append(research[:80])
        repo_context = self._github.fetch_context(repo, query_hints=hints)

        user_prompt = build_design_user_prompt(
            issue_key=issue_key,
            summary=summary,
            brief=brief,
            research=research,
            linked_issues_text=linked_text,
            repo_context=repo_context,
            comments_text=comments_text,
        )
        try:
            plan_body = self._llm.complete(
                messages=[
                    {"role": "system", "content": DESIGN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=4000,
                temperature=0.3,
            )
        except Exception as e:
            logger.error("Design LLM failed for %s", issue_key, exc_info=True)
            raise DesignHandlerError(f"LLM request failed: {e}") from e

        plan_body = (plan_body or "").strip()
        if not plan_body:
            raise DesignHandlerError("LLM returned empty plan body")

        for prefix in ("## AI Plan (Bigas)", "## AI Research (Bigas)", "## Brief", "# AI Plan"):
            if plan_body.lower().startswith(prefix.lower()):
                plan_body = plan_body.split("\n", 1)[-1].strip()

        new_description = upsert_plan_section(
            description_plain,
            plan_markdown=plan_body,
            brief_fallback=brief,
        )

        try:
            self._jira.update_description(issue_key, new_description)
            self._jira.transition_issue(
                issue_key,
                to_status_name=approval_status,
                comment=(
                    f"{BIGAS_COMMENT_MARKER} Design/plan complete. "
                    f"Moved to {approval_status} for human review. "
                    f"(model={self._model})"
                ),
            )
        except JiraError as e:
            raise DesignHandlerError(str(e)) from e

        return {
            "ok": True,
            "handler": "design_plan",
            "issue_key": issue_key,
            "summary": summary,
            "model": self._model,
            "repo": repo,
            "linked_issues": linked_keys,
            "moved_to": approval_status,
            "plan_chars": len(plan_body),
            "had_research_section": bool(research),
            "human_comments_included": comments_text != "(none)",
        }
