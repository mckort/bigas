"""Chat-side production deploy pipeline with progress messages."""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Union

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
_POLL_LOCK = threading.RLock()

ThreadIds = Union[str, Iterable[Optional[str]], None]

_DEPLOY_START_RE = re.compile(
    r"\b(deploya|deployera|rulla\s+ut|starta\s+deploy|k[öo]r\s+deploy|"
    r"deploy\s+(vfa|vcfield|backend|web|prod|production))\b"
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


def _as_ids(thread_ids: ThreadIds) -> List[str]:
    if not thread_ids:
        return []
    if isinstance(thread_ids, str):
        return [thread_ids]
    out: List[str] = []
    seen = set()
    for tid in thread_ids:
        if tid and tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def _unique_thread_ids(*groups: ThreadIds) -> List[str]:
    out: List[str] = []
    seen = set()
    for group in groups:
        for tid in _as_ids(group):
            if tid not in seen:
                seen.add(tid)
                out.append(tid)
    return out


def _sibling_ids(thread_id: Optional[str], payload: Optional[Dict[str, Any]] = None) -> List[str]:
    extra = (payload or {}).get("poll_thread_ids") or []
    return _unique_thread_ids(thread_id, extra)


def _post(
    thread_ids: ThreadIds,
    content: str,
    *,
    role: str = "assistant",
    status: Optional[str] = None,
    task: Optional[Dict[str, Any]] = None,
) -> None:
    text = (content or "").strip()
    if not text:
        return
    if task:
        from bigas.agents.task_runtime import project_message

        extra: Dict[str, Any] = {"pipeline": True, "agent_id": "devops"}
        project_message(task, text, role=role, status=status, extra_meta=extra)
        if status_message := text:
            from bigas.chat.tasks import patch_task

            patch_task(task["task_id"], status_message=status_message)
        return
    meta: Dict[str, Any] = {"agent_id": "devops", "pipeline": True}
    if status:
        meta["status"] = status
    store = _store()
    for tid in _as_ids(thread_ids):
        store.add_message(tid, role=role, content=text, metadata=dict(meta))


def _complete_pipeline_progress(thread_ids: ThreadIds, task: Optional[Dict[str, Any]] = None) -> None:
    """Mark pipeline progress messages complete so the UI can stop spinners."""
    if task:
        from bigas.agents.task_runtime import complete_in_progress_messages

        complete_in_progress_messages(task)
        return
    store = _store()
    if not hasattr(store, "patch_message"):
        return
    for tid in _as_ids(thread_ids):
        for message in store.list_messages(tid):
            meta = message.get("metadata") or {}
            if meta.get("pipeline") and meta.get("status") == "in_progress":
                store.patch_message(message["message_id"], metadata={"status": "complete"})


def _pending(thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not thread_id:
        return None
    from bigas.chat.tasks import STATE_INPUT_REQUIRED, get_open_task_for_thread

    task = get_open_task_for_thread(thread_id, kind="deploy") or get_open_task_for_thread(
        thread_id, to_agent_id="devops"
    )
    if task:
        pending = (task.get("metadata") or {}).get("pending_deploy")
        if isinstance(pending, dict):
            return pending
        if task.get("state") == STATE_INPUT_REQUIRED:
            return {"task_id": task.get("task_id")}
    thread = _store().get_thread(thread_id) or {}
    pending = thread.get("pending_deploy")
    return pending if isinstance(pending, dict) else None


def _set_pending(thread_id: Optional[str], payload: Optional[Dict[str, Any]]) -> None:
    if not thread_id or not hasattr(_store(), "patch_thread"):
        return
    _store().patch_thread(thread_id, pending_deploy=payload)


def _set_pending_all(thread_ids: ThreadIds, payload: Optional[Dict[str, Any]]) -> None:
    ids = _as_ids(thread_ids)
    data = None if payload is None else {**payload, "poll_thread_ids": ids}
    for tid in ids:
        _set_pending(tid, data)


def clear_stale_pending_deploy(user_message: str, thread_id: Optional[str] = None) -> None:
    """Drop deploy confirmation state when the user sends an unrelated message."""
    if should_run_deploy_pipeline(user_message, thread_id):
        return
    from bigas.agents.task_runtime import cancel_task
    from bigas.chat.tasks import STATE_INPUT_REQUIRED, get_open_task_for_thread

    task = get_open_task_for_thread(thread_id, kind="deploy")
    if task and task.get("state") == STATE_INPUT_REQUIRED:
        cancel_task(task["task_id"], "Dropped the pending deploy confirmation.", project=False)
        return
    pending = _pending(thread_id)
    if pending:
        _set_pending_all(_sibling_ids(thread_id, pending), None)


def should_run_deploy_pipeline(user_message: str, thread_id: Optional[str] = None) -> bool:
    if is_deploy_start(user_message):
        return True
    pending = _pending(thread_id)
    if pending and (is_confirm(user_message) or is_cancel(user_message)):
        return True
    return False


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
    from bigas.chat.tasks import get_open_task_for_thread

    task = get_open_task_for_thread(thread_id, kind="deploy")
    if task:
        poll = (task.get("metadata") or {}).get("poll")
        if isinstance(poll, dict):
            return poll
    thread = _store().get_thread(thread_id) or {}
    poll = thread.get("pending_deploy_poll")
    return poll if isinstance(poll, dict) else None


def _set_deploy_poll(thread_id: Optional[str], payload: Optional[Dict[str, Any]]) -> None:
    if not thread_id or not hasattr(_store(), "patch_thread"):
        return
    _store().patch_thread(thread_id, pending_deploy_poll=payload)


def _write_deploy_poll(
    thread_ids: ThreadIds,
    payload: Optional[Dict[str, Any]],
    task: Optional[Dict[str, Any]] = None,
) -> None:
    if task:
        from bigas.chat.tasks import patch_task

        patch_task(task["task_id"], metadata={"poll": payload})
        return
    ids = _as_ids(thread_ids)
    data = None if payload is None else {**payload, "poll_thread_ids": ids}
    for tid in ids:
        _set_deploy_poll(tid, data)


def _has_pipeline_postcheck(thread_id: str) -> bool:
    for message in _store().list_messages(thread_id):
        content = message.get("content") or ""
        meta = message.get("metadata") or {}
        if meta.get("pipeline") and content.startswith("**Post-check.**"):
            return True
    return False


def _parse_started_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _code_failure(conclusion: str) -> bool:
    return (conclusion or "").lower() not in ("success", "cancelled", "skipped", "")


def _handoff_failed_deploys(
    thread_ids: ThreadIds,
    *,
    repo: str,
    failed_runs: List[Dict[str, Any]],
    starting_ref: str = "main",
    log_repo: Optional[str] = None,
    task: Optional[Dict[str, Any]] = None,
) -> None:
    ids = _as_ids(thread_ids)
    if not ids or not repo or not failed_runs:
        _complete_pipeline_progress(ids, task)
        return

    _post(
        ids,
        "🔎 **CTO handoff:** fetching failed job logs and launching a fix agent…",
        role="system",
        status="in_progress",
        task=task,
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

    _post(ids, "\n".join(diagnosis), task=task)

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
        _complete_pipeline_progress(ids, task)
        _post(
            ids,
            f"Could not launch the CTO fix agent ({e}). "
            "The diagnosis above is still usable for a manual fix.",
            task=task,
        )
        return
    except Exception as e:
        logger.exception("CTO deploy hotfix launch failed")
        _complete_pipeline_progress(ids, task)
        _post(
            ids,
            f"Could not launch the CTO fix agent ({e}). "
            "The diagnosis above is still usable for a manual fix.",
            task=task,
        )
        return

    agent_url = (launched.get("agent_url") or launched.get("agent_id") or "").strip()
    _complete_pipeline_progress(ids, task)
    _post(
        ids,
        "**CTO agent launched** to fix the failed deploy and open a PR"
        + (f": {agent_url}" if agent_url else "."),
        task=task,
    )


def _resolve_deploy_task(
    thread_id: Optional[str],
    poll: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    from bigas.chat.tasks import get_open_task_for_thread, get_task

    task_id = (poll or {}).get("task_id")
    if task_id:
        return get_task(task_id)
    return get_open_task_for_thread(thread_id, kind="deploy") or get_open_task_for_thread(
        thread_id, to_agent_id="devops"
    )


def _finish_deploy_task(
    task: Optional[Dict[str, Any]],
    artifact: str,
    *,
    failed: bool = False,
) -> None:
    if not task:
        return
    from bigas.agents.task_runtime import finish_task
    from bigas.chat.tasks import STATE_COMPLETED, STATE_FAILED

    finish_task(
        task["task_id"],
        artifact,
        state=STATE_FAILED if failed else STATE_COMPLETED,
        project=False,
    )


def _finalize_deploy_postcheck(thread_id: str, poll: Dict[str, Any]) -> None:
    task = _resolve_deploy_task(thread_id, poll)
    ids = list((task or {}).get("thread_ids") or []) or _sibling_ids(thread_id, poll)
    if any(_has_pipeline_postcheck(tid) for tid in ids):
        _write_deploy_poll(ids, None, task)
        _complete_pipeline_progress(ids, task)
        _finish_deploy_task(task, "Post-check already posted.")
        return

    repo = poll.get("repo") or ""
    triggered = poll.get("triggered") or []
    site_urls = poll.get("site_urls") or []
    finished = list(poll.get("finished_lines") or [])
    failed_runs = list(poll.get("failed_runs") or [])
    starting_ref = (poll.get("ref") or "main").strip() or "main"
    done_ids = {int(rid) for rid in (poll.get("done_run_ids") or [])}
    remaining = [
        item
        for item in triggered
        if item.get("run_id") and int(item["run_id"]) not in done_ids
    ]

    if remaining:
        leftover = ", ".join(
            f"{item.get('workflow')} #{item.get('run_id')}" for item in remaining
        )
        finished.append(
            f"⏳ Timeout: still running: {leftover}. Ask for status later."
        )

    health_lines: List[str] = []
    for url in site_urls or []:
        try:
            health = check_website_health(url)
            health_lines.append(health.get("summary") or f"{url}: checked")
        except Exception as e:
            health_lines.append(f"{url}: health check failed ({e})")

    parts = ["**Post-check.**"]
    if finished:
        parts.extend(finished)
    if health_lines:
        parts.append("Site health:")
        parts.extend(f"- {line}" for line in health_lines)
    artifact = "\n".join(parts)
    _post(ids, artifact, task=task)
    _write_deploy_poll(ids, None, task)
    hotfix_repo = poll.get("product_repo") or repo
    if failed_runs and hotfix_repo:
        _handoff_failed_deploys(
            ids,
            repo=hotfix_repo,
            failed_runs=failed_runs,
            starting_ref=starting_ref,
            log_repo=repo,
            task=task,
        )
        _finish_deploy_task(task, artifact, failed=True)
    else:
        _complete_pipeline_progress(ids, task)
        _finish_deploy_task(task, artifact, failed=False)


def poll_deploy_postcheck(thread_id: str) -> Dict[str, Any]:
    """Single client-driven poll step for post-deploy workflow status and health."""
    with _POLL_LOCK:
        task = _resolve_deploy_task(thread_id)
        if task:
            return _poll_deploy_task_locked(task)
        return _poll_deploy_postcheck_locked(thread_id)


def poll_deploy_task(task_id: str) -> Dict[str, Any]:
    from bigas.chat.tasks import get_task, is_open

    task = get_task(task_id)
    if not task:
        return {"status": "complete", "active": False}
    with _POLL_LOCK:
        if not is_open(task):
            return {"status": "complete", "active": False}
        return _poll_deploy_task_locked(task)


def _poll_deploy_task_locked(task: Dict[str, Any]) -> Dict[str, Any]:
    poll = (task.get("metadata") or {}).get("poll")
    thread_id = task.get("source_thread_id") or ((task.get("thread_ids") or [None])[0])
    if not isinstance(poll, dict):
        return _poll_deploy_postcheck_locked(thread_id or "")
    return _run_poll_step(thread_id or "", poll, task=task)


def _poll_deploy_postcheck_locked(thread_id: str) -> Dict[str, Any]:
    poll = _deploy_poll(thread_id)
    if not poll:
        return {"status": "complete", "active": False}
    return _run_poll_step(thread_id, poll, task=_resolve_deploy_task(thread_id, poll))


def _run_poll_step(
    thread_id: str,
    poll: Dict[str, Any],
    *,
    task: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ids = list((task or {}).get("thread_ids") or []) or _sibling_ids(thread_id, poll)
    repo = poll.get("repo") or ""
    triggered = poll.get("triggered") or []
    if not triggered:
        _post(
            ids,
            "I didn't get a GitHub Actions run ID back. Check the Actions tab manually.",
            task=task,
        )
        _complete_pipeline_progress(ids, task)
        _write_deploy_poll(ids, None, task)
        _finish_deploy_task(task, "No GitHub Actions run ID.", failed=True)
        return {"status": "complete", "active": False}

    started_at = poll.get("started_at") or datetime.now(timezone.utc).isoformat()
    deadline = _parse_started_at(started_at) + timedelta(seconds=_POLL_TIMEOUT_SEC)
    finished = list(poll.get("finished_lines") or [])
    failed_runs = list(poll.get("failed_runs") or [])
    done_ids = {int(rid) for rid in (poll.get("done_run_ids") or [])}

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
        finished.append(
            f"{icon} {item.get('workflow') or 'workflow'} run #{run_id} "
            f"{conclusion}" + (f" — {url}" if url else "")
        )
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

    remaining = [
        item
        for item in triggered
        if item.get("run_id") and int(item["run_id"]) not in done_ids
    ]
    timed_out = datetime.now(timezone.utc) >= deadline
    if remaining and not timed_out:
        next_poll = {
            **poll,
            "started_at": started_at,
            "finished_lines": finished,
            "failed_runs": failed_runs,
            "done_run_ids": sorted(done_ids),
        }
        if task:
            next_poll["task_id"] = task.get("task_id")
        _write_deploy_poll(ids, next_poll, task)
        return {"status": "in_progress", "active": True}

    _finalize_deploy_postcheck(
        thread_id,
        {
            **poll,
            "finished_lines": finished,
            "failed_runs": failed_runs,
            "done_run_ids": sorted(done_ids),
            "task_id": (task or {}).get("task_id") or poll.get("task_id"),
        },
    )
    return {"status": "complete", "active": False}


def run_chat_deploy_pipeline(
    *,
    thread_id: Optional[str],
    user_message: str,
    project_key: Optional[str] = None,
    mirror_thread_ids: Optional[List[str]] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run pre-check → (optional confirm) → trigger → client-polled post-check."""
    from bigas.agents.task_runtime import (
        cancel_task,
        ensure_task,
        finish_task,
        set_task_input_required,
        set_task_working,
    )
    from bigas.chat.tasks import STATE_COMPLETED, STATE_FAILED, get_task

    pending = _pending(thread_id)
    task = get_task(task_id) if task_id else None
    if not task:
        task = ensure_task(
            thread_id=thread_id,
            to_agent_id="devops",
            instruction=user_message,
            extra_thread_ids=mirror_thread_ids,
            review_result=False,
            kind="deploy",
        )
    thread_ids = _unique_thread_ids(
        thread_id,
        mirror_thread_ids,
        (task or {}).get("thread_ids"),
        (pending or {}).get("poll_thread_ids"),
    )
    if pending and is_cancel(user_message):
        _set_pending_all(thread_ids, None)
        _complete_pipeline_progress(thread_ids, task)
        if task:
            cancel_task(task["task_id"], "Cancelled the deploy. No workflow was started.")
        else:
            _post(thread_ids, "Cancelled the deploy. No workflow was started.", task=task)
        return {"status": "complete", "summary": "Deploy cancelled."}

    confirmed = bool(pending and is_confirm(user_message))
    key = (
        project_key
        or (pending or {}).get("project_key")
        or resolve_project(user_message)
    )
    if not key:
        _complete_pipeline_progress(thread_ids, task)
        msg = "Which site should be deployed? Name a project key (e.g. VFA, GPWW) or product name."
        _post(thread_ids, msg, task=task)
        if task:
            finish_task(task["task_id"], msg, state=STATE_COMPLETED, project=False)
        return {"status": "complete", "summary": "Project not specified."}

    _post(
        thread_ids,
        "🔎 **Pre-check:** comparing the code about to ship against what's running in prod…",
        role="system",
        status="in_progress",
        task=task,
    )
    try:
        risk = check_deployment_risk(project_key=key)
    except DevOpsError as e:
        _complete_pipeline_progress(thread_ids, task)
        _post(thread_ids, f"Pre-check failed: {e}", task=task)
        if task:
            finish_task(task["task_id"], f"Pre-check failed: {e}", state=STATE_FAILED, project=False)
        return {"status": "complete", "summary": str(e)}
    except Exception as e:
        logger.exception("Pre-check failed")
        _complete_pipeline_progress(thread_ids, task)
        _post(thread_ids, f"Pre-check failed: {e}", task=task)
        if task:
            finish_task(task["task_id"], f"Pre-check failed: {e}", state=STATE_FAILED, project=False)
        return {"status": "complete", "summary": str(e)}

    _post(thread_ids, _format_risk_for_chat(risk), task=task)

    risk_level = (risk.get("risk_level") or "low").lower()
    needs_confirm = risk_level in ("high", "medium")
    if needs_confirm and not confirmed:
        _complete_pipeline_progress(thread_ids, task)
        pending_payload = {"project_key": key, "risk_level": risk_level, "repo": risk.get("repo")}
        _set_pending_all(thread_ids, pending_payload)
        confirm_msg = (
            f"Risk level is **{risk_level}**. Reply **yes** to deploy anyway, or **no** to cancel."
        )
        if task:
            set_task_input_required(task["task_id"], confirm_msg, pending_deploy=pending_payload)
            task = get_task(task["task_id"])
        _post(thread_ids, confirm_msg, task=task)
        return {"status": "complete", "summary": risk.get("summary") or ""}

    _set_pending_all(thread_ids, None)
    if task:
        set_task_working(task["task_id"], "Starting GitHub Actions…", pending_deploy=None)
        task = get_task(task["task_id"])
    _post(
        thread_ids,
        "🚀 **Deploy:** starting GitHub Actions…",
        role="system",
        status="in_progress",
        task=task,
    )
    try:
        result = trigger_deployment(project_key=key, ref=risk.get("head_ref"))
    except DevOpsError as e:
        _complete_pipeline_progress(thread_ids, task)
        _post(thread_ids, f"Could not start deploy: {e}", task=task)
        if task:
            finish_task(task["task_id"], f"Could not start deploy: {e}", state=STATE_FAILED, project=False)
        return {"status": "complete", "summary": str(e)}
    except Exception as e:
        logger.exception("trigger_deployment failed")
        _complete_pipeline_progress(thread_ids, task)
        _post(thread_ids, f"Could not start deploy: {e}", task=task)
        if task:
            finish_task(task["task_id"], f"Could not start deploy: {e}", state=STATE_FAILED, project=False)
        return {"status": "complete", "summary": str(e)}

    _post(thread_ids, result.get("summary") or "Deploy triggered.", task=task)
    triggered = result.get("triggered") or []
    if not triggered:
        errors = result.get("errors") or []
        _complete_pipeline_progress(thread_ids, task)
        msg = "No workflow started." + ((" " + "; ".join(errors)) if errors else "")
        _post(thread_ids, msg, task=task)
        if task:
            finish_task(task["task_id"], msg, state=STATE_FAILED, project=False)
        return {"status": "complete", "summary": result.get("summary") or ""}

    waiting = (
        "⏳ **Post-check:** waiting for GitHub Actions. I'll post here when it's done "
        "(this can take several minutes)."
    )
    _post(thread_ids, waiting, role="system", status="in_progress", task=task)
    poll = {
        "repo": result.get("deploy_repo") or result.get("repo") or risk.get("repo") or "",
        "product_repo": result.get("repo") or risk.get("repo") or "",
        "triggered": triggered,
        "site_urls": result.get("site_urls") or risk.get("site_urls") or [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_lines": [],
        "failed_runs": [],
        "done_run_ids": [],
        "ref": result.get("ref") or risk.get("head_ref") or "main",
        "task_id": (task or {}).get("task_id"),
    }
    if thread_ids or task:
        _write_deploy_poll(thread_ids, poll, task)
        return {
            "status": "in_progress",
            "summary": result.get("summary") or "",
            "deploy_poll_active": True,
            "task_poll_active": True,
        }

    return {"status": "complete", "summary": result.get("summary") or ""}
