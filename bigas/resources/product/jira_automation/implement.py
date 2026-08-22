"""In progress (AI) handler — launch Cursor cloud agent to implement + open PR."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional

import requests

from bigas.discord_webhook import post_to_discord
from bigas.github_refs import format_pr_discord_line, parse_github_pr
from bigas.resources.cto.autofix.cursor_client import (
    CursorCloudAgentClient,
    CursorCloudAgentError,
)
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraError,
    adf_to_plain_text,
)
from bigas.resources.product.jira_automation.comments import (
    format_human_comments,
    issue_discord_label,
)
from bigas.resources.product.jira_automation.config import BIGAS_COMMENT_MARKER
from bigas.resources.product.jira_automation.description import (
    PLAN_HEADING,
    RESEARCH_HEADING,
    extract_brief,
    extract_section,
)
from bigas.resources.product.jira_automation.prompts import (
    build_implement_prompt_product,
    implement_prompt_for,
    resolve_workstream,
)

logger = logging.getLogger(__name__)


class ImplementHandlerError(RuntimeError):
    pass


def _slugify(text: str, *, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s or "work")[:max_len].strip("-") or "work"


def _monitor_seconds() -> int:
    raw = (os.environ.get("BIGAS_JIRA_IMPLEMENT_MONITOR_SECONDS") or "900").strip()
    try:
        return max(60, int(raw))
    except ValueError:
        return 900


def _sync_wait_seconds() -> int:
    """
    Inline poll budget after launch (Cloud Run only has reliable CPU during the request).
    Long enough to catch agents that stop early to ask for confirmation (~2–3 min).
    """
    raw = (os.environ.get("BIGAS_JIRA_IMPLEMENT_SYNC_WAIT_SECONDS") or "240").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 240


def _monitor_interval_seconds() -> int:
    raw = (os.environ.get("BIGAS_JIRA_IMPLEMENT_MONITOR_INTERVAL_SECONDS") or "30").strip()
    try:
        return max(10, int(raw))
    except ValueError:
        return 30


# Backward-compatible alias (product workstream).
build_implement_prompt = build_implement_prompt_product

def lookup_pr_for_branch(*, repo: str, branch_name: str) -> tuple[str, str]:
    """Return (html_url, title) for an open PR on branch, or ("", "")."""
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    branch = (branch_name or "").strip()
    if not token or not branch or "/" not in repo:
        return "", ""
    owner, name = repo.split("/", 1)
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{name}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"state": "open", "head": f"{owner}:{branch}", "per_page": 5},
            timeout=30,
        )
        if resp.status_code >= 400:
            return "", ""
        items = resp.json() if resp.text else []
        if isinstance(items, list) and items:
            url = (items[0].get("html_url") or "").strip()
            title = (items[0].get("title") or "").strip()
            return url, title
    except Exception:
        logger.warning("GitHub PR lookup failed for %s@%s", repo, branch, exc_info=True)
    return "", ""


def lookup_pr_url_for_branch(*, repo: str, branch_name: str) -> str:
    """Return open PR URL for branch if found via GitHub API."""
    url, _title = lookup_pr_for_branch(repo=repo, branch_name=branch_name)
    return url


def _github_pr_title(pr_url: str) -> str:
    parsed = parse_github_pr(pr_url)
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not parsed or not token:
        return ""
    repo, number = parsed
    owner, name = repo.split("/", 1)
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{name}/pulls/{number}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ""
        data = resp.json() if resp.text else {}
        if not isinstance(data, dict):
            return ""
        return (data.get("title") or "").strip()
    except Exception:
        logger.warning("GitHub PR title fetch failed for %s", pr_url, exc_info=True)
        return ""


def evaluate_implementation_outcome(status: Dict[str, Any], *, repo: str) -> Dict[str, Any]:
    """
    Classify a terminal Cursor run for implement monitoring.

    Returns keys: kind (pr_opened|finished_no_pr|failed), pr_url, pr_title, status, agent_url, detail
    """
    st = (status.get("status") or "").strip().upper()
    agent_url = (status.get("agent_url") or "").strip()
    pr_url = (status.get("pr_url") or "").strip()
    pr_title = (status.get("pr_title") or "").strip()
    branch = (status.get("branch_name") or "").strip()
    if not pr_url and branch:
        pr_url, looked_title = lookup_pr_for_branch(repo=repo, branch_name=branch)
        pr_title = pr_title or looked_title
    elif pr_url and not pr_title:
        pr_title = _github_pr_title(pr_url)

    if st == "FINISHED" and pr_url:
        return {
            "kind": "pr_opened",
            "pr_url": pr_url,
            "pr_title": pr_title,
            "status": st,
            "agent_url": agent_url,
            "branch_name": branch,
            "detail": "",
        }
    if st == "FINISHED":
        return {
            "kind": "finished_no_pr",
            "pr_url": "",
            "status": st,
            "agent_url": agent_url,
            "branch_name": branch,
            "detail": (
                "Cursor agent finished without opening a PR "
                "(often stopped to ask for confirmation)."
            ),
        }
    return {
        "kind": "failed",
        "pr_url": pr_url,
        "status": st or "UNKNOWN",
        "agent_url": agent_url,
        "branch_name": branch,
        "detail": f"Cursor agent ended with status {st or 'UNKNOWN'}.",
    }


def _post_discord_cto(message: str) -> None:
    url = (os.environ.get("DISCORD_WEBHOOK_URL_CTO") or "").strip()
    post_to_discord(url, (message or "").strip(), chat_agent_id="cto")


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
        workstream = resolve_workstream(fields.get("labels") or [])
        build_prompt = implement_prompt_for(workstream)

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

        prompt = build_prompt(
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
            f"{BIGAS_COMMENT_MARKER} Implementation started via Cursor cloud agent "
            f"(workstream={workstream}).\n"
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

        outcome: Optional[Dict[str, Any]] = None
        monitor_started = False
        if agent_id:
            # Prefer inline wait so Cloud Run CPU stays allocated for early aborts.
            outcome = self._poll_until_terminal(
                agent_id=agent_id,
                run_id=run_id,
                repo=repo,
                timeout_seconds=_sync_wait_seconds(),
            )
            if outcome:
                self._report_implementation_outcome(
                    issue_key=issue_key,
                    label=issue_discord_label(issue_key, summary),
                    outcome=outcome,
                    agent_url=agent_url,
                    agent_id=agent_id,
                )
            else:
                # Still running — best-effort background monitor (needs non-throttled CPU).
                self._start_outcome_monitor(
                    issue_key=issue_key,
                    summary=summary,
                    repo=repo,
                    agent_id=agent_id,
                    run_id=run_id,
                    agent_url=agent_url,
                )
                monitor_started = True

        return {
            "ok": True,
            "handler": "implement",
            "issue_key": issue_key,
            "summary": summary,
            "repo": repo,
            "workstream": workstream,
            "base_branch": base_branch,
            "agent_id": agent_id,
            "agent_url": agent_url,
            "run_id": run_id,
            "left_in_status": "In Progress (AI)",
            "had_plan_section": bool(plan.strip()),
            "had_research_section": bool(research.strip()),
            "human_comments_included": comments_text != "(none)",
            "monitor_started": monitor_started,
            "outcome": outcome,
        }

    def _start_outcome_monitor(
        self,
        *,
        issue_key: str,
        summary: str,
        repo: str,
        agent_id: str,
        run_id: str,
        agent_url: str,
    ) -> None:
        t = threading.Thread(
            target=self._monitor_implementation_outcome,
            kwargs={
                "issue_key": issue_key,
                "summary": summary,
                "repo": repo,
                "agent_id": agent_id,
                "run_id": run_id,
                "agent_url": agent_url,
            },
            name=f"jira-implement-monitor-{issue_key}",
            daemon=True,
        )
        t.start()

    def _poll_until_terminal(
        self,
        *,
        agent_id: str,
        run_id: str,
        repo: str,
        timeout_seconds: int,
    ) -> Optional[Dict[str, Any]]:
        if timeout_seconds <= 0:
            return None
        deadline = time.time() + timeout_seconds
        interval = _monitor_interval_seconds()
        # First poll after a short delay so launch can register.
        time.sleep(min(15, interval))
        while time.time() < deadline:
            try:
                status = self._cursor.get_run_status(
                    agent_id=agent_id, run_id=run_id or None
                )
            except CursorCloudAgentError as e:
                logger.warning(
                    "Implement sync poll failed for %s: %s", agent_id, e
                )
                time.sleep(interval)
                continue
            if status.get("done"):
                return evaluate_implementation_outcome(status, repo=repo)
            time.sleep(interval)
        return None

    def _monitor_implementation_outcome(
        self,
        *,
        issue_key: str,
        summary: str,
        repo: str,
        agent_id: str,
        run_id: str,
        agent_url: str,
    ) -> None:
        """Background poll until terminal; comment + Discord on no PR / failure."""
        remaining = max(60, _monitor_seconds() - _sync_wait_seconds())
        outcome = self._poll_until_terminal(
            agent_id=agent_id,
            run_id=run_id,
            repo=repo,
            timeout_seconds=remaining,
        )
        label = issue_discord_label(issue_key, summary)
        if outcome:
            self._report_implementation_outcome(
                issue_key=issue_key,
                label=label,
                outcome=outcome,
                agent_url=agent_url or outcome.get("agent_url") or "",
                agent_id=agent_id,
            )
            return

        detail = f"still running after ~{_monitor_seconds()}s"
        comment = (
            f"{BIGAS_COMMENT_MARKER} Implementation still in progress "
            f"(monitor timed out).\n"
            f"Agent: {agent_url or agent_id}\n"
            f"Detail: {detail}\n"
            f"Left in In Progress (AI). Re-check the agent or move the card manually."
        )
        try:
            self._jira.add_comment(issue_key, comment)
        except JiraError:
            logger.warning(
                "Failed to write implement monitor timeout comment on %s",
                issue_key,
                exc_info=True,
            )
        _post_discord_cto(
            f"**Implementation monitor timeout** {label}\n"
            f"Left in **In Progress (AI)**.\n"
            f"Agent: {agent_url or agent_id}\n"
            f"{detail}"
        )

    def _report_implementation_outcome(
        self,
        *,
        issue_key: str,
        label: str,
        outcome: Dict[str, Any],
        agent_url: str,
        agent_id: str,
    ) -> None:
        kind = outcome.get("kind")
        pr_url = (outcome.get("pr_url") or "").strip()
        detail = (outcome.get("detail") or "").strip()
        st = outcome.get("status") or ""

        if kind == "pr_opened":
            comment = (
                f"{BIGAS_COMMENT_MARKER} Implementation agent opened a PR.\n"
                f"PR: {pr_url}\n"
                f"Agent: {agent_url or agent_id}\n"
                f"Left in In Progress (AI) until the PR is ready to merge."
            )
            try:
                self._jira.add_comment(issue_key, comment)
            except JiraError:
                logger.warning(
                    "Failed to write PR-opened comment on %s", issue_key, exc_info=True
                )
            _post_discord_cto(
                f"**Implementation PR opened** {label}\n"
                f"{format_pr_discord_line(pr_url, outcome.get('pr_title') or '')}\n"
                f"Agent: {agent_url or agent_id}"
            )
            return

        # finished_no_pr or failed — leave in column, surface clearly
        comment = (
            f"{BIGAS_COMMENT_MARKER} Implementation agent ended without a usable PR; "
            f"left in In Progress (AI).\n"
            f"Status: {st}\n"
            f"Agent: {agent_url or agent_id}\n"
            f"{detail}\n"
            f"Re-run In Progress (AI) after fixing, or continue the agent manually."
        )
        try:
            self._jira.add_comment(issue_key, comment)
        except JiraError:
            logger.warning(
                "Failed to write implement stuck comment on %s", issue_key, exc_info=True
            )
        title = (
            "**Implementation stuck (no PR)**"
            if kind == "finished_no_pr"
            else "**Implementation failed**"
        )
        _post_discord_cto(
            f"{title} {label}\n"
            f"Left in **In Progress (AI)**.\n"
            f"Status: `{st}`\n"
            f"Agent: {agent_url or agent_id}\n"
            f"{detail}"
        )
