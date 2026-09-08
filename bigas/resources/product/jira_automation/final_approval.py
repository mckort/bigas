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
from bigas.resources.product.release_workflow import (
    feature_branch_prefix,
    is_versioned_feature_head,
    version_from_feature_branch,
)

logger = logging.getLogger(__name__)

_ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_RELEASE_TITLE_RE = re.compile(
    r"^(release\s+|prepare deploy\b|resolve conflicts\b)",
    re.I,
)


def extract_jira_issue_key(*texts: str) -> Optional[str]:
    """Return the first Jira-looking issue key from the given texts."""
    for text in texts:
        if not text:
            continue
        m = _ISSUE_KEY_RE.search(text)
        if m:
            return m.group(1)
    return None


def title_with_issue_key(title: str, issue_key: str) -> str:
    """Prefix a PR title with the ticket key unless it already has that key."""
    key = (issue_key or "").strip().upper()
    raw = (title or "").strip()
    if not key:
        return raw
    if extract_jira_issue_key(raw) == key:
        return raw
    return f"{key}: {raw}" if raw else key


def squash_commit_title(title: str, pr_number: int) -> str:
    """PR title plus number so repo squash defaults cannot drop the ticket key."""
    number = int(pr_number)
    raw = (title or "").strip() or "Merge pull request"
    if re.search(rf"\s*\(#{number}\)\s*$", raw):
        return raw
    return f"{raw} (#{number})"


def should_skip_auto_ticket(
    pr: Dict[str, Any],
    *,
    repo: str,
    project_key: str,
    cfg: Optional[JiraAutomationConfig] = None,
) -> Optional[str]:
    """Return a skip reason when this PR must not create a board ticket."""
    user = (((pr.get("user") or {}).get("login") or "")).strip().lower()
    head = (((pr.get("head") or {}).get("ref") or "")).strip()
    base = (((pr.get("base") or {}).get("ref") or "")).strip()
    title = (pr.get("title") or "").strip()
    if "dependabot" in user or head.startswith("dependabot/"):
        return "dependabot"
    if _RELEASE_TITLE_RE.match(title):
        return "release_pr"
    if head.startswith("bigas-rebase/"):
        return "release_pr"
    resolved = cfg or JiraAutomationConfig.from_env()
    feature = (resolved.automerge_branch_for_project(project_key, repo) or "").strip()
    production = (resolved.base_branch_for_repo(repo) or "").strip()
    if feature and production and feature != production:
        if head == feature and base == production:
            return "release_pr"
        if is_versioned_feature_head(head, feature) and base == production:
            return "release_pr"
    return None


def find_ticket_by_pr_url(project_key: str, pr_url: str) -> Optional[Dict[str, Any]]:
    """Return an existing board ticket whose description mentions this PR URL."""
    wanted = (pr_url or "").strip()
    if not wanted:
        return None
    from bigas.tickets.store import get_ticket_store

    for ticket in get_ticket_store().list_tickets_by_project(project_key):
        if wanted in (ticket.get("description") or ""):
            return ticket
    return None


def _ticket_fix_version(issue_key: str) -> Optional[str]:
    try:
        from bigas.tickets.store import get_ticket_store

        ticket = get_ticket_store().get_ticket_by_key(issue_key)
    except Exception:
        return None
    if not ticket:
        return None
    return (ticket.get("fix_version") or "").strip() or None


