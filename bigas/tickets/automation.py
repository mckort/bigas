"""Internal ticket AI column automation (mirrors Jira webhook flow)."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from bigas.resources.product.jira_automation.config import (
    BIGAS_COMMENT_MARKER,
    HANDLER_DESIGN,
    HANDLER_IMPLEMENT,
    HANDLER_RESEARCH,
    JiraAutomationConfig,
)
from bigas.resources.product.jira_automation.design import DesignPlanHandler
from bigas.resources.product.jira_automation.implement import ImplementHandler
from bigas.resources.product.jira_automation.research import ResearchDescribeHandler
from bigas.resources.product.jira_automation.service import _IDEMPOTENCY, _get_quota
from bigas.tickets.jira_adapter import TicketJiraAdapter

logger = logging.getLogger(__name__)


class InternalTicketAutomation:
    """Route internal ticket status changes to existing AI handlers."""

    def __init__(self) -> None:
        self._config = JiraAutomationConfig.from_env()
        self._adapter = TicketJiraAdapter()

    def handle_status_change(
        self,
        *,
        issue_key: str,
        to_status: str,
        from_status: str = "",
        project_key: str = "",
    ) -> Dict[str, Any]:
        issue_key = (issue_key or "").strip()
        to_status = (to_status or "").strip()
        project_key = (project_key or "").strip().upper()
        if not project_key and issue_key and "-" in issue_key:
            project_key = issue_key.split("-", 1)[0].upper()

        handler = self._config.handler_for_status(to_status)
        ticket = self._adapter._ticket(issue_key)
        if ticket:
            from bigas.okr.engine import handle_objective_status_change
            from bigas.okr.model import is_objective

            if is_objective(ticket) and handler in (
                HANDLER_RESEARCH,
                HANDLER_DESIGN,
                HANDLER_IMPLEMENT,
            ):
                result = handle_objective_status_change(
                    ticket, to_status=to_status, from_status=from_status
                )
                if (
                    handler == HANDLER_RESEARCH
                    and result.get("handler") == "okr_research"
                    and result.get("moved_to")
                    and not result.get("skipped")
                ):
                    self._notify_pm(
                        issue_key,
                        result,
                        result.get("moved_to") or self._config.status_description_approval,
                    )
                return result

        if not handler:
            return {"ok": True, "skipped": True, "reason": "no handler for status"}

        if not self._config.is_project_allowed(project_key):
            return {"ok": True, "skipped": True, "reason": "project not in allowlist"}

        repo = self._config.repo_for_project(project_key)
        if not repo:
            return {"ok": True, "skipped": True, "reason": "no repo mapped"}

        idem = f"internal:{issue_key}:{to_status}:{from_status}"
        cache_key = f"{handler}:{idem}"
        if not _IDEMPOTENCY.try_claim(cache_key):
            return {"ok": True, "skipped": True, "reason": "duplicate delivery"}

        quota = _get_quota(self._config.daily_quota)
        ok_quota, used, limit = quota.try_acquire()
        if not ok_quota:
            _IDEMPOTENCY.clear(cache_key)
            self._adapter.add_comment(
                issue_key,
                f"{BIGAS_COMMENT_MARKER} Skipped AI run: daily quota reached ({used}/{limit}).",
            )
            return {"ok": True, "skipped": True, "reason": "daily_quota_exceeded"}

        jira = self._adapter
        try:
            if handler == HANDLER_RESEARCH:
                result = ResearchDescribeHandler(jira=jira).run(
                    issue_key=issue_key,
                    repo=repo,
                    approval_status=self._config.status_description_approval,
                )
                self._notify_pm(issue_key, result, self._config.status_description_approval)
            elif handler == HANDLER_DESIGN:
                result = DesignPlanHandler(jira=jira).run(
                    issue_key=issue_key,
                    repo=repo,
                    approval_status=self._config.status_design_approval,
                )
                self._notify_cto(issue_key, result, self._config.status_design_approval)
            else:
                result = ImplementHandler(jira=jira).run(
                    issue_key=issue_key,
                    repo=repo,
                    base_branch=self._config.base_branch_for_repo(repo),
                )
                self._notify_cto_implement(issue_key, result, repo)
            return {"ok": True, "handler": handler, "issue_key": issue_key, **(result or {})}
        except Exception as exc:
            logger.exception("Internal ticket automation failed for %s", issue_key)
            quota.release()
            _IDEMPOTENCY.clear(cache_key)
            try:
                jira.add_comment(issue_key, f"{BIGAS_COMMENT_MARKER} Automation error: {exc}")
            except Exception:
                pass
            return {"ok": False, "error": str(exc), "issue_key": issue_key}

    def _notify_pm(self, issue_key: str, result: Dict[str, Any], approval: str) -> None:
        from bigas.discord_webhook import post_to_discord
        from bigas.resources.product.jira_automation.comments import issue_discord_label

        label = issue_discord_label(issue_key, result.get("summary"))
        post_to_discord(
            os.environ.get("DISCORD_WEBHOOK_URL_PRODUCT") or "",
            f"**Research complete** {label}\nMoved to **{approval}** for review.",
            chat_agent_id="product",
        )

    def _notify_cto(self, issue_key: str, result: Dict[str, Any], approval: str) -> None:
        from bigas.discord_webhook import post_to_discord
        from bigas.resources.product.jira_automation.comments import issue_discord_label

        label = issue_discord_label(issue_key, result.get("summary"))
        post_to_discord(
            os.environ.get("DISCORD_WEBHOOK_URL_CTO") or "",
            f"**Design/plan complete** {label}\nMoved to **{approval}** for review.",
            chat_agent_id="cto",
        )

    def _notify_cto_implement(
        self, issue_key: str, result: Dict[str, Any], repo: str
    ) -> None:
        from bigas.discord_webhook import post_to_discord
        from bigas.resources.product.jira_automation.comments import issue_discord_label

        label = issue_discord_label(issue_key, result.get("summary"))
        agent_url = result.get("agent_url") or result.get("cursor_agent_url") or ""
        msg = f"**Implement started** {label}\nRepo: `{repo}`"
        if agent_url:
            msg += f"\nCursor agent: {agent_url}"
        post_to_discord(
            os.environ.get("DISCORD_WEBHOOK_URL_CTO") or "",
            msg,
            chat_agent_id="cto",
        )
