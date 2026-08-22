from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from bigas.chat.jira_formatting import format_jira_issue_markdown
from bigas.llm.factory import get_llm_client
from bigas.resources.product.create_jira_issue.lookup import (
    LookupJiraError,
    LookupJiraService,
)
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    normalize_issue_key,
)
from bigas.resources.product.review_jira_issue.prompts import (
    PM_REVIEW_MARKER,
    PM_REVIEW_SYSTEM_PROMPT,
    build_pm_review_user_prompt,
)

logger = logging.getLogger(__name__)


class ReviewJiraIssueError(RuntimeError):
    pass


def _recommend_advance(review_text: str) -> Optional[bool]:
    head = (review_text or "")[:500].lower()
    if "do not advance" in head or "don't advance" in head:
        return False
    if re.search(r"\badvance\b", head):
        return True
    return None


class ReviewJiraIssueService:
    """Product critique of a Jira issue; optionally posts/updates one marked comment."""

    def review(
        self,
        *,
        issue_key: Optional[str] = None,
        instructions: Optional[str] = None,
        post_comment: bool = True,
        llm_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = normalize_issue_key(issue_key)
        if not key:
            raise ReviewJiraIssueError("issue_key is required")

        try:
            looked_up = LookupJiraService().lookup(issue_key=key)
        except LookupJiraError as e:
            raise ReviewJiraIssueError(str(e)) from e

        issue = looked_up.get("issue") if isinstance(looked_up.get("issue"), dict) else {}
        summary = str(issue.get("summary") or key).strip()
        status = str(issue.get("status") or "").strip()
        issue_type = str(issue.get("issue_type") or "").strip()
        url = str(issue.get("url") or "").strip()
        description = str(issue.get("description") or "").strip()
        comments_text = str(issue.get("human_comments") or "(none)").strip() or "(none)"

        try:
            llm, _model = get_llm_client(
                feature="jira_pm_review",
                explicit_model=llm_model,
            )
            review_body = (llm.complete(
                [
                    {"role": "system", "content": PM_REVIEW_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": build_pm_review_user_prompt(
                            issue_key=key,
                            summary=summary,
                            status=status,
                            issue_type=issue_type,
                            description=description,
                            comments_text=comments_text,
                            instructions=instructions,
                        ),
                    },
                ],
                max_tokens=2500,
                temperature=0.3,
            ) or "").strip()
        except Exception as e:
            raise ReviewJiraIssueError(f"LLM review failed: {e}") from e

        if not review_body:
            raise ReviewJiraIssueError("LLM returned an empty review")

        footer = format_jira_issue_markdown(
            key=key,
            url=url,
            summary=summary,
            include_transition_button=True,
        )
        chat_review = f"{review_body}\n\n{footer}".strip()

        comment_posted = False
        comment_updated = False
        if post_comment:
            try:
                client = JiraClient(JiraConfig.from_env())
                posted = client.add_or_update_marked_comment(
                    key,
                    review_body,
                    marker=PM_REVIEW_MARKER,
                )
                comment_posted = True
                comment_updated = bool(posted.get("updated"))
            except JiraError as e:
                logger.warning("Failed to post PM review comment on %s: %s", key, e)

        return {
            "ok": True,
            "key": key,
            "url": url,
            "issue_summary": summary,
            "status": status,
            "review": chat_review,
            "recommend_advance": _recommend_advance(review_body),
            "comment_posted": comment_posted,
            "comment_updated": comment_updated,
        }
