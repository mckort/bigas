"""Move a linked issue to Final approval after the PR is merged."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

import requests

from bigas.discord_webhook import post_to_discord
from bigas.github_refs import format_pr_discord_line
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
)
from bigas.resources.product.jira_automation.comments import issue_discord_label
from bigas.resources.product.jira_automation.config import (
    BIGAS_COMMENT_MARKER,
    JiraAutomationConfig,
)

logger = logging.getLogger(__name__)

_ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def extract_jira_issue_key(*texts: str) -> Optional[str]:
    """Return the first Jira-looking issue key from the given texts."""
    for text in texts:
        if not text:
            continue
        m = _ISSUE_KEY_RE.search(text)
        if m:
            return m.group(1)
    return None


def _post_discord(message: str) -> None:
    url = (os.environ.get("DISCORD_WEBHOOK_URL_CTO") or "").strip()
    post_to_discord(
        url,
        (message or "").strip(),
        chat_agent_id="cto",
        mirror_thread=False,
    )


def _resolve_issue_client(issue_key: str):
    """Prefer the internal board when that ticket exists; otherwise Jira."""
    try:
        from bigas.tickets.jira_adapter import TicketJiraAdapter
        from bigas.tickets.store import get_ticket_store

        if get_ticket_store().get_ticket_by_key(issue_key):
            return TicketJiraAdapter()
    except Exception:
        logger.debug("Internal ticket lookup skipped for %s", issue_key, exc_info=True)
    return JiraClient(JiraConfig.from_env())


def transition_issue_to_final_approval_for_pr(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    github_token: Optional[str] = None,
    assume_merged: bool = False,
) -> Dict[str, Any]:
    """
    If a merged PR references an issue in an allowed project, move it to Final approval.

    Internal-board tickets are updated in the ticket store; otherwise Jira is used.
    Best-effort: returns skipped/ok payload; does not raise for soft misses.
    """
    token = (github_token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        return {"skipped": True, "reason": "GITHUB_TOKEN missing"}

    owner, name = repo.split("/", 1)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{name}/pulls/{pr_number}",
            headers=headers,
            timeout=30,
        )
        if resp.status_code >= 400:
            return {"skipped": True, "reason": f"github PR fetch {resp.status_code}"}
        pr = resp.json() if resp.text else {}
    except Exception as e:
        return {"skipped": True, "reason": f"github PR fetch failed: {e}"}

    if not assume_merged and not pr.get("merged"):
        return {"skipped": True, "reason": "pr_not_merged"}

    title = (pr.get("title") or "").strip()
    body = (pr.get("body") or "").strip()
    head_ref = ((pr.get("head") or {}).get("ref") or "").strip()
    issue_key = extract_jira_issue_key(title, body, head_ref)
    if not issue_key:
        return {"skipped": True, "reason": "no Jira issue key found on PR"}

    project_key = issue_key.split("-", 1)[0].upper()
    try:
        cfg = JiraAutomationConfig.from_env()
        if not cfg.is_project_allowed(project_key):
            return {
                "skipped": True,
                "reason": f"project {project_key} not in allowlist",
                "issue_key": issue_key,
            }
        client = _resolve_issue_client(issue_key)
        issue = client.get_issue(issue_key, fields=["summary", "status"])
        fields = issue.get("fields") or {}
        summary = (fields.get("summary") or "").strip()
        current = ((fields.get("status") or {}).get("name") or "").strip()

        # Already there (e.g. auto-merge plus closed webhook) — no Discord spam.
        if current.casefold() == cfg.status_final_approval.casefold():
            return {
                "ok": True,
                "skipped": True,
                "reason": "already_in_final_approval",
                "issue_key": issue_key,
                "summary": summary,
                "from_status": current,
                "moved_to": cfg.status_final_approval,
                "pr_url": pr_url,
            }

        client.transition_issue(
            issue_key,
            to_status_name=cfg.status_final_approval,
            comment=(
                f"{BIGAS_COMMENT_MARKER} PR merged — moved to "
                f"{cfg.status_final_approval}.\nPR: {pr_url}"
            ),
        )
        _post_discord(
            f"**PR merged — final approval** {issue_discord_label(issue_key, summary)}\n"
            f"Was `{current}` → **{cfg.status_final_approval}**\n"
            f"{format_pr_discord_line(pr_url, title)}"
        )
        return {
            "ok": True,
            "issue_key": issue_key,
            "summary": summary,
            "from_status": current,
            "moved_to": cfg.status_final_approval,
            "pr_url": pr_url,
        }
    except JiraError as e:
        logger.warning("Final approval transition failed for %s: %s", issue_key, e)
        return {"ok": False, "issue_key": issue_key, "error": str(e)}
    except Exception as e:
        logger.warning("Final approval hook failed", exc_info=True)
        return {"ok": False, "error": str(e)}
