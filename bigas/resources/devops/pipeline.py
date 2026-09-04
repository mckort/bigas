"""Chat-side production deploy pipeline with progress messages."""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bigas.portfolio import resolve_project
from bigas.resources.devops.service import (
    DevOpsError,
    check_deployment_risk,
    check_website_health,
    get_deployment_status,
    get_failed_run_excerpt,
    trigger_deployment,
)

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_SEC = 45 * 60

_DEPLOY_START_RE = re.compile(
    r"\b(deploya|deployera|rulla\s+ut|starta\s+deploy|k[öo]r\s+deploy|"
    r"deploy\s+(vfa|vcfield|bigas|big|backend|web|prod|production))\b"
    r"|^(deploy|ship)\b",
    re.I,
)
_STATUS_RE = re.compile(
    r"\b(status|hur\s+g[aå]r|health\s*check|post-?check|lyckades|failade|failed)\b",
    re.I,
)
_CONFIRM_RE = re.compile(
    r"^\s*(ja|yes|ok|okej|kör|go|gör det|deploy anyway|bekräfta)\b",
    re.I,
)
_CANCEL_RE = re.compile(
    r"^\s*(nej|no|avbryt|cancel|stoppa)\b",
    re.I,
)


def is_deploy_start(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return False
    from bigas.resources.devops.prepare import is_prepare_start

    if is_prepare_start(blob):
        return False
    if _STATUS_RE.search(blob) and not re.search(r"\b(deploya|deployera)\b", blob, re.I):
        return False
    return bool(_DEPLOY_START_RE.search(blob))


def is_confirm(text: str) -> bool:
    return bool(_CONFIRM_RE.search(text or ""))


def is_cancel(text: str) -> bool:
    return bool(_CANCEL_RE.search(text or ""))


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
    """Mark pipeline progress messages complete so the UI can stop spinners."""
    if not thread_id:
        return
    store = _store()
    if not hasattr(store, "patch_message"):
        return
    for message in store.list_messages(thread_id):
        meta = message.get("metadata") or {}
        if meta.get("pipeline") and meta.get("status") == "in_progress":
            store.patch_message(message["message_id"], metadata={"status": "complete"})


def _pending(thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not thread_id:
        return None
    thread = _store().get_thread(thread_id) or {}
    pending = thread.get("pending_deploy")
    return pending if isinstance(pending, dict) else None


def _set_pending(thread_id: Optional[str], payload: Optional[Dict[str, Any]]) -> None:
    if not thread_id or not hasattr(_store(), "patch_thread"):
        return
    _store().patch_thread(thread_id, pending_deploy=payload)


def _clear_pending_deploy_state(thread_id: Optional[str]) -> None:
    if _pending(thread_id):
        _set_pending(thread_id, None)
    from bigas.resources.devops.prepare import clear_prepare_state

    clear_prepare_state(thread_id)


def clear_stale_pending_deploy(user_message: str, thread_id: Optional[str] = None) -> None:
    """Drop leftover deploy state on unrelated messages or a new deploy command."""
    from bigas.resources.devops.prepare import is_prepare_start

    if is_prepare_start(user_message) or is_deploy_start(user_message):
        _clear_pending_deploy_state(thread_id)
        return
    if should_run_deploy_pipeline(user_message, thread_id):
        return
    _clear_pending_deploy_state(thread_id)


def should_run_deploy_pipeline(user_message: str, thread_id: Optional[str] = None) -> bool:
    from bigas.resources.devops.prepare import is_prepare_start, pending_release_notes

    if is_prepare_start(user_message) or is_deploy_start(user_message):
        return True
    pending = _pending(thread_id)
    if pending and (is_confirm(user_message) or is_cancel(user_message)):
        return True
    if pending_release_notes(thread_id) and (
        is_confirm(user_message) or is_cancel(user_message)
    ):
        return True
    return False


def _format_triggered_runs(triggered: List[Dict[str, Any]]) -> str:
    lines = []
    for item in triggered or []:
        workflow = item.get("workflow") or "workflow"
        run_id = item.get("run_id")
        url = item.get("html_url") or ""
        label = f"{workflow} #{run_id}" if run_id else workflow
        lines.append(f"- {label}" + (f" — {url}" if url else ""))
    return "\n".join(lines)


def _unfinished_runs(
    triggered: List[Dict[str, Any]], done_ids: set
) -> List[Dict[str, Any]]:
    return [
        item
        for item in triggered or []
        if item.get("run_id") and int(item["run_id"]) not in done_ids
    ]


def _format_remaining_runs(triggered: List[Dict[str, Any]], done_ids: set) -> str:
    leftover = [
        f"{item.get('workflow') or 'workflow'} #{item.get('run_id')}"
        for item in _unfinished_runs(triggered, done_ids)
    ]
    return ", ".join(leftover)


def _format_risk_for_chat(risk: Dict[str, Any]) -> str:
    lines = ["**Pre-check complete.**", risk.get("summary") or ""]
    findings = risk.get("findings") or {}
    risky = []
    for key in ("database_migration", "dependency_change", "infrastructure_config", "other_risky"):
        items = findings.get(key) or []
        if items:
            risky.append(f"- {key}: " + ", ".join(items[:8]))
    if risky:
        lines.append("Changed risk files:")
        lines.extend(risky)
    return "\n".join(line for line in lines if line).strip()


def _deploy_poll(thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not thread_id:
        return None
    thread = _store().get_thread(thread_id) or {}
    poll = thread.get("pending_deploy_poll")
    return poll if isinstance(poll, dict) else None


def _set_deploy_poll(thread_id: Optional[str], payload: Optional[Dict[str, Any]]) -> None:
    if not thread_id or not hasattr(_store(), "patch_thread"):
        return
    _store().patch_thread(
        thread_id,
        pending_deploy_poll=payload,
        has_pending_deploy_poll=bool(payload),
    )


def _parse_started_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _code_failure(conclusion: str) -> bool:
    return (conclusion or "").lower() not in ("success", "cancelled", "skipped", "")


def _handoff_failed_deploys(
    thread_id: Optional[str],
    *,
    repo: str,
    failed_runs: List[Dict[str, Any]],
    starting_ref: str = "main",
    log_repo: Optional[str] = None,
) -> None:
    if not thread_id or not repo or not failed_runs:
        _complete_pipeline_progress(thread_id)
        return

    _post(
        thread_id,
        "🔎 **CTO handoff:** fetching failed job logs and launching a fix agent…",
        role="system",
        status="in_progress",
    )

    failures: List[Dict[str, Any]] = []
    log_from = (log_repo or repo).strip()
    diagnosis = ["**Deploy failed — diagnosis.**"]
    for item in failed_runs:
        run_id = item.get("run_id")
        workflow = item.get("workflow") or "workflow"
        conclusion = item.get("conclusion") or "failure"
        html_url = item.get("html_url") or ""
        excerpt = ""
        if run_id:
            try:
                fetched = get_failed_run_excerpt(repo=log_from, run_id=int(run_id))
                excerpt = (fetched.get("excerpt") or "").strip()
            except Exception as e:
                logger.warning("Failed to fetch logs for run %s: %s", run_id, e)
                excerpt = f"(could not fetch logs: {e})"
        line = f"- ❌ {workflow} #{run_id} {conclusion}"
        if html_url:
            line += f" — {html_url}"
        diagnosis.append(line)
        if excerpt:
            diagnosis.append("```")
            diagnosis.append(excerpt[:2500])
            diagnosis.append("```")
        failures.append(
            {
                "workflow": workflow,
                "run_id": run_id,
                "conclusion": conclusion,
                "html_url": html_url,
                "excerpt": excerpt,
            }
        )

    _post(thread_id, "\n".join(diagnosis))

    try:
        from bigas.resources.cto.deploy_hotfix import (
            DeployHotfixError,
            launch_failed_deploy_fix,
        )

        launched = launch_failed_deploy_fix(
            repo=repo,
            failures=failures,
            starting_ref=starting_ref,
        )
    except DeployHotfixError as e:
        logger.warning("CTO deploy hotfix launch failed: %s", e)
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            f"Could not launch the CTO fix agent ({e}). "
            "The diagnosis above is still usable for a manual fix.",
        )
        return
    except Exception as e:
        logger.exception("CTO deploy hotfix launch failed")
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            f"Could not launch the CTO fix agent ({e}). "
            "The diagnosis above is still usable for a manual fix.",
        )
        return

    agent_url = (launched.get("agent_url") or launched.get("agent_id") or "").strip()
    _complete_pipeline_progress(thread_id)
    _post(
        thread_id,
        "**CTO agent launched** to fix the failed deploy and open a PR"
        + (f": {agent_url}" if agent_url else "."),
    )


def _finalize_deploy_postcheck(
    thread_id: str,
    poll: Dict[str, Any],
    remaining: Optional[List[Dict[str, Any]]] = None,
) -> None:
    repo = poll.get("repo") or ""
    triggered = poll.get("triggered") or []
    site_urls = poll.get("site_urls") or []
    finished = list(poll.get("finished_lines") or [])
    failed_runs = list(poll.get("failed_runs") or [])
    starting_ref = (poll.get("ref") or "main").strip() or "main"
    done_ids = {int(rid) for rid in (poll.get("done_run_ids") or [])}
    if remaining is None:
        remaining = _unfinished_runs(triggered, done_ids)

    if remaining:
        leftover = ", ".join(
            f"{item.get('workflow')} #{item.get('run_id')}" for item in remaining
        )
        finished.append(
            f"⏳ Timeout: still running: {leftover}. Ask for status later."
        )

    health_lines: List[str] = []
    health_ok = True
    for url in site_urls or []:
        try:
            health = check_website_health(url)
            health_lines.append(health.get("summary") or f"{url}: checked")
            if health.get("is_healthy") is False:
                health_ok = False
        except Exception as e:
            health_ok = False
            health_lines.append(f"{url}: health check failed ({e})")

    parts = ["**Post-check.**"]
    if finished:
        parts.extend(finished)
    if health_lines:
        parts.append("Site health:")
        parts.extend(f"- {line}" for line in health_lines)
    if remaining:
        parts.append("Post-check did not finish cleanly — skipping release notes.")
    elif failed_runs:
        parts.append("A workflow failed — skipping release notes and social drafts.")
    elif not health_ok:
        parts.append("Site health check failed — skipping release notes and social drafts.")
    _post(thread_id, "\n".join(parts))
    _set_deploy_poll(thread_id, None)
    if not failed_runs and not remaining and health_ok and poll.get("release_version"):
        try:
            from bigas.resources.devops.prepare import finalize_versioned_deploy

            finalize_versioned_deploy(thread_id, poll)
        except Exception:
            logger.exception("Versioned release close after deploy failed")
    hotfix_repo = poll.get("product_repo") or repo
    if failed_runs and hotfix_repo:
        _handoff_failed_deploys(
            thread_id,
            repo=hotfix_repo,
            failed_runs=failed_runs,
            starting_ref=starting_ref,
            log_repo=repo,
        )
    else:
        if not failed_runs:
            try:
                from bigas.tickets.releases import close_release_from_deploy_ref

                closed = close_release_from_deploy_ref(
                    poll.get("project_key") or poll.get("release_project_key"),
                    starting_ref,
                )
                if closed and not closed.get("already_released"):
                    moved = closed.get("moved") or []
                    nxt = closed.get("next_version")
                    if moved:
                        _post(
                            thread_id,
                            f"**{closed['release'].get('name')} released.** "
                            f"{len(moved)} open ticket(s) moved to {nxt}.",
                        )
                    else:
                        _post(
                            thread_id,
                            f"**{closed['release'].get('name')} released.** "
                            "No open tickets needed to move.",
                        )
            except Exception:
                logger.exception("Board release close after deploy failed")
        _complete_pipeline_progress(thread_id)


def poll_deploy_postcheck(thread_id: str) -> Dict[str, Any]:
    """Single client-driven poll step for post-deploy workflow status and health."""
    from bigas.resources.devops.prepare import pending_prepare_poll, poll_prepare_followup

    if pending_prepare_poll(thread_id):
        result = poll_prepare_followup(thread_id)
        if result.get("status") == "in_progress" or result.get("active"):
            return {"status": "in_progress", "active": True}
        if result.get("deploy_poll_active"):
            return {"status": "in_progress", "active": True}
        if _deploy_poll(thread_id):
            return {"status": "in_progress", "active": True}
        return {"status": result.get("status") or "complete", "active": False}

    poll = _deploy_poll(thread_id)
    if not poll:
        return {"status": "complete", "active": False}

    repo = poll.get("repo") or ""
    triggered = poll.get("triggered") or []
    if not triggered:
        _post(
            thread_id,
            "I didn't get a GitHub Actions run ID back. Check the Actions tab manually.",
        )
        _complete_pipeline_progress(thread_id)
        _set_deploy_poll(thread_id, None)
        return {"status": "complete", "active": False}

    started_at = poll.get("started_at") or datetime.now(timezone.utc).isoformat()
    deadline = _parse_started_at(started_at) + timedelta(seconds=_POLL_TIMEOUT_SEC)
    finished = list(poll.get("finished_lines") or [])
    failed_runs = list(poll.get("failed_runs") or [])
    done_ids = {int(rid) for rid in (poll.get("done_run_ids") or [])}
    newly_finished: List[str] = []

    for item in triggered:
        run_id = item.get("run_id")
        if not run_id or int(run_id) in done_ids:
            continue
        try:
            status = get_deployment_status(repo=repo, run_id=int(run_id))
        except Exception as e:
            logger.warning("Deploy status poll failed for run %s: %s", run_id, e)
            continue
        if (status.get("workflow_status") or "").lower() != "completed":
            continue
        conclusion = (status.get("conclusion") or "unknown").lower()
        url = status.get("html_url") or item.get("html_url") or ""
        icon = "✅" if conclusion == "success" else "❌"
        line = (
            f"{icon} {item.get('workflow') or 'workflow'} run #{run_id} "
            f"{conclusion}" + (f" — {url}" if url else "")
        )
        finished.append(line)
        newly_finished.append(line)
        done_ids.add(int(run_id))
        if _code_failure(conclusion):
            failed_runs.append(
                {
                    "workflow": item.get("workflow") or "workflow",
                    "run_id": int(run_id),
                    "conclusion": conclusion,
                    "html_url": url,
                }
            )

    remaining = _unfinished_runs(triggered, done_ids)
    timed_out = datetime.now(timezone.utc) >= deadline
    if remaining and not timed_out:
        if newly_finished:
            leftover = _format_remaining_runs(triggered, done_ids)
            progress = "\n".join(newly_finished)
            if leftover:
                progress += f"\n⏳ Still running: {leftover}"
            _post(thread_id, progress, role="system", status="in_progress")
        _set_deploy_poll(
            thread_id,
            {
                **poll,
                "started_at": started_at,
                "finished_lines": finished,
                "failed_runs": failed_runs,
                "done_run_ids": sorted(done_ids),
            },
        )
        return {"status": "in_progress", "active": True}

    _finalize_deploy_postcheck(
        thread_id,
        {
            **poll,
            "finished_lines": finished,
            "failed_runs": failed_runs,
            "done_run_ids": sorted(done_ids),
        },
        remaining=remaining,
    )
    return {"status": "complete", "active": False}


def _poll_includes_run(
    poll: Optional[Dict[str, Any]],
    run_id: int,
    repo: str = "",
) -> bool:
    if not poll:
        return False
    wanted = int(run_id)
    repos = {
        (poll.get("repo") or "").strip().lower(),
        (poll.get("product_repo") or "").strip().lower(),
        (poll.get("deploy_repo") or "").strip().lower(),
    }
    repo_l = (repo or "").strip().lower()
    if repo_l and repos - {""} and repo_l not in repos:
        return False
    for item in poll.get("triggered") or []:
        raw = item.get("run_id")
        if raw and int(raw) == wanted:
            return True
    return False


def expire_stale_deploy_poll(thread_id: Optional[str]) -> bool:
    """Finalize a pending deploy poll that has passed the client timeout."""
    if not thread_id:
        return False
    poll = _deploy_poll(thread_id)
    if not poll:
        return False
    started = poll.get("started_at")
    if not started:
        return False
    try:
        deadline = _parse_started_at(str(started)) + timedelta(seconds=_POLL_TIMEOUT_SEC)
    except ValueError:
        return False
    if datetime.now(timezone.utc) < deadline:
        return False
    triggered = poll.get("triggered") or []
    done_ids = {int(rid) for rid in (poll.get("done_run_ids") or [])}
    remaining = _unfinished_runs(triggered, done_ids)
    # Clear before network I/O so concurrent polls cannot re-enter or brick the thread.
    _set_deploy_poll(thread_id, None)
    try:
        _finalize_deploy_postcheck(thread_id, poll, remaining=remaining)
    except Exception:
        logger.exception("Stale deploy poll finalization failed for thread %s", thread_id)
        _post(
            thread_id,
            "Post-check timed out but finalization failed — check deploy status manually.",
        )
        _complete_pipeline_progress(thread_id)
    return True


def schedule_expire_stale_deploy_poll(thread_id: Optional[str]) -> None:
    """Run stale deploy poll expiry in the background (for hot GET polling paths)."""
    if not thread_id:
        return

    def _run() -> None:
        try:
            expire_stale_deploy_poll(thread_id)
        except Exception:
            logger.exception(
                "Background stale deploy poll expiry failed for thread %s",
                thread_id,
            )

    threading.Thread(target=_run, daemon=True).start()


def resume_deploy_postcheck_from_workflow(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Advance chat post-check when GitHub reports a watched workflow run completed."""
    action = (payload.get("action") or "").strip().lower()
    run = payload.get("workflow_run") or {}
    if action != "completed" or not isinstance(run, dict):
        return {"resumed": 0, "thread_ids": []}
    raw_id = run.get("id")
    if not raw_id:
        return {"resumed": 0, "thread_ids": []}
    run_id = int(raw_id)
    repo_obj = payload.get("repository") or {}
    owner = ((repo_obj.get("owner") or {}).get("login") or "").strip()
    name = (repo_obj.get("name") or "").strip()
    repo = f"{owner}/{name}" if owner and name else ""

    store = _store()
    lister = getattr(store, "list_pending_deploy_threads", None)
    threads = lister() if callable(lister) else []
    resumed: List[str] = []
    for thread in threads:
        thread_id = (thread or {}).get("thread_id")
        poll = (thread or {}).get("pending_deploy_poll")
        if not thread_id or not _poll_includes_run(
            poll if isinstance(poll, dict) else None, run_id, repo
        ):
            continue
        try:
            poll_deploy_postcheck(thread_id)
            resumed.append(thread_id)
        except Exception:
            logger.exception(
                "Resume deploy post-check failed for thread %s (run %s)",
                thread_id,
                run_id,
            )
            _set_deploy_poll(thread_id, None)
            _complete_pipeline_progress(thread_id)
    return {"resumed": len(resumed), "thread_ids": resumed}


def start_confirmed_deploy(
    *,
    thread_id: Optional[str],
    project_key: str,
    risk: Optional[Dict[str, Any]] = None,
    release_version: Optional[str] = None,
    ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Trigger GitHub Actions and start client-driven post-check polling."""
    from bigas.resources.devops.prepare import clear_pending_release_notes

    _set_pending(thread_id, None)
    clear_pending_release_notes(thread_id)
    planned = (risk or {}).get("workflows_to_run")
    planned_skips = (risk or {}).get("skipped_workflows") or []
    if isinstance(planned, list) and not planned and planned_skips:
        _complete_pipeline_progress(thread_id)
        summary = (risk or {}).get("summary") or "Nothing to deploy — already up to date."
        if "already up to date" not in summary.lower():
            summary = f"{summary}\n\nNothing to deploy — already up to date."
        _post(thread_id, summary)
        return {"status": "complete", "summary": summary}
    version = (release_version or "").strip()
    heading = (
        f"🚀 **Deploy {project_key} {version}:** starting GitHub Actions…"
        if version
        else f"🚀 **Deploy {project_key}:** starting GitHub Actions…"
    )
    _post(
        thread_id,
        heading,
        role="system",
        status="in_progress",
    )
    deploy_ref = (ref or (risk or {}).get("head_ref") or "main").strip() or "main"
    try:
        result = trigger_deployment(project_key=project_key, ref=deploy_ref, risk=risk)
    except DevOpsError as e:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Could not start deploy: {e}")
        return {"status": "complete", "summary": str(e)}
    except Exception as e:
        logger.exception("trigger_deployment failed")
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Could not start deploy: {e}")
        return {"status": "complete", "summary": str(e)}

    _post(thread_id, result.get("summary") or "Deploy triggered.")
    triggered = result.get("triggered") or []
    skipped = result.get("skipped_workflows") or []
    if not triggered:
        errors = result.get("errors") or []
        _complete_pipeline_progress(thread_id)
        if skipped and not errors:
            _post(thread_id, "Nothing to deploy — already up to date.")
            return {
                "status": "complete",
                "summary": result.get("summary") or "Already up to date.",
            }
        _post(
            thread_id,
            "No workflow started."
            + ((" " + "; ".join(errors)) if errors else ""),
        )
        return {"status": "complete", "summary": result.get("summary") or ""}

    wait_lines = [
        "⏳ **Post-check:** GitHub Actions is running. I'll post progress here as each workflow finishes.",
    ]
    run_list = _format_triggered_runs(triggered)
    if run_list:
        wait_lines.append(run_list)
    _post(
        thread_id,
        "\n".join(wait_lines),
        role="system",
        status="in_progress",
    )
    if thread_id:
        _set_deploy_poll(
            thread_id,
            {
                "repo": result.get("deploy_repo") or result.get("repo") or (risk or {}).get("repo") or "",
                "product_repo": result.get("repo") or (risk or {}).get("repo") or "",
                "triggered": triggered,
                "site_urls": result.get("site_urls") or (risk or {}).get("site_urls") or [],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_lines": [],
                "failed_runs": [],
                "done_run_ids": [],
                "ref": result.get("ref") or deploy_ref,
                "project_key": project_key,
                "release_version": (release_version or "").strip() or None,
            },
        )
        return {
            "status": "in_progress",
            "summary": result.get("summary") or "",
            "deploy_poll_active": True,
        }

    return {"status": "complete", "summary": result.get("summary") or ""}


def run_chat_deploy_pipeline(
    *,
    thread_id: Optional[str],
    user_message: str,
    project_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Run pre-check → (optional confirm) → trigger → client-polled post-check."""
    from bigas.resources.devops.prepare import (
        clear_pending_release_notes,
        handle_release_notes_reply,
        is_prepare_start,
        pending_release_notes,
        run_prepare_deploy,
    )

    if is_prepare_start(user_message):
        return run_prepare_deploy(thread_id=thread_id, user_message=user_message)

    pending = _pending(thread_id)
    if pending and is_cancel(user_message):
        _set_pending(thread_id, None)
        _complete_pipeline_progress(thread_id)
        _post(thread_id, "Cancelled the deploy. No workflow was started.")
        return {"status": "complete", "summary": "Deploy cancelled."}

    confirmed = bool(pending and is_confirm(user_message))
    if confirmed and (pending or {}).get("kind") == "prepare":
        clear_pending_release_notes(thread_id)
        try:
            risk = check_deployment_risk(project_key=pending.get("project_key"))
        except Exception as e:
            _complete_pipeline_progress(thread_id)
            _post(thread_id, f"Pre-check failed: {e}")
            return {"status": "complete", "summary": str(e)}
        return start_confirmed_deploy(
            thread_id=thread_id,
            project_key=pending.get("project_key") or "",
            risk=risk,
            release_version=pending.get("version"),
            ref=risk.get("head_ref") or "main",
        )

    notes_pending = pending_release_notes(thread_id)
    if not pending and notes_pending and (is_confirm(user_message) or is_cancel(user_message)):
        return handle_release_notes_reply(thread_id=thread_id, user_message=user_message)

    named = resolve_project(user_message)
    if is_deploy_start(user_message) and pending:
        _clear_pending_deploy_state(thread_id)
        pending = None
    key = (project_key or "").strip().upper() or None
    if is_deploy_start(user_message):
        key = key or named
    else:
        key = key or (pending or {}).get("project_key") or named
    if not key:
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            "Which site should be deployed? Name a project key (e.g. VFA, GPWW) or product name.",
        )
        return {"status": "complete", "summary": "Project not specified."}

    _post(
        thread_id,
        "🔎 **Pre-check:** comparing the code about to ship against what's running in prod…",
        role="system",
        status="in_progress",
    )
    try:
        risk = check_deployment_risk(project_key=key)
    except DevOpsError as e:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Pre-check failed: {e}")
        return {"status": "complete", "summary": str(e)}
    except Exception as e:
        logger.exception("Pre-check failed")
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Pre-check failed: {e}")
        return {"status": "complete", "summary": str(e)}

    _post(thread_id, _format_risk_for_chat(risk))

    risk_level = (risk.get("risk_level") or "low").lower()
    needs_confirm = risk_level in ("high", "medium")
    if needs_confirm and not confirmed:
        _complete_pipeline_progress(thread_id)
        _set_pending(
            thread_id,
            {"project_key": key, "risk_level": risk_level, "repo": risk.get("repo")},
        )
        _post(
            thread_id,
            f"Risk level is **{risk_level}**. Reply **yes** to deploy anyway, or **no** to cancel.",
        )
        return {"status": "complete", "summary": risk.get("summary") or ""}

    return start_confirmed_deploy(
        thread_id=thread_id,
        project_key=key,
        risk=risk,
    )
