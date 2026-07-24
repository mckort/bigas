"""Orchestrate Jira AI column automation (webhook → handler)."""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any, Dict, Optional

import requests

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
)
from bigas.resources.product.jira_automation.config import (
    BIGAS_COMMENT_MARKER,
    HANDLER_DESIGN,
    HANDLER_IMPLEMENT,
    HANDLER_RESEARCH,
    JiraAutomationConfig,
)
from bigas.resources.product.jira_automation.idempotency import IdempotencyCache
from bigas.resources.product.jira_automation.quota import DailyQuota
from bigas.resources.product.jira_automation.research import (
    ResearchDescribeHandler,
    ResearchHandlerError,
)

logger = logging.getLogger(__name__)

# Process-wide guards (shared across requests on one instance)
_IDEMPOTENCY = IdempotencyCache()
_QUOTA: Optional[DailyQuota] = None


class JiraAutomationError(RuntimeError):
    pass


def _get_quota(limit: int) -> DailyQuota:
    global _QUOTA
    if _QUOTA is None or _QUOTA.limit != limit:
        _QUOTA = DailyQuota(limit)
    return _QUOTA


def verify_webhook_secret(provided: Optional[str], expected: str) -> bool:
    exp = (expected or "").strip()
    got = (provided or "").strip()
    if not exp:
        return False
    # Allow "Bearer <token>" form
    if got.lower().startswith("bearer "):
        got = got[7:].strip()
    if len(got) != len(exp):
        # still compare to avoid trivial timing oracle on length alone for empty
        secrets.compare_digest(got.encode("utf-8"), exp.encode("utf-8"))
        return False
    return secrets.compare_digest(got.encode("utf-8"), exp.encode("utf-8"))


def extract_webhook_secret_from_headers(headers: Any) -> str:
    """Pull secret from X-Bigas-Webhook-Secret or Authorization."""
    if headers is None:
        return ""
    get = headers.get if hasattr(headers, "get") else lambda _k, _d=None: None
    direct = get("X-Bigas-Webhook-Secret") or get("x-bigas-webhook-secret") or ""
    if direct:
        return str(direct).strip()
    auth = get("Authorization") or get("authorization") or ""
    return str(auth).strip()


def parse_automation_payload(data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """
    Normalize Jira Automation / manual payloads into:
      issue_key, to_status, from_status, idempotency_key, project_key (optional)
    """
    data = data or {}
    issue = data.get("issue") if isinstance(data.get("issue"), dict) else {}
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}

    issue_key = (
        (data.get("issue_key") or data.get("key") or issue.get("key") or "")
        .strip()
    )

    to_status = (
        data.get("to_status")
        or data.get("status")
        or ((data.get("transition") or {}) if isinstance(data.get("transition"), dict) else {}).get(
            "to_status"
        )
        or ((fields.get("status") or {}) if isinstance(fields.get("status"), dict) else {}).get(
            "name"
        )
        or ""
    )
    to_status = str(to_status).strip()

    from_status = (
        data.get("from_status")
        or ((data.get("transition") or {}) if isinstance(data.get("transition"), dict) else {}).get(
            "from_status"
        )
        or ""
    )
    from_status = str(from_status).strip()

    project_key = (
        data.get("project_key")
        or ((fields.get("project") or {}) if isinstance(fields.get("project"), dict) else {}).get(
            "key"
        )
        or ""
    )
    project_key = str(project_key).strip().upper()
    if not project_key and issue_key and "-" in issue_key:
        project_key = issue_key.split("-", 1)[0].upper()

    idem = (
        data.get("idempotency_key")
        or data.get("delivery_id")
        or data.get("changelog_id")
        or ""
    )
    idem = str(idem).strip()
    if not idem:
        idem = f"{issue_key}:{to_status}:{from_status}"

    return {
        "issue_key": issue_key,
        "to_status": to_status,
        "from_status": from_status,
        "project_key": project_key,
        "idempotency_key": idem,
    }


