"""Versioned prepare-deploy: merge to main, risk report, confirm, close, notes."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bigas.portfolio import brand_name, jira_project_keys, normalize_project_key, resolve_project
from bigas.resources.devops.service import DevOpsError, check_deployment_risk, list_shipping_commits
from bigas.resources.product.create_jira_issue.lookup import parse_issue_keys
from bigas.tickets.constants import is_in_release_cut
from bigas.tickets.releases import tickets_on_version
from bigas.tickets.semver import SemverError, normalize_version_name
from bigas.tickets.store import get_ticket_store

logger = logging.getLogger(__name__)

_PREPARE_RE = re.compile(
    r"\bprepare\s+deploy\b"
    r"(?:\s+(?P<project>[A-Za-z]{2,8}))?"
    r"(?:\s+(?P<version>v?\d+\.\d+\.\d+))?",
    re.I,
)
_POLL_TIMEOUT_SEC = 45 * 60


def is_prepare_start(text: str) -> bool:
    return bool(_PREPARE_RE.search(text or ""))


def parse_prepare_command(text: str) -> Dict[str, str]:
    match = _PREPARE_RE.search(text or "")
    project = ""
    version = ""
    if match:
        project = normalize_project_key(match.group("project"))
        version = (match.group("version") or "").strip()
    if not project:
        project = resolve_project(text or "") or ""
    if version:
        try:
            version = normalize_version_name(version)
        except SemverError:
            version = version.lstrip("vV")
    return {"project_key": project, "version": version}


def _store():
    from bigas.chat.db import get_chat_store

    return get_chat_store()


def _post(
    thread_id: Optional[str],
    content: str,
    *,
    role: str = "assistant",
    status: Optional[str] = None,
) -> None:
    if not thread_id or not (content or "").strip():
        return
    meta: Dict[str, Any] = {"agent_id": "devops", "pipeline": True}
    if status:
        meta["status"] = status
    _store().add_message(thread_id, role=role, content=content.strip(), metadata=meta)


def _complete_pipeline_progress(thread_id: Optional[str]) -> None:
    if not thread_id:
        return
    store = _store()
    if not hasattr(store, "patch_message"):
        return
    for message in store.list_messages(thread_id):
        meta = message.get("metadata") or {}
        if meta.get("pipeline") and meta.get("status") == "in_progress":
            store.patch_message(message["message_id"], metadata={"status": "complete"})


def _thread_get(thread_id: Optional[str], key: str) -> Optional[Dict[str, Any]]:
    if not thread_id:
        return None
    thread = _store().get_thread(thread_id) or {}
    value = thread.get(key)
    return value if isinstance(value, dict) else None


def _thread_set(thread_id: Optional[str], **fields: Any) -> None:
    if not thread_id or not hasattr(_store(), "patch_thread"):
        return
    _store().patch_thread(thread_id, **fields)


def pending_release_notes(thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    return _thread_get(thread_id, "pending_release_notes")


def pending_prepare_poll(thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    return _thread_get(thread_id, "pending_prepare_poll")


def clear_prepare_state(thread_id: Optional[str]) -> None:
    if not thread_id:
        return
    _thread_set(thread_id, pending_prepare_poll=None, pending_release_notes=None)


def format_version_ticket_report(
    project_key: str,
    version: str,
    tickets: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    items = tickets if tickets is not None else tickets_on_version(project_key, version)
    in_cut: List[Dict[str, Any]] = []
    open_tickets: List[Dict[str, Any]] = []
    for ticket in items:
        if is_in_release_cut(ticket.get("status") or "", project_key=project_key):
            in_cut.append(ticket)
        else:
            open_tickets.append(ticket)

    lines = [f"**{project_key} {version} — what's in this cut**"]
    if in_cut:
        lines.append("")
        lines.append(f"Included ({len(in_cut)} Done or Final approval):")
        for ticket in in_cut[:30]:
            key = ticket.get("key") or "?"
            title = (ticket.get("title") or ticket.get("summary") or "").strip()
            status = (ticket.get("status") or "").strip()
            label = key if status == "Done" else f"{key} ({status or 'in cut'})"
            lines.append(f"- {label}: {title}" if title else f"- {label}")
        if len(in_cut) > 30:
            lines.append(f"- …and {len(in_cut) - 30} more")
    else:
        lines.append("")
        lines.append("No Done or Final approval tickets are assigned to this release.")

    if open_tickets:
        lines.append("")
        lines.append(f"**Open tickets ({len(open_tickets)}) — not in this cut if you deploy:**")
        for ticket in open_tickets[:20]:
            key = ticket.get("key") or "?"
            title = (ticket.get("title") or ticket.get("summary") or "").strip()
            status = (ticket.get("status") or "open").strip()
            label = f"{key} ({status})"
            lines.append(f"- {label}: {title}" if title else f"- {label}")
        lines.append(
            "If you deploy anyway they move to the next existing unreleased version, "
            "or lose their version assignment if none exists."
        )
    return "\n".join(lines), in_cut, open_tickets


def _ticket_keys_for_project(project_key: str, text: str) -> List[str]:
    prefix = f"{normalize_project_key(project_key)}-"
    return [key for key in parse_issue_keys(text, max_keys=200) if key.startswith(prefix)]


def _first_line(message: str) -> str:
    return (message or "").split("\n", 1)[0].strip()


def format_git_reconcile_report(
    *,
    project_key: str,
    version: str,
    in_cut: List[Dict[str, Any]],
    commits: List[Dict[str, Any]],
    compared: Optional[List[str]] = None,
    truncated: bool = False,
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Match board cut tickets to commit messages on the shipping git range."""
    key = normalize_project_key(project_key)
    cut_by_key = {
        (ticket.get("key") or "").strip().upper(): ticket
        for ticket in in_cut
        if (ticket.get("key") or "").strip()
    }
    store = get_ticket_store()
    matched: List[Dict[str, Any]] = []
    extra: List[Dict[str, Any]] = []
    seen_cut: set = set()
    seen_extra_sha: set = set()

    for commit in commits:
        sha = (commit.get("sha") or "").strip()
        subject = (commit.get("subject") or _first_line(commit.get("message") or "")).strip()
        keys = _ticket_keys_for_project(key, commit.get("message") or subject)
        hit_cut = [item for item in keys if item in cut_by_key]
        if hit_cut:
            for item in hit_cut:
                if item in seen_cut:
                    continue
                seen_cut.add(item)
                matched.append(
                    {
                        "key": item,
                        "ticket": cut_by_key[item],
                        "sha": sha,
                        "subject": subject,
                    }
                )
            continue

        reason = "no ticket key"
        other_version = ""
        open_on_cut = ""
        if keys:
            found = None
            for item in keys:
                found = store.get_ticket_by_key(item)
                if found:
                    break
            if not found:
                reason = f"{keys[0]} has no board ticket"
            elif is_in_release_cut(found.get("status") or "", project_key=key):
                assigned = (found.get("fix_version") or "").strip() or "unassigned"
                other_version = assigned
                reason = f"{found.get('key')} is on {assigned}, not {version}"
            else:
                open_on_cut = found.get("key") or keys[0]
                reason = (
                    f"{open_on_cut} is {(found.get('status') or 'open').strip()} "
                    f"— not in this cut"
                )
        if sha and sha in seen_extra_sha:
            continue
        if sha:
            seen_extra_sha.add(sha)
        extra.append(
            {
                "sha": sha,
                "subject": subject or "(no subject)",
                "keys": keys,
                "reason": reason,
                "other_version": other_version,
                "open_on_cut": open_on_cut,
            }
        )

    compared_ok = bool(compared) or (commits and not errors)
    missing = [
        ticket
        for item, ticket in cut_by_key.items()
        if item not in seen_cut
    ] if compared_ok else []
    extra = extra if compared_ok else []

    lines = [f"**Git vs board for {key} {version}**"]
    if compared:
        lines.append("Compared `" + "`, `".join(compared) + "`.")
    elif errors:
        lines.append("Could not compare git to this cut.")
    else:
        lines.append("No production tag or branch range to compare.")

    if truncated:
        lines.append("GitHub returned a truncated commit list (more than 250 commits).")
    if errors:
        lines.append("Compare errors: " + "; ".join(errors))

    if not compared_ok:
        lines.append("Skipping ticket↔commit matching until git compare succeeds.")
        return {
            "text": "\n".join(lines),
            "matched": [],
            "missing_from_git": [],
            "extra_commits": [],
            "needs_confirm": bool(errors),
            "errors": list(errors or []),
        }

    if matched:
        lines.append("")
        lines.append(f"In this cut and in git ({len(matched)}):")
        for row in matched[:30]:
            ticket = row["ticket"]
            title = (ticket.get("title") or ticket.get("summary") or "").strip()
            short = (row.get("sha") or "")[:7]
            suffix = f" `{short}`" if short else ""
            lines.append(
                f"- {row['key']}: {title}{suffix}" if title else f"- {row['key']}{suffix}"
            )
        if len(matched) > 30:
            lines.append(f"- …and {len(matched) - 30} more")

    if missing:
        lines.append("")
        lines.append(
            f"**On this cut, not in git ({len(missing)})** — code may not be on the branch:"
        )
        for ticket in missing[:20]:
            tkey = ticket.get("key") or "?"
            title = (ticket.get("title") or ticket.get("summary") or "").strip()
            status = (ticket.get("status") or "").strip()
            label = f"{tkey} ({status})" if status else tkey
            lines.append(f"- {label}: {title}" if title else f"- {label}")
        if len(missing) > 20:
            lines.append(f"- …and {len(missing) - 20} more")

    if extra:
        lines.append("")
        lines.append(
            f"**Also shipping — not a ticket on {version} ({len(extra)}):**"
        )
        for row in extra[:20]:
            short = (row.get("sha") or "")[:7]
            subject = row.get("subject") or "(no subject)"
            reason = row.get("reason") or ""
            prefix = f"`{short}` " if short else ""
            lines.append(f"- {prefix}{subject} — {reason}")
        if len(extra) > 20:
            lines.append(f"- …and {len(extra) - 20} more")

    if not matched and not missing and not extra and not errors:
        lines.append("")
        lines.append("No commits between prod and the feature branch.")

    needs_confirm = bool(missing or extra)
    return {
        "text": "\n".join(lines),
        "matched": matched,
        "missing_from_git": missing,
        "extra_commits": extra,
        "needs_confirm": needs_confirm,
        "errors": list(errors or []),
    }


