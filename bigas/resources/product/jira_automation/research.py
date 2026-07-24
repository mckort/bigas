"""Research and describe (AI) handler."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bigas.llm.factory import get_llm_client
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraError,
    adf_to_plain_text,
)
from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER
from bigas.resources.product.jira_automation.description import (
    extract_brief,
    upsert_research_section,
)
from bigas.resources.product.jira_automation.github_context import GitHubRepoContext
from bigas.resources.product.jira_automation.prompts import (
    RESEARCH_SYSTEM_PROMPT,
    build_research_user_prompt,
)
from bigas.resources.product.jira_automation.web_research import fetch_web_snippets

logger = logging.getLogger(__name__)


class ResearchHandlerError(RuntimeError):
    pass


def _linked_issue_keys(fields: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    seen = set()
    for link in fields.get("issuelinks") or []:
        for side in ("outwardIssue", "inwardIssue"):
            issue = link.get(side) or {}
            key = (issue.get("key") or "").strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    parent = fields.get("parent") or {}
    parent_key = (parent.get("key") or "").strip()
    if parent_key and parent_key not in seen:
        keys.append(parent_key)
    return keys


def _format_linked_issues(jira: JiraClient, keys: List[str]) -> str:
    if not keys:
        return "(none)"
    lines = []
    for key in keys[:15]:
        try:
            issue = jira.get_issue(
                key,
                fields=["summary", "status", "description", "issuetype"],
            )
            fields = issue.get("fields") or {}
            summary = (fields.get("summary") or "").strip()
            status = ((fields.get("status") or {}).get("name") or "").strip()
            itype = ((fields.get("issuetype") or {}).get("name") or "").strip()
            desc = adf_to_plain_text(fields.get("description"))[:800]
            lines.append(f"- {key} [{itype}/{status}]: {summary}\n  {desc}")
        except JiraError as e:
            lines.append(f"- {key}: (unavailable: {e})")
    return "\n".join(lines)


class ResearchDescribeHandler:
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
            feature="jira_research",
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
            raise ResearchHandlerError(str(e)) from e

        fields = issue.get("fields") or {}
        summary = (fields.get("summary") or "").strip()
        description_plain = adf_to_plain_text(fields.get("description"))
        brief = extract_brief(description_plain) or description_plain or summary

        linked_keys = _linked_issue_keys(fields)
        linked_text = _format_linked_issues(self._jira, linked_keys)

        hints = [summary]
        labels = fields.get("labels") or []
        hints.extend(str(l) for l in labels[:5])
        repo_context = self._github.fetch_context(repo, query_hints=hints)

        web_query = f"{summary} {brief[:120]}".strip()
        web_context = fetch_web_snippets(web_query)

        user_prompt = build_research_user_prompt(
            issue_key=issue_key,
            summary=summary,
            brief=brief,
            linked_issues_text=linked_text,
            repo_context=repo_context,
            web_context=web_context,
        )
        try:
            research_body = self._llm.complete(
                messages=[
                    {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=3500,
                temperature=0.4,
            )
        except Exception as e:
            logger.error("Research LLM failed for %s", issue_key, exc_info=True)
            raise ResearchHandlerError(f"LLM request failed: {e}") from e

        research_body = (research_body or "").strip()
        if not research_body:
            raise ResearchHandlerError("LLM returned empty research body")

        # Strip accidental outer headings if the model ignored instructions
        for prefix in ("## AI Research (Bigas)", "## Brief", "# AI Research"):
            if research_body.lower().startswith(prefix.lower()):
                research_body = research_body.split("\n", 1)[-1].strip()

        new_description = upsert_research_section(
            description_plain,
            research_markdown=research_body,
            brief_fallback=brief,
        )

        try:
            self._jira.update_description(issue_key, new_description)
            self._jira.transition_issue(
                issue_key,
                to_status_name=approval_status,
                comment=(
                    f"{BIGAS_COMMENT_MARKER} Research complete. "
                    f"Moved to {approval_status} for human review. "
                    f"(model={self._model})"
                ),
            )
        except JiraError as e:
            raise ResearchHandlerError(str(e)) from e

        return {
            "ok": True,
            "handler": "research_describe",
            "issue_key": issue_key,
            "model": self._model,
            "repo": repo,
            "linked_issues": linked_keys,
            "moved_to": approval_status,
            "research_chars": len(research_body),
        }