def _post_discord(webhook_url: Optional[str], message: str) -> bool:
    url = (webhook_url or "").strip()
    if not url or url.startswith("placeholder"):
        return False
    msg = (message or "").strip()
    if not msg:
        return False
    if len(msg) > 1900:
        msg = msg[:1897] + "..."
    try:
        resp = requests.post(url, json={"content": msg}, timeout=20)
        if resp.status_code not in (200, 204):
            logger.error("Discord post failed: %s %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception:
        logger.error("Discord post failed", exc_info=True)
        return False


class JiraAutomationService:
    def __init__(
        self,
        *,
        config: Optional[JiraAutomationConfig] = None,
        jira: Optional[JiraClient] = None,
    ):
        self._config = config or JiraAutomationConfig.from_env()
        self._jira = jira or JiraClient(JiraConfig.from_env())
        self._quota = _get_quota(self._config.daily_quota)

    @property
    def config(self) -> JiraAutomationConfig:
        return self._config

    def handle_event(
        self,
        *,
        issue_key: str,
        to_status: str,
        from_status: str = "",
        project_key: str = "",
        idempotency_key: str = "",
        sync: bool = True,
    ) -> Dict[str, Any]:
        """
        Route a status change to the appropriate handler.
        Raises JiraAutomationError for hard failures after accepting work.
        Returns skip payloads with ok/skipped for non-actionable events.
        """
        issue_key = (issue_key or "").strip()
        to_status = (to_status or "").strip()
        project_key = (project_key or "").strip().upper()
        if not project_key and issue_key and "-" in issue_key:
            project_key = issue_key.split("-", 1)[0].upper()

        if not issue_key or not to_status:
            raise JiraAutomationError("issue_key and to_status are required")

        if not self._config.is_project_allowed(project_key):
            return {
                "ok": True,
                "skipped": True,
                "reason": f"project {project_key} not in allowlist",
                "issue_key": issue_key,
            }

        handler = self._config.handler_for_status(to_status)
        if not handler:
            return {
                "ok": True,
                "skipped": True,
                "reason": f"no handler for status {to_status!r}",
                "issue_key": issue_key,
            }

        # Phase 1: only research is implemented
        if handler == HANDLER_DESIGN:
            return {
                "ok": True,
                "skipped": True,
                "reason": "design_plan handler not implemented yet (phase 2)",
                "issue_key": issue_key,
            }
        if handler == HANDLER_IMPLEMENT:
            return {
                "ok": True,
                "skipped": True,
                "reason": "implement handler not implemented yet (phase 3)",
                "issue_key": issue_key,
            }
        if handler != HANDLER_RESEARCH:
            return {
                "ok": True,
                "skipped": True,
                "reason": f"unknown handler {handler}",
                "issue_key": issue_key,
            }

        idem = idempotency_key or f"{issue_key}:{to_status}:{from_status}"
        cache_key = f"{handler}:{idem}"
        if not _IDEMPOTENCY.try_claim(cache_key):
            return {
                "ok": True,
                "skipped": True,
                "reason": "duplicate delivery",
                "issue_key": issue_key,
                "idempotency_key": idem,
            }

        ok_quota, used, limit = self._quota.try_acquire()
        if not ok_quota:
            _IDEMPOTENCY.clear(cache_key)
            msg = (
                f"{BIGAS_COMMENT_MARKER} Skipped AI run: daily quota reached "
                f"({used}/{limit} UTC). Try again tomorrow or raise BIGAS_JIRA_AI_DAILY_QUOTA."
            )
            try:
                self._jira.add_comment(issue_key, msg)
            except JiraError:
                logger.warning("Failed to comment quota skip on %s", issue_key, exc_info=True)
            self._notify_pm(
                f"**Jira AI quota reached** ({used}/{limit})\n"
                f"Skipped research for `{issue_key}` — left in `{to_status}`."
            )
            return {
                "ok": True,
                "skipped": True,
                "reason": "daily_quota_exceeded",
                "used": used,
                "limit": limit,
                "issue_key": issue_key,
            }

        repo = self._config.repo_for_project(project_key)
        if not repo:
            return self._fail(
                issue_key=issue_key,
                to_status=to_status,
                channel="pm",
                error=f"No GitHub repo mapped for project {project_key}",
                cache_key=cache_key,
                release_quota=True,
            )

        try:
            result = ResearchDescribeHandler(jira=self._jira).run(
                issue_key=issue_key,
                repo=repo,
                approval_status=self._config.status_description_approval,
            )
        except (ResearchHandlerError, JiraError, Exception) as e:
            logger.error("Research handler failed for %s", issue_key, exc_info=True)
            return self._fail(
                issue_key=issue_key,
                to_status=to_status,
                channel="pm",
                error=str(e),
                cache_key=cache_key,
                release_quota=True,
            )

        # Success: keep cache_key claimed so Automation retries stay no-ops
        self._notify_pm(
            f"**Research complete** `{issue_key}`\n"
            f"Moved to **{self._config.status_description_approval}** for review.\n"
            f"Repo: `{repo}` · model: `{result.get('model')}`"
        )
        result["quota_used"] = used
        result["quota_limit"] = limit
        result["from_status"] = from_status
        result["to_status"] = to_status
        result["sync"] = sync
        return result

    def _fail(
        self,
        *,
        issue_key: str,
        to_status: str,
        channel: str,
        error: str,
        cache_key: str = "",
        release_quota: bool = False,
    ) -> Dict[str, Any]:
        if cache_key:
            _IDEMPOTENCY.clear(cache_key)
        if release_quota:
            self._quota.release()

        comment = (
            f"{BIGAS_COMMENT_MARKER} AI handler failed; left in `{to_status}`.\n"
            f"Error: {error[:1500]}"
        )
        try:
            self._jira.add_comment(issue_key, comment)
        except JiraError:
            logger.warning("Failed to write failure comment on %s", issue_key, exc_info=True)

        discord_msg = (
            f"**Jira AI failed** `{issue_key}`\n"
            f"Left in `{to_status}`.\n"
            f"Error: {error[:500]}"
        )
        if channel == "cto":
            self._notify_cto(discord_msg)
        else:
            self._notify_pm(discord_msg)

        return {
            "ok": False,
            "issue_key": issue_key,
            "error": error,
            "left_in_status": to_status,
        }

    def _notify_pm(self, message: str) -> None:
        url = os.environ.get(self._config.discord_pm_env)
        _post_discord(url, message)

    def _notify_cto(self, message: str) -> None:
        url = os.environ.get(self._config.discord_cto_env)
        _post_discord(url, message)