def reconcile_release_with_git(
    *,
    project_key: str,
    version: str,
    in_cut: List[Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        shipping = list_shipping_commits(project_key=project_key)
    except Exception as exc:
        logger.exception("list_shipping_commits failed")
        return format_git_reconcile_report(
            project_key=project_key,
            version=version,
            in_cut=in_cut,
            commits=[],
            errors=[str(exc)],
        )
    return format_git_reconcile_report(
        project_key=project_key,
        version=version,
        in_cut=in_cut,
        commits=shipping.get("commits") or [],
        compared=shipping.get("compared") or [],
        truncated=bool(shipping.get("truncated")),
        errors=shipping.get("errors") or [],
    )


def _github_client():
    from bigas.resources.devops.github_actions import GitHubActionsClient, GitHubActionsError

    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        raise GitHubActionsError("GITHUB_TOKEN is required to prepare a release PR")
    return GitHubActionsClient(token)


def _project_repo(project_key: str) -> str:
    from bigas.resources.product.jira_automation.config import JiraAutomationConfig

    cfg = JiraAutomationConfig.from_env()
    repo = (cfg.repo_for_project(project_key) or "").strip()
    if not repo or "/" not in repo:
        raise DevOpsError(f"No GitHub repo mapped for {project_key}")
    return repo


def _branch_pair(project_key: str, repo: str) -> Tuple[str, str]:
    from bigas.resources.product.jira_automation.config import JiraAutomationConfig

    cfg = JiraAutomationConfig.from_env()
    feature = (cfg.automerge_branch_for_project(project_key, repo) or "main").strip()
    production = (cfg.base_branch_for_repo(repo) or "main").strip()
    return feature, production


def ensure_release_on_main(
    *,
    project_key: str,
    version: str,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create/reuse a feature→main PR when needed. Returns merged or polling."""
    from bigas.resources.devops.github_actions import GitHubActionsError

    repo = _project_repo(project_key)
    feature, production = _branch_pair(project_key, repo)
    if feature == production:
        return {"status": "already_on_main", "repo": repo, "ref": production}

    owner, name = repo.split("/", 1)
    client = _github_client()
    try:
        compare = client.compare_refs(owner, name, production, feature)
    except GitHubActionsError as exc:
        raise DevOpsError(str(exc)) from exc

    ahead = int(compare.get("ahead_by") or 0)
    if ahead <= 0:
        return {"status": "already_on_main", "repo": repo, "ref": production}

    existing = client.find_open_pull_request(
        owner, name, head=feature, base=production
    )
    if existing:
        pr_number = int(existing.get("number") or 0)
        pr_url = (existing.get("html_url") or "").strip()
        _post(
            thread_id,
            f"🔗 **Release PR:** reusing [{repo}#{pr_number}]({pr_url}) "
            f"({feature} → {production}, {ahead} commit(s) ahead).",
        )
    else:
        created = client.create_pull_request(
            owner,
            name,
            title=f"Release {version}: merge {feature} into {production}",
            body=(
                f"Prepare deploy of **{project_key} {version}**.\n\n"
                f"Merges `{feature}` into `{production}` so production can ship this cut."
            ),
            head=feature,
            base=production,
        )
        pr_number = int(created.get("number") or 0)
        pr_url = (created.get("html_url") or "").strip()
        _post(
            thread_id,
            f"🔗 **Release PR opened:** [{repo}#{pr_number}]({pr_url}) "
            f"({feature} → {production}, {ahead} commit(s) ahead).",
        )

    if not pr_number:
        raise DevOpsError("Could not resolve a pull request number for the release merge")
    return review_and_merge_release_pr(
        repo=repo,
        pr_number=pr_number,
        thread_id=thread_id,
        project_key=project_key,
        version=version,
    )


def review_and_merge_release_pr(
    *,
    repo: str,
    pr_number: int,
    thread_id: Optional[str] = None,
    project_key: str = "",
    version: str = "",
    phase: str = "initial",
) -> Dict[str, Any]:
    from bigas.resources.cto.autofix.heuristics import (
        review_is_ready_to_merge,
        review_needs_autofix,
    )
    from bigas.resources.cto.pr_review.github_client import (
        BIGAS_REVIEW_MARKER,
        GitHubPRCommentClient,
        GitHubPRCommentError,
    )
    from bigas.resources.cto.pr_review.service import PRReviewError, PRReviewService

    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    owner, repo_name = repo.split("/", 1)
    gh = GitHubPRCommentClient(token=token)
    pr = gh.get_pull_request(owner, repo_name, pr_number)
    pr_url = (pr.get("html_url") or f"https://github.com/{repo}/pull/{pr_number}").strip()

    if pr.get("merged"):
        _post(thread_id, f"✅ Release PR already merged: {pr_url}")
        return {
            "status": "merged",
            "repo": repo,
            "pr_url": pr_url,
            "ref": "main",
            "project_key": project_key,
            "version": version,
        }

    if pr.get("draft"):
        try:
            gh.mark_pull_request_ready_for_review(owner, repo_name, pr_number)
        except GitHubPRCommentError as exc:
            logger.warning("Could not mark release PR ready: %s", exc)

    _post(
        thread_id,
        "🔎 **Review:** running CTO review on the release PR…",
        role="system",
        status="in_progress",
    )
    try:
        diff = gh.get_pr_diff(owner, repo_name, pr_number)
        review = PRReviewService().review(diff=diff, phase=phase)
        review_body = review.text
        gh.post_or_update_pr_comment(
            owner=owner,
            repo=repo_name,
            pr_number=pr_number,
            body=review_body,
            marker=BIGAS_REVIEW_MARKER,
        )
    except (PRReviewError, GitHubPRCommentError) as exc:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Release PR review failed: {exc}")
        return {"status": "failed", "summary": str(exc), "pr_url": pr_url}

    if review_is_ready_to_merge(review_body):
        return _merge_or_wait(
            gh,
            owner=owner,
            repo_name=repo_name,
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            thread_id=thread_id,
            project_key=project_key,
            version=version,
        )

    needs, reason = review_needs_autofix(review_body)
    if not needs:
        return _merge_or_wait(
            gh,
            owner=owner,
            repo_name=repo_name,
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            thread_id=thread_id,
            project_key=project_key,
            version=version,
        )

    return _launch_autofix_and_poll(
        repo=repo,
        pr_number=pr_number,
        pr_url=pr_url,
        review_body=review_body,
        thread_id=thread_id,
        project_key=project_key,
        version=version,
        reason=reason,
    )


def _merge_or_wait(
    gh: Any,
    *,
    owner: str,
    repo_name: str,
    repo: str,
    pr_number: int,
    pr_url: str,
    thread_id: Optional[str],
    project_key: str,
    version: str,
) -> Dict[str, Any]:
    from bigas.resources.cto.pr_review.github_client import (
        GitHubMergeNotReadyError,
        GitHubPRCommentError,
    )

    try:
        gh.merge_pull_request(
            owner,
            repo_name,
            pr_number,
            merge_method="squash",
            commit_title=f"Release {version}: merge into main" if version else None,
        )
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"✅ Merged release PR: {pr_url}")
        return {
            "status": "merged",
            "repo": repo,
            "pr_url": pr_url,
            "ref": "main",
            "project_key": project_key,
            "version": version,
        }
    except GitHubMergeNotReadyError:
        try:
            gh.enable_pull_request_auto_merge(
                owner, repo_name, pr_number, merge_method="squash"
            )
        except Exception as exc:
            logger.warning("Could not enable auto-merge on release PR: %s", exc)
        _post(
            thread_id,
            f"⏳ Waiting for required checks, then GitHub will merge {pr_url}",
            role="system",
            status="in_progress",
        )
        _thread_set(
            thread_id,
            pending_prepare_poll={
                "phase": "wait_merge",
                "repo": repo,
                "pr_number": pr_number,
                "pr_url": pr_url,
                "project_key": project_key,
                "version": version,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"status": "polling", "deploy_poll_active": True, "pr_url": pr_url}
    except GitHubPRCommentError as exc:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Could not merge the release PR: {exc}")
        return {"status": "failed", "summary": str(exc), "pr_url": pr_url}


def _launch_autofix_and_poll(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    review_body: str,
    thread_id: Optional[str],
    project_key: str,
    version: str,
    reason: str,
) -> Dict[str, Any]:
    from bigas.resources.cto.autofix.service import AutofixError, AutofixService

    _post(
        thread_id,
        f"🔧 **Autofix:** {reason or 'review needs fixes'}. Launching a CTO fix agent…",
        role="system",
        status="in_progress",
    )
    try:
        launched = AutofixService().run(
            repo=repo, pr_number=pr_number, review_body=review_body
        )
    except AutofixError as exc:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Could not launch autofix: {exc}")
        return {"status": "failed", "summary": str(exc), "pr_url": pr_url}

    if launched.get("skipped") and launched.get("review_clean"):
        return review_and_merge_release_pr(
            repo=repo,
            pr_number=pr_number,
            thread_id=thread_id,
            project_key=project_key,
            version=version,
            phase="post_autofix",
        )
    if launched.get("skipped") and not launched.get("launched"):
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            f"Autofix did not start ({launched.get('reason') or 'skipped'}). "
            f"Handle the release PR manually: {pr_url}",
        )
        return {"status": "failed", "summary": launched.get("reason") or "autofix skipped"}

    agent_url = (launched.get("agent_url") or "").strip()
    _post(
        thread_id,
        "⏳ Autofix agent running"
        + (f": {agent_url}" if agent_url else ".")
        + " I'll continue when it's done.",
        role="system",
        status="in_progress",
    )
    _thread_set(
        thread_id,
        pending_prepare_poll={
            "phase": "autofix",
            "repo": repo,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "project_key": project_key,
            "version": version,
            "agent_id": launched.get("agent_id") or "",
            "run_id": launched.get("run_id") or "",
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"status": "polling", "deploy_poll_active": True, "pr_url": pr_url}


def poll_prepare_followup(thread_id: str) -> Dict[str, Any]:
    poll = pending_prepare_poll(thread_id)
    if not poll:
        return {"status": "complete", "active": False}

    started = poll.get("started_at") or datetime.now(timezone.utc).isoformat()
    try:
        started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        started_dt = datetime.now(timezone.utc)
    if datetime.now(timezone.utc) >= started_dt + timedelta(seconds=_POLL_TIMEOUT_SEC):
        _thread_set(thread_id, pending_prepare_poll=None)
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            "⏳ Prepare timed out waiting for the release PR. "
            "Ask me to prepare deploy again, or merge the PR by hand.",
        )
        return {"status": "complete", "active": False}

    repo = poll.get("repo") or ""
    pr_number = int(poll.get("pr_number") or 0)
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not repo or not pr_number or "/" not in repo:
        _thread_set(thread_id, pending_prepare_poll=None)
        return {"status": "complete", "active": False}

    from bigas.resources.cto.pr_review.github_client import GitHubPRCommentClient

    owner, repo_name = repo.split("/", 1)
    gh = GitHubPRCommentClient(token=token)
    pr = gh.get_pull_request(owner, repo_name, pr_number)
    if pr.get("merged"):
        _thread_set(thread_id, pending_prepare_poll=None)
        _post(thread_id, f"✅ Release PR merged: {poll.get('pr_url') or pr.get('html_url')}")
        return continue_after_main_ready(
            thread_id=thread_id,
            project_key=poll.get("project_key") or "",
            version=poll.get("version") or "",
        )

    phase = poll.get("phase") or "autofix"
    if phase == "autofix":
        agent_id = (poll.get("agent_id") or "").strip()
        if agent_id:
            from bigas.resources.cto.autofix.service import AutofixError, AutofixService

            try:
                status = AutofixService().poll_status(
                    agent_id=agent_id, run_id=poll.get("run_id") or None
                )
            except AutofixError as exc:
                logger.warning("Prepare autofix poll failed: %s", exc)
                return {"status": "in_progress", "active": True}
            if not status.get("done"):
                return {"status": "in_progress", "active": True}
            if not status.get("ok"):
                _thread_set(thread_id, pending_prepare_poll=None)
                _complete_pipeline_progress(thread_id)
                _post(
                    thread_id,
                    f"Autofix failed ({status.get('status') or 'UNKNOWN'}). "
                    f"Release PR: {poll.get('pr_url')}",
                )
                return {"status": "complete", "active": False}
        _thread_set(thread_id, pending_prepare_poll=None)
        result = review_and_merge_release_pr(
            repo=repo,
            pr_number=pr_number,
            thread_id=thread_id,
            project_key=poll.get("project_key") or "",
            version=poll.get("version") or "",
            phase="post_autofix",
        )
        result.setdefault("project_key", poll.get("project_key") or "")
        result.setdefault("version", poll.get("version") or "")
        return _prepare_result_to_poll(thread_id, result)

    return {"status": "in_progress", "active": True}


def _prepare_result_to_poll(thread_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    if result.get("status") == "merged":
        return continue_after_main_ready(
            thread_id=thread_id,
            project_key=result.get("project_key") or "",
            version=result.get("version") or "",
        )
    if result.get("status") == "polling":
        return {"status": "in_progress", "active": True}
    return {"status": "complete", "active": False}


def continue_after_main_ready(
    *,
    thread_id: Optional[str],
    project_key: str,
    version: str,
) -> Dict[str, Any]:
    """Risk check + feature report, then confirm or auto-deploy."""
    key = normalize_project_key(project_key)
    try:
        ver = normalize_version_name(version)
    except SemverError as exc:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, str(exc))
        return {"status": "complete", "summary": str(exc)}

    report, in_cut, open_tickets = format_version_ticket_report(key, ver)
    _post(thread_id, report)
    git = reconcile_release_with_git(project_key=key, version=ver, in_cut=in_cut)
    if git.get("text"):
        _post(thread_id, git["text"])

    _post(
        thread_id,
        "🔎 **Pre-check:** comparing the code about to ship against what's running in prod…",
        role="system",
        status="in_progress",
    )
    try:
        risk = check_deployment_risk(project_key=key)
    except DevOpsError as exc:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Pre-check failed: {exc}")
        return {"status": "complete", "summary": str(exc)}
    except Exception as exc:
        logger.exception("Prepare pre-check failed")
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Pre-check failed: {exc}")
        return {"status": "complete", "summary": str(exc)}

    from bigas.resources.devops.pipeline import _format_risk_for_chat

    _post(thread_id, _format_risk_for_chat(risk))
    risk_level = (risk.get("risk_level") or "low").lower()
    git_mismatch = bool(git.get("needs_confirm"))
    needs_confirm = (
        risk_level in ("high", "medium") or bool(open_tickets) or git_mismatch
    )
    _complete_pipeline_progress(thread_id)

    pending = {
        "kind": "prepare",
        "project_key": key,
        "version": ver,
        "risk_level": risk_level,
        "repo": risk.get("repo"),
        "open_ticket_keys": [t.get("key") for t in open_tickets if t.get("key")],
        "missing_from_git": [
            t.get("key") for t in (git.get("missing_from_git") or []) if t.get("key")
        ],
        "extra_commit_count": len(git.get("extra_commits") or []),
        "site_urls": risk.get("site_urls") or [],
    }
    if needs_confirm:
        from bigas.resources.devops.pipeline import _set_pending

        _set_pending(thread_id, pending)
        reasons = []
        if risk_level in ("high", "medium"):
            reasons.append(f"risk level is **{risk_level}**")
        if open_tickets:
            reasons.append(f"**{len(open_tickets)} open ticket(s)** would be left out")
        missing = git.get("missing_from_git") or []
        extra = git.get("extra_commits") or []
        if missing:
            reasons.append(
                f"**{len(missing)} cut ticket(s)** were not found in git"
            )
        if extra:
            reasons.append(
                f"**{len(extra)} commit(s)** would ship without a ticket on {ver}"
            )
        _post(
            thread_id,
            "Prepare is ready, but "
            + " and ".join(reasons)
            + ". Reply **yes** to deploy `main`, or **no** to cancel.",
        )
        return {"status": "complete", "summary": risk.get("summary") or ""}

    from bigas.resources.devops.pipeline import start_confirmed_deploy

    _post(
        thread_id,
        "Risk is **low**, the cut matches git, and there are no leftover open tickets. "
        "Starting deploy of `main`…",
    )
    return start_confirmed_deploy(
        thread_id=thread_id,
        project_key=key,
        risk=risk,
        release_version=ver,
    )


def run_prepare_deploy(
    *,
    thread_id: Optional[str],
    user_message: str,
    project_key: Optional[str] = None,
    version: Optional[str] = None,
) -> Dict[str, Any]:
    parsed = parse_prepare_command(user_message)
    key = normalize_project_key(project_key or parsed.get("project_key"))
    ver = (version or parsed.get("version") or "").strip()
    if ver:
        try:
            ver = normalize_version_name(ver)
        except SemverError as exc:
            _complete_pipeline_progress(thread_id)
            _post(thread_id, str(exc))
            return {"status": "complete", "summary": str(exc)}

    if not key:
        keys = ", ".join(jira_project_keys() or ["VFA", "BIG"])
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            f"Which project should I prepare? Use **prepare deploy PROJECT VERSION** "
            f"(e.g. `prepare deploy VFA 0.1.0`). Known projects: {keys}.",
        )
        return {"status": "complete", "summary": "Project not specified."}
    if not ver:
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            f"Which release of **{key}**? Use **prepare deploy {key} 0.1.0** "
            "(semver X.Y.Z).",
        )
        return {"status": "complete", "summary": "Version not specified."}

    from bigas.tickets.release_store import get_release_store

    if not get_release_store().get_release_by_name(key, ver):
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            f"No board release **{ver}** for {key}. Create it on `/board` → Releases first.",
        )
        return {"status": "complete", "summary": f"Release {ver} not found."}

    _post(
        thread_id,
        f"📦 **Prepare {brand_name(key) or key} {ver}:** "
        "getting this cut onto `main`, then I'll risk-check and report.",
        role="system",
        status="in_progress",
    )
    try:
        merge = ensure_release_on_main(
            project_key=key, version=ver, thread_id=thread_id
        )
    except DevOpsError as exc:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Could not get {ver} onto main: {exc}")
        return {"status": "complete", "summary": str(exc)}
    except Exception as exc:
        logger.exception("ensure_release_on_main failed")
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Could not get {ver} onto main: {exc}")
        return {"status": "complete", "summary": str(exc)}

    if merge.get("status") == "polling":
        return {
            "status": "in_progress",
            "summary": "Waiting for release PR review/merge.",
            "deploy_poll_active": True,
        }
    if merge.get("status") == "failed":
        return {"status": "complete", "summary": merge.get("summary") or "Prepare failed."}

    return continue_after_main_ready(thread_id=thread_id, project_key=key, version=ver)


def finalize_versioned_deploy(thread_id: str, poll: Dict[str, Any]) -> None:
    """Close the board release, post notes, and ask about social drafts."""
    from bigas.tickets.releases import ReleaseError, close_release

    key = (poll.get("project_key") or "").strip().upper()
    version = (poll.get("release_version") or "").strip()
    if not key or not version:
        return
    try:
        closed = close_release(
            key,
            version,
            target_ref=poll.get("ref") or "main",
            create_github=True,
            create_next_if_missing=False,
        )
    except ReleaseError as exc:
        _post(thread_id, f"Deploy succeeded, but I could not close {key} {version}: {exc}")
        return

    if closed.get("already_released"):
        _post(thread_id, f"**{key} {version}** was already marked released.")
    else:
        moved = closed.get("moved") or []
        nxt = closed.get("next_version")
        gh = (closed.get("github_release") or {}).get("html_url") or ""
        extra = ""
        if moved and nxt:
            extra = f" {len(moved)} open ticket(s) moved to {nxt}."
        elif moved:
            extra = f" {len(moved)} open ticket(s) had their version cleared."
        if gh:
            extra += f" GitHub: {gh}"
        _post(thread_id, f"**{key} {version} released.**{extra}")

    notes: Dict[str, Any] = {}
    try:
        from bigas.resources.product.create_release_notes.service import (
            CreateReleaseNotesService,
        )

        notes = CreateReleaseNotesService().create(
            fix_version=version,
            project_keys=[key],
        )
    except Exception as exc:
        logger.exception("Release notes after prepare-deploy failed")
        _post(thread_id, f"Could not draft release notes ({exc}).")
        return

    markdown = (notes.get("customer_markdown") or "").strip()
    title = notes.get("release_title") or f"Release {version}"
    if markdown:
        _post(thread_id, f"**{title}**\n\n{markdown}")
    social = notes.get("social") or {}
    _thread_set(
        thread_id,
        pending_release_notes={
            "project_key": key,
            "version": version,
            "social": {
                "x": (social.get("x") or "").strip(),
                "linkedin": (social.get("linkedin") or "").strip(),
                "facebook": (social.get("facebook") or "").strip(),
                "instagram": (social.get("instagram") or "").strip(),
            },
            "blog_markdown": (notes.get("blog_markdown") or "").strip(),
        },
    )
    _post(
        thread_id,
        "Want blog / social drafts for this release? Reply **yes** for an X "
        "Approve/Decline draft (other channels as copy only), or **no** to skip.",
    )


def handle_release_notes_reply(
    *,
    thread_id: Optional[str],
    user_message: str,
) -> Dict[str, Any]:
    from bigas.resources.devops.pipeline import is_cancel, is_confirm

    pending = pending_release_notes(thread_id)
    if not pending:
        return {"status": "complete", "summary": "No pending release drafts."}

    if is_cancel(user_message):
        _thread_set(thread_id, pending_release_notes=None)
        _post(thread_id, "Skipped blog / social drafts.")
        return {"status": "complete", "summary": "Skipped social drafts."}

    if not is_confirm(user_message):
        return {"status": "complete", "summary": ""}

    _thread_set(thread_id, pending_release_notes=None)
    social = pending.get("social") or {}
    x_text = (social.get("x") or "").strip()
    other_lines = []
    for label, key in (
        ("LinkedIn", "linkedin"),
        ("Facebook", "facebook"),
        ("Instagram", "instagram"),
    ):
        text = (social.get(key) or "").strip()
        if text:
            other_lines.append(f"**{label}** (copy only)\n{text}")
    blog = (pending.get("blog_markdown") or "").strip()
    if blog:
        other_lines.append(f"**Blog draft** (copy only)\n{blog}")

    if x_text:
        try:
            from bigas.resources.product.x_posts.service import (
                XPostsService,
                format_discord_message,
            )

            result = XPostsService().generate(
                project_keys=[pending.get("project_key")],
                tweets=[x_text],
            )
            _post(thread_id, format_discord_message(result))
        except Exception as exc:
            logger.exception("X draft from release notes failed")
            _post(
                thread_id,
                f"Could not store an X Approve/Decline draft ({exc}). X copy:\n\n{x_text}",
            )
    else:
        _post(thread_id, "No X copy came back from the release notes.")

    if other_lines:
        _post(thread_id, "\n\n".join(other_lines))
    return {"status": "complete", "summary": "Release drafts ready."}


def list_shortcut_projects() -> List[Dict[str, str]]:
    from bigas.portfolio import DEFAULT_BRAND_NAMES
    from bigas.resources.devops.config import resolve_deploy_target

    keys = jira_project_keys() or list(DEFAULT_BRAND_NAMES.keys())

    return [
        {"key": key, "name": DEFAULT_BRAND_NAMES.get(key) or brand_name(key) or key}
        for key in keys
        if resolve_deploy_target(project_key=key)
    ]