def _align_pr_base_to_board_release(
    *,
    repo: str,
    pr: Dict[str, Any],
    project_key: str,
    issue_key: str,
    github_token: str,
    pr_number: Optional[int],
) -> Optional[str]:
    """Create staging-x.y.z from the board default and retarget an unversioned base."""
    current = (((pr.get("base") or {}).get("ref") or "")).strip()
    if not current or version_from_feature_branch(current):
        return None

    from bigas.resources.product.fix_version import ensure_active_fix_version
    from bigas.resources.product.release_branches import resolve_implement_base_branch
    from bigas.tickets.jira_adapter import TicketJiraAdapter

    fix_version = ensure_active_fix_version(
        TicketJiraAdapter(),
        issue_key=issue_key,
        project_key=project_key,
    ) or _ticket_fix_version(issue_key)
    if not fix_version:
        return None

    wanted = resolve_implement_base_branch(
        project_key=project_key,
        repo=repo,
        fix_version=fix_version,
    )
    if not wanted or wanted == current or not version_from_feature_branch(wanted):
        return None
    if current != feature_branch_prefix(wanted):
        return None

    number = pr_number or pr.get("number")
    token = (github_token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token or not number:
        return None
    if not _update_pr_title_and_body(
        repo=repo,
        pr_number=int(number),
        base=wanted,
        github_token=token,
    ):
        return None
    if not pr.get("base"):
        pr["base"] = {}
    pr["base"]["ref"] = wanted
    return wanted


def _update_pr_title_and_body(
    *,
    repo: str,
    pr_number: int,
    title: Optional[str] = None,
    body: Optional[str] = None,
    base: Optional[str] = None,
    github_token: str,
) -> bool:
    owner, name = repo.split("/", 1)
    payload: Dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if base is not None:
        payload["base"] = base
    if not payload:
        return False
    try:
        resp = requests.patch(
            f"https://api.github.com/repos/{owner}/{name}/pulls/{pr_number}",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Could not retitle PR %s#%s (%s)",
                repo,
                pr_number,
                resp.status_code,
            )
            return False
        return True
    except Exception:
        logger.warning("Could not retitle PR %s#%s", repo, pr_number, exc_info=True)
        return False


def ensure_board_ticket_for_pr(
    *,
    repo: str,
    pr: Dict[str, Any],
    pr_url: str,
    github_token: str = "",
    pr_number: Optional[int] = None,
    status: str = "To Do",
    retitle: bool = True,
) -> Dict[str, Any]:
    """
    Create an internal-board ticket when a PR has no key, and optionally
    prefix the PR title so squash-merge carries ``VFA-51: …``.
    """
    if not isinstance(pr, dict) or not pr:
        return {"skipped": True, "reason": "no_pr"}

    title = (pr.get("title") or "").strip()
    body = (pr.get("body") or "").strip()
    head_ref = (((pr.get("head") or {}).get("ref") or "")).strip()
    issue_key = extract_jira_issue_key(title, body, head_ref)

    from bigas.tickets.releases import project_key_for_repo

    if issue_key:
        project_key = issue_key.split("-", 1)[0].upper()
    else:
        project_key = (project_key_for_repo(repo) or "").strip().upper()
    if not project_key:
        return {"skipped": True, "reason": "no_project_for_repo"}

    cfg = JiraAutomationConfig.from_env()
    if not cfg.is_project_allowed(project_key):
        return {
            "skipped": True,
            "reason": f"project {project_key} not in allowlist",
            "project_key": project_key,
        }

    skip = should_skip_auto_ticket(pr, repo=repo, project_key=project_key, cfg=cfg)
    if skip:
        return {"skipped": True, "reason": skip, "project_key": project_key}

    created = False
    if not issue_key:
        existing = find_ticket_by_pr_url(project_key, pr_url)
        if existing:
            issue_key = (existing.get("key") or "").strip()
        else:
            from bigas.tickets.service import TicketService

            head_sha = (((pr.get("head") or {}).get("sha") or "")).strip()
            desc_parts = [
                "Opened from GitHub (no ticket key on the PR).",
                f"PR: {pr_url}",
            ]
            if head_sha:
                desc_parts.append(f"Head: {head_sha}")
            if body:
                desc_parts.append(body)
            try:
                ticket = TicketService().create_ticket_for_project(
                    project_key,
                    title=title or f"Changes from {pr_url}",
                    description="\n\n".join(desc_parts),
                    issue_type="Task",
                    status=status,
                    git_ref=head_ref or (((pr.get("base") or {}).get("ref") or "")).strip(),
                )
            except Exception as exc:
                logger.warning("Auto-create board ticket failed for %s", pr_url, exc_info=True)
                return {"ok": False, "reason": f"create_failed: {exc}", "project_key": project_key}
            issue_key = (ticket.get("key") or "").strip()
            created = True
            if issue_key:
                _post_discord(
                    f"**Created board ticket** {issue_discord_label(issue_key, title)}\n"
                    f"{format_pr_discord_line(pr_url, title)}"
                )

    if not issue_key:
        return {"ok": False, "reason": "create_failed", "project_key": project_key}

    try:
        from bigas.resources.product.fix_version import ensure_active_fix_version
        from bigas.tickets.jira_adapter import TicketJiraAdapter

        ensure_active_fix_version(
            TicketJiraAdapter(),
            issue_key=issue_key,
            project_key=project_key,
        )
    except Exception:
        logger.warning("Could not assign fix version on %s", issue_key, exc_info=True)

    new_title = title_with_issue_key(title, issue_key)
    new_body = body
    if issue_key not in body:
        new_body = f"{issue_key}\n\n{body}".strip() if body else issue_key
    number = pr_number or pr.get("number")
    retitled = False
    token = (github_token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if (
        retitle
        and token
        and number
        and (new_title != title or new_body != body)
    ):
        retitled = _update_pr_title_and_body(
            repo=repo,
            pr_number=int(number),
            title=new_title,
            body=new_body if new_body != body else None,
            github_token=token,
        )
        if retitled:
            pr["title"] = new_title
            if new_body != body:
                pr["body"] = new_body

    retargeted_base = _align_pr_base_to_board_release(
        repo=repo,
        pr=pr,
        project_key=project_key,
        issue_key=issue_key,
        github_token=github_token,
        pr_number=pr_number,
    )

    return {
        "ok": True,
        "created": created,
        "issue_key": issue_key,
        "title": (pr.get("title") or new_title or title).strip(),
        "retitled": retitled,
        "retargeted_base": retargeted_base,
        "project_key": project_key,
        "pr_url": pr_url,
    }


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
    created = False
    cfg = JiraAutomationConfig.from_env()
    if not issue_key:
        ensured = ensure_board_ticket_for_pr(
            repo=repo,
            pr=pr,
            pr_url=pr_url,
            github_token=token,
            pr_number=pr_number,
            status=cfg.status_final_approval,
            retitle=False,
        )
        issue_key = (ensured.get("issue_key") or "").strip()
        created = bool(ensured.get("created"))
        if not issue_key:
            return {
                "skipped": True,
                "reason": ensured.get("reason") or "no Jira issue key found on PR",
                "pr_url": pr_url,
            }

    project_key = issue_key.split("-", 1)[0].upper()
    try:
        if not cfg.is_project_allowed(project_key):
            return {
                "skipped": True,
                "reason": f"project {project_key} not in allowlist",
                "issue_key": issue_key,
            }
        client = _resolve_issue_client(issue_key)
        try:
            from bigas.resources.product.fix_version import ensure_active_fix_version

            ensure_active_fix_version(
                client,
                issue_key=issue_key,
                project_key=project_key,
            )
        except Exception:
            logger.warning("Could not assign fix version on %s", issue_key, exc_info=True)
        issue = client.get_issue(issue_key, fields=["summary", "status"])
        fields = issue.get("fields") or {}
        summary = (fields.get("summary") or "").strip()
        current = ((fields.get("status") or {}).get("name") or "").strip()

        try:
            from bigas.tickets.review import attach_review_from_pr

            attach_review_from_pr(
                issue_key,
                pr_url=pr_url,
                pr_title=title,
                github_token=token,
                comment=True,
            )
        except Exception:
            logger.warning(
                "Failed to attach review links on %s", issue_key, exc_info=True
            )

        # Already there (e.g. auto-merge plus closed webhook) — no Discord spam.
        if current.casefold() == cfg.status_final_approval.casefold():
            reason = (
                "created_in_final_approval" if created else "already_in_final_approval"
            )
            return {
                "ok": True,
                "skipped": not created,
                "created": created,
                "reason": reason,
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
            "created": created,
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
