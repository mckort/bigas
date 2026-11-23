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
    r"^\s*(ja|yes|ok|okej|kör|go|gör det|deploy anyway|bekräfta)\b"
    r"(?:\s+(?P<project>[A-Za-z][A-Za-z0-9_-]{1,15}))?",
    re.I,
)
_CANCEL_RE = re.compile(
    r"^\s*(nej|no|avbryt|cancel|stoppa)\b"
    r"(?:\s+(?P<project>[A-Za-z][A-Za-z0-9_-]{1,15}))?",
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


_REPLY_PROJECT_FILLER = frozenset(
    {
        "to",
        "please",
        "anyway",
        "for",
        "the",
        "it",
        "now",
        "do",
        "deploy",
        "that",
        "this",
    }
)


def _reply_project_token(raw: Optional[str]) -> Optional[str]:
    from bigas.portfolio import normalize_project_key

    token = (raw or "").strip()
    if not token or token.lower() in _REPLY_PROJECT_FILLER:
        return None
    return normalize_project_key(token) or None


def confirm_project_key(text: str) -> Optional[str]:
    match = _CONFIRM_RE.search(text or "")
    if not match:
        return None
    return _reply_project_token(match.group("project"))


def cancel_project_key(text: str) -> Optional[str]:
    match = _CANCEL_RE.search(text or "")
    if not match:
        return None
    return _reply_project_token(match.group("project"))


def _store():
    from bigas.chat.db import get_chat_store

    return get_chat_store()


def _post(
    thread_id: Optional[str],
    content: str,
    *,
    role: str = "assistant",
    status: Optional[str] = None,
    project_key: Optional[str] = None,
) -> None:
    if not thread_id or not (content or "").strip():
        return
    from bigas.resources.devops.progress_messages import pipeline_metadata

    _store().add_message(
        thread_id,
        role=role,
        content=content.strip(),
        metadata=pipeline_metadata(status=status, project_key=project_key),
    )


def _complete_pipeline_progress(
    thread_id: Optional[str], *, project_key: Optional[str] = None
) -> None:
    """Mark pipeline progress messages complete so the UI can stop spinners."""
    if not thread_id:
        return
    from bigas.resources.devops.progress_messages import complete_pipeline_progress

    complete_pipeline_progress(_store(), thread_id, project_key=project_key)


def _project_key_of(payload: Optional[Dict[str, Any]], fallback: str = "") -> str:
    from bigas.portfolio import normalize_project_key

    if isinstance(payload, dict):
        key = normalize_project_key(payload.get("project_key") or fallback)
        if key:
            return key
    return normalize_project_key(fallback)


def _pending_map_from_thread(thread: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Normalize legacy ``pending_deploy`` plus ``pending_deploys`` into one map."""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(thread, dict):
        return out
    raw_map = thread.get("pending_deploys")
    if isinstance(raw_map, dict):
        for key, value in raw_map.items():
            if not isinstance(value, dict):
                continue
            project = _project_key_of(value, str(key))
            if project:
                out[project] = {**value, "project_key": project}
    legacy = thread.get("pending_deploy")
    if isinstance(legacy, dict):
        project = _project_key_of(legacy)
        if project and project not in out:
            out[project] = {**legacy, "project_key": project}
    return out


def _all_pendings(thread_id: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not thread_id:
        return {}
    return _pending_map_from_thread(_store().get_thread(thread_id) or {})


def _pending(
    thread_id: Optional[str], project_key: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    pendings = _all_pendings(thread_id)
    if not pendings:
        return None
    if project_key:
        return pendings.get(_project_key_of({"project_key": project_key}))
    if len(pendings) == 1:
        return next(iter(pendings.values()))
    return None


def _write_pending_map(
    thread_id: Optional[str], pendings: Dict[str, Dict[str, Any]]
) -> None:
    if not thread_id or not hasattr(_store(), "patch_thread"):
        return
    cleaned = {
        key: {**value, "project_key": key}
        for key, value in pendings.items()
        if isinstance(value, dict) and key
    }
    legacy = next(iter(cleaned.values())) if len(cleaned) == 1 else None
    _store().patch_thread(
        thread_id,
        pending_deploys=cleaned or None,
        pending_deploy=legacy,
    )


def _set_pending(
    thread_id: Optional[str],
    payload: Optional[Dict[str, Any]],
    *,
    project_key: Optional[str] = None,
) -> None:
    """Set or clear a yes/no confirm. Scoped to one project when known."""
    if not thread_id:
        return
    pendings = _all_pendings(thread_id)
    if payload is None:
        key = _project_key_of({"project_key": project_key}) if project_key else ""
        if key:
            pendings.pop(key, None)
        else:
            pendings = {}
        _write_pending_map(thread_id, pendings)
        return
    key = _project_key_of(payload, project_key or "")
    if not key:
        return
    pendings[key] = {**payload, "project_key": key}
    _write_pending_map(thread_id, pendings)


def _clear_pending_deploy_state(
    thread_id: Optional[str], *, project_key: Optional[str] = None
) -> None:
    from bigas.resources.devops.prepare import clear_prepare_state

    if project_key:
        _set_pending(thread_id, None, project_key=project_key)
        clear_prepare_state(thread_id, project_key=project_key)
        return
    _set_pending(thread_id, None)
    clear_prepare_state(thread_id)


def _command_project_key(user_message: str) -> Optional[str]:
    from bigas.portfolio import resolve_project
    from bigas.resources.devops.prepare import is_prepare_start, parse_prepare_command

    if is_prepare_start(user_message):
        key = (parse_prepare_command(user_message).get("project_key") or "").strip()
        if key:
            return key
    return resolve_project(user_message)


def clear_stale_pending_deploy(user_message: str, thread_id: Optional[str] = None) -> None:
    """Drop confirm/prepare state only for the project named in a new command.

    Unrelated chat leaves outstanding confirms alone so a parallel prepare for
    another project can still answer yes/no.
    """
    from bigas.resources.devops.gpw_pipeline import is_gpw_command, staging_envs
    from bigas.resources.devops.prepare import is_prepare_start

    if is_gpw_command(user_message):
        keys = sorted(staging_envs().keys())
        target = _command_project_key(user_message) or (keys[0] if len(keys) == 1 else None)
        if target:
            _set_pending(thread_id, None, project_key=target)
        return
    if is_prepare_start(user_message) or is_deploy_start(user_message):
        target = _command_project_key(user_message)
        if target:
            _clear_pending_deploy_state(thread_id, project_key=target)
        return
    # Confirms, cancels, and release-notes replies keep their pending state.
    # Do not wipe other projects' yes/no prompts on ordinary chat.


def should_run_deploy_pipeline(user_message: str, thread_id: Optional[str] = None) -> bool:
    from bigas.resources.devops.gpw_pipeline import is_gpw_command
    from bigas.resources.devops.prepare import is_prepare_start, pending_release_notes

    if is_gpw_command(user_message) or is_prepare_start(user_message) or is_deploy_start(user_message):
        return True
    if _all_pendings(thread_id) and (is_confirm(user_message) or is_cancel(user_message)):
        return True
    if pending_release_notes(thread_id) and (
        is_confirm(user_message) or is_cancel(user_message)
    ):
        return True
    return False


def _resolve_pending_for_reply(
    thread_id: Optional[str], user_message: str, *, confirm: bool
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Pick which project confirm a yes/no applies to.

    Returns ``(pending, error)``. When several confirms wait and the reply has
    no project key, ``error`` asks the user to name one.
    """
    pendings = _all_pendings(thread_id)
    if not pendings:
        return None, None
    named = confirm_project_key(user_message) if confirm else cancel_project_key(user_message)
    if named:
        pending = pendings.get(named)
        if not pending:
            ordered = sorted(pendings)
            keys = ", ".join(ordered)
            sample = ordered[0]
            return None, (
                f"No deploy waiting for **{named}**. "
                f"Waiting on: {keys}. Reply **yes {sample}** "
                "(or **no …**) for that project."
            )
        return pending, None
    if len(pendings) == 1:
        return next(iter(pendings.values())), None
    keys = ", ".join(sorted(pendings))
    verb = "yes" if confirm else "no"
    sample = sorted(pendings)[0]
    return None, (
        f"Several deploys are waiting ({keys}). "
        f"Reply **{verb} {sample}** (or another project key) so I know which one."
    )


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


_RISK_FINDINGS = (
    ("database_migration", "Database changes", "changes the database"),
    ("dependency_change", "Dependency updates", "updates dependencies"),
    ("infrastructure_config", "Deploy configuration", "changes deploy configuration"),
    (
        "other_risky",
        "Secrets or production settings",
        "touches files that look like secrets or production settings",
    ),
)


def _finding_paths(findings: Dict[str, Any], key: str) -> List[str]:
    paths: List[str] = []
    for item in findings.get(key) or []:
        path = str(item).strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def _quoted_paths(paths: List[str], limit: int) -> str:
    shown = paths[:limit]
    text = ", ".join(f"`{path}`" for path in shown)
    extra = len(paths) - len(shown)
    if extra:
        text += f", and {extra} more"
    return text


def _join_clauses(parts: List[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def plain_risk_clause(risk: Dict[str, Any]) -> str:
    """' because this deploy …' for a high or medium confirmation, or empty."""
    findings = risk.get("findings") or {}
    parts: List[str] = []
    for key, _label, verb in _RISK_FINDINGS:
        paths = _finding_paths(findings, key)
        if paths:
            parts.append(f"{verb} ({_quoted_paths(paths, 5)})")
    if not parts:
        return ""
    return " because this deploy " + _join_clauses(parts)


def _format_risk_for_chat(risk: Dict[str, Any]) -> str:
    lines = ["**Pre-check complete.**", risk.get("summary") or ""]
    findings = risk.get("findings") or {}
    risky = []
    for key, label, _verb in _RISK_FINDINGS:
        paths = _finding_paths(findings, key)
        if paths:
            risky.append(f"- {label}: {_quoted_paths(paths, 8)}")
    if risky:
        lines.append("Changed risk files:")
        lines.extend(risky)
    return "\n".join(line for line in lines if line).strip()


def _deploy_poll_map_from_thread(
    thread: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(thread, dict):
        return out
    raw_map = thread.get("pending_deploy_polls")
    if isinstance(raw_map, dict):
        for key, value in raw_map.items():
            if not isinstance(value, dict):
                continue
            project = _project_key_of(value, str(key))
            if project:
                out[project] = {**value, "project_key": project}
    legacy = thread.get("pending_deploy_poll")
    if isinstance(legacy, dict):
        project = _project_key_of(legacy)
        if project and project not in out:
            out[project] = {**legacy, "project_key": project}
        elif not project and not out:
            # GPW/legacy polls without a key still need a slot.
            out["_"] = dict(legacy)
    return out


def _all_deploy_polls(thread_id: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not thread_id:
        return {}
    return _deploy_poll_map_from_thread(_store().get_thread(thread_id) or {})


def _deploy_poll(
    thread_id: Optional[str], project_key: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    polls = _all_deploy_polls(thread_id)
    if not polls:
        return None
    if project_key:
        return polls.get(_project_key_of({"project_key": project_key}))
    if len(polls) == 1:
        return next(iter(polls.values()))
    # Prefer a GPW poll when the client polls without a project.
    for value in polls.values():
        if value.get("kind") == "gpw":
            return value
    return next(iter(polls.values()))


def _write_deploy_poll_map(
    thread_id: Optional[str], polls: Dict[str, Dict[str, Any]]
) -> None:
    if not thread_id or not hasattr(_store(), "patch_thread"):
        return
    cleaned = {
        key: ({**value, "project_key": key} if key != "_" else dict(value))
        for key, value in polls.items()
        if isinstance(value, dict)
    }
    legacy = next(iter(cleaned.values())) if len(cleaned) == 1 else (
        next(iter(cleaned.values()), None) if cleaned else None
    )
    # Keep a representative legacy field so older UI still sees an active poll.
    if len(cleaned) > 1:
        legacy = next(iter(cleaned.values()))
    _store().patch_thread(
        thread_id,
        pending_deploy_polls=cleaned or None,
        pending_deploy_poll=legacy,
        has_pending_deploy_poll=bool(cleaned),
    )


def _set_deploy_poll(
    thread_id: Optional[str],
    payload: Optional[Dict[str, Any]],
    *,
    project_key: Optional[str] = None,
) -> None:
    if not thread_id:
        return
    polls = _all_deploy_polls(thread_id)
    if payload is None:
        key = _project_key_of({"project_key": project_key}) if project_key else ""
        if key:
            polls.pop(key, None)
            # "_" is the unkeyed/GPW slot. Remove it only when this clear
            # targets that slot. A named project must leave it alone.
            if key == "_":
                polls.pop("_", None)
        else:
            polls = {}
        _write_deploy_poll_map(thread_id, polls)
        return
    key = _project_key_of(payload, project_key or "") or "_"
    polls[key] = {**payload, **({"project_key": key} if key != "_" else {})}
    _write_deploy_poll_map(thread_id, polls)


def _deploy_poll_slot(poll: Optional[Dict[str, Any]]) -> str:
    """Map key for one poll. Unkeyed and legacy GPW polls use ``_``."""
    key = _project_key_of(poll) if isinstance(poll, dict) else ""
    return key or "_"


def thread_has_active_deploy_poll(thread: Optional[Dict[str, Any]]) -> bool:
    """True when any prepare or deploy poll is waiting (map or legacy)."""
    if not isinstance(thread, dict):
        return False
    if _deploy_poll_map_from_thread(thread):
        return True
    from bigas.resources.devops.prepare import prepare_poll_map_from_thread

    return bool(prepare_poll_map_from_thread(thread))


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
    _set_deploy_poll(thread_id, None, project_key=_deploy_poll_slot(poll))
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
                    shipped = (closed.get("release") or {}).get("name") or ""
                    project = (
                        poll.get("project_key")
                        or poll.get("release_project_key")
                        or ""
                    )
                    if shipped and project:
                        try:
                            from bigas.resources.product.release_branches import (
                                rebase_newer_release_branches,
                            )

                            rebase_newer_release_branches(
                                project_key=project,
                                shipped_version=shipped,
                                thread_id=thread_id,
                            )
                        except Exception:
                            logger.exception(
                                "Rebase of newer release branches after %s failed",
                                shipped,
                            )
            except Exception:
                logger.exception("Board release close after deploy failed")
        _complete_pipeline_progress(
            thread_id,
            project_key=poll.get("project_key") or poll.get("release_project_key"),
        )


def _poll_activity(thread_id: str) -> Dict[str, Any]:
    """``active`` stays true while any prepare or deploy poll remains on the thread."""
    thread = _store().get_thread(thread_id) or {}
    active = thread_has_active_deploy_poll(thread)
    return {"status": "in_progress" if active else "complete", "active": active}


def _advance_deploy_poll(thread_id: str, poll: Dict[str, Any]) -> None:
    """Advance one product deploy poll. Other polls on the thread stay put."""
    from bigas.resources.devops.progress_messages import pipeline_project

    poll_project = poll.get("project_key")
    with pipeline_project(poll_project):
        _advance_deploy_poll_body(thread_id, poll)


def _advance_deploy_poll_body(thread_id: str, poll: Dict[str, Any]) -> None:
    repo = poll.get("repo") or ""
    triggered = poll.get("triggered") or []
    poll_project = poll.get("project_key")
    if not triggered:
        _post(
            thread_id,
            "I didn't get a GitHub Actions run ID back. Check the Actions tab manually.",
            project_key=poll_project,
        )
        _complete_pipeline_progress(thread_id, project_key=poll_project)
        _set_deploy_poll(thread_id, None, project_key=_deploy_poll_slot(poll))
        return

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
            project_key=poll_project,
        )
        return

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


def poll_deploy_postcheck(
    thread_id: str, project_key: Optional[str] = None
) -> Dict[str, Any]:
    """Single client-driven poll step for post-deploy workflow status and health.

    ``project_key`` advances that project only (webhook resume). The client
    omits it and every prepare and deploy poll on the thread advances.
    A prepare poll for one project does not skip another project's deploy.
    """
    from bigas.resources.devops.gpw_pipeline import poll_gpw
    from bigas.resources.devops.prepare import (
        pending_prepare_poll,
        poll_prepare_followup,
        prepare_poll_map_from_thread,
    )

    if project_key:
        prepare_keys = (
            [project_key] if pending_prepare_poll(thread_id, project_key) else []
        )
        slot = _project_key_of({"project_key": project_key}) or project_key
        targeted = _deploy_poll(thread_id, project_key)
        deploy_items = [(slot, targeted)] if isinstance(targeted, dict) else []
    else:
        thread = _store().get_thread(thread_id) or {}
        prepare_keys = list(prepare_poll_map_from_thread(thread).keys())
        deploy_items = list(_all_deploy_polls(thread_id).items())

    for key in prepare_keys:
        poll_prepare_followup(thread_id, project_key=key)

    for key, _poll in deploy_items:
        current = _deploy_poll(thread_id, key)
        if not isinstance(current, dict):
            continue
        if current.get("kind") == "gpw":
            poll_gpw(thread_id, project_key=current.get("project_key") or key or "_")
        else:
            _advance_deploy_poll(thread_id, current)

    return _poll_activity(thread_id)


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
    """Finalize pending deploy polls that have passed the client timeout."""
    if not thread_id:
        return False
    expired_any = False
    for poll in list(_all_deploy_polls(thread_id).values()):
        # Prepare-staging waits on this same field while a review agent runs.
        # That wait has its own timeout. Expiring it here posts a deploy Post-check
        # and drops the follow-up review.
        if (poll.get("kind") or "") == "gpw":
            continue
        started = poll.get("started_at")
        if not started:
            continue
        try:
            deadline = _parse_started_at(str(started)) + timedelta(seconds=_POLL_TIMEOUT_SEC)
        except ValueError:
            continue
        if datetime.now(timezone.utc) < deadline:
            continue
        triggered = poll.get("triggered") or []
        done_ids = {int(rid) for rid in (poll.get("done_run_ids") or [])}
        remaining = _unfinished_runs(triggered, done_ids)
        # Clear before network I/O so concurrent polls cannot re-enter or brick the thread.
        _set_deploy_poll(thread_id, None, project_key=_deploy_poll_slot(poll))
        try:
            _finalize_deploy_postcheck(thread_id, poll, remaining=remaining)
        except Exception:
            logger.exception("Stale deploy poll finalization failed for thread %s", thread_id)
            _post(
                thread_id,
                "Post-check timed out but finalization failed — check deploy status manually.",
            )
            _complete_pipeline_progress(thread_id)
        expired_any = True
    return expired_any


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
        if not thread_id:
            continue
        polls = _deploy_poll_map_from_thread(thread if isinstance(thread, dict) else None)
        matched = [
            poll
            for poll in polls.values()
            if _poll_includes_run(poll, run_id, repo)
        ]
        if not matched:
            continue
        try:
            seen = set()
            for poll in matched:
                slot = _deploy_poll_slot(poll)
                if slot in seen:
                    continue
                seen.add(slot)
                poll_deploy_postcheck(thread_id, project_key=slot)
            resumed.append(thread_id)
        except Exception:
            logger.exception(
                "Resume deploy post-check failed for thread %s (run %s)",
                thread_id,
                run_id,
            )
            for poll in matched:
                slot = _deploy_poll_slot(poll)
                _set_deploy_poll(thread_id, None, project_key=slot)
                _complete_pipeline_progress(thread_id, project_key=slot)
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
    from bigas.resources.devops.progress_messages import pipeline_project

    with pipeline_project(project_key):
        return _start_confirmed_deploy_body(
            thread_id=thread_id,
            project_key=project_key,
            risk=risk,
            release_version=release_version,
            ref=ref,
            clear_pending_release_notes=clear_pending_release_notes,
        )


def _start_confirmed_deploy_body(
    *,
    thread_id: Optional[str],
    project_key: str,
    risk: Optional[Dict[str, Any]],
    release_version: Optional[str],
    ref: Optional[str],
    clear_pending_release_notes,
) -> Dict[str, Any]:
    _set_pending(thread_id, None, project_key=project_key)
    clear_pending_release_notes(thread_id)
    planned = (risk or {}).get("workflows_to_run")
    planned_skips = (risk or {}).get("skipped_workflows") or []
    if isinstance(planned, list) and not planned and planned_skips:
        _complete_pipeline_progress(thread_id, project_key=project_key)
        summary = (risk or {}).get("summary") or "Nothing to deploy — already up to date."
        if "already up to date" not in summary.lower():
            summary = f"{summary}\n\nNothing to deploy — already up to date."
        _post(thread_id, summary, project_key=project_key)
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
        project_key=project_key,
    )
    deploy_ref = (ref or (risk or {}).get("head_ref") or "main").strip() or "main"
    try:
        result = trigger_deployment(project_key=project_key, ref=deploy_ref, risk=risk)
    except DevOpsError as e:
        _complete_pipeline_progress(thread_id, project_key=project_key)
        _post(thread_id, f"Could not start deploy: {e}", project_key=project_key)
        return {"status": "complete", "summary": str(e)}
    except Exception as e:
        logger.exception("trigger_deployment failed")
        _complete_pipeline_progress(thread_id, project_key=project_key)
        _post(thread_id, f"Could not start deploy: {e}", project_key=project_key)
        return {"status": "complete", "summary": str(e)}

    _post(thread_id, result.get("summary") or "Deploy triggered.", project_key=project_key)
    triggered = result.get("triggered") or []
    skipped = result.get("skipped_workflows") or []
    if not triggered:
        errors = result.get("errors") or []
        _complete_pipeline_progress(thread_id, project_key=project_key)
        if skipped and not errors:
            _post(
                thread_id,
                "Nothing to deploy — already up to date.",
                project_key=project_key,
            )
            return {
                "status": "complete",
                "summary": result.get("summary") or "Already up to date.",
            }
        _post(
            thread_id,
            "No workflow started."
            + ((" " + "; ".join(errors)) if errors else ""),
            project_key=project_key,
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
        project_key=project_key,
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
    _from_gpw: bool = False,
) -> Dict[str, Any]:
    """Run pre-check → (optional confirm) → trigger → client-polled post-check."""
    from bigas.resources.devops.prepare import (
        clear_pending_release_notes,
        handle_release_notes_reply,
        is_prepare_start,
        pending_release_notes,
        run_prepare_deploy,
    )

    from bigas.resources.devops.gpw_pipeline import (
        is_gpw_command,
        pending_gpw_action,
        run_gpw_pipeline,
    )

    if not _from_gpw and (is_gpw_command(user_message) or pending_gpw_action(thread_id)):
        return run_gpw_pipeline(thread_id=thread_id, user_message=user_message)

    if is_prepare_start(user_message):
        return run_prepare_deploy(thread_id=thread_id, user_message=user_message)

    if is_cancel(user_message) and _all_pendings(thread_id):
        pending, err = _resolve_pending_for_reply(thread_id, user_message, confirm=False)
        if err:
            _complete_pipeline_progress(thread_id)
            _post(thread_id, err)
            return {"status": "complete", "summary": err}
        if pending:
            _set_pending(thread_id, None, project_key=pending.get("project_key"))
            _complete_pipeline_progress(thread_id)
            label = pending.get("project_key") or "the deploy"
            _post(thread_id, f"Cancelled **{label}**. No workflow was started.")
            return {"status": "complete", "summary": "Deploy cancelled."}

    pending = None
    confirmed = False
    if is_confirm(user_message) and _all_pendings(thread_id):
        pending, err = _resolve_pending_for_reply(thread_id, user_message, confirm=True)
        if err:
            _complete_pipeline_progress(thread_id)
            _post(thread_id, err)
            return {"status": "complete", "summary": err}
        confirmed = bool(pending)

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
    if (
        not _all_pendings(thread_id)
        and notes_pending
        and (is_confirm(user_message) or is_cancel(user_message))
    ):
        return handle_release_notes_reply(thread_id=thread_id, user_message=user_message)

    named = resolve_project(user_message)
    if is_deploy_start(user_message):
        target = (project_key or "").strip().upper() or named
        if target:
            _clear_pending_deploy_state(thread_id, project_key=target)
        pending = None
        confirmed = False
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

    from bigas.resources.devops.progress_messages import pipeline_project

    with pipeline_project(key):
        _post(
            thread_id,
            "🔎 **Pre-check:** comparing the code about to ship against what's running in prod…",
            role="system",
            status="in_progress",
            project_key=key,
        )
        try:
            risk = check_deployment_risk(project_key=key)
        except DevOpsError as e:
            _complete_pipeline_progress(thread_id, project_key=key)
            _post(thread_id, f"Pre-check failed: {e}", project_key=key)
            return {"status": "complete", "summary": str(e)}
        except Exception as e:
            logger.exception("Pre-check failed")
            _complete_pipeline_progress(thread_id, project_key=key)
            _post(thread_id, f"Pre-check failed: {e}", project_key=key)
            return {"status": "complete", "summary": str(e)}

        _post(thread_id, _format_risk_for_chat(risk), project_key=key)

        risk_level = (risk.get("risk_level") or "low").lower()
        needs_confirm = risk_level in ("high", "medium")
        if needs_confirm and not confirmed:
            _complete_pipeline_progress(thread_id, project_key=key)
            _set_pending(
                thread_id,
                {"project_key": key, "risk_level": risk_level, "repo": risk.get("repo")},
            )
            other = [k for k in _all_pendings(thread_id) if k != key]
            yes_hint = f"**yes {key}**" if other else "**yes**"
            no_hint = f"**no {key}**" if other else "**no**"
            _post(
                thread_id,
                f"Risk level is **{risk_level}**{plain_risk_clause(risk)}. "
                f"Reply {yes_hint} to deploy anyway, or {no_hint} to cancel.",
                project_key=key,
            )
            return {"status": "complete", "summary": risk.get("summary") or ""}

        return start_confirmed_deploy(
            thread_id=thread_id,
            project_key=key,
            risk=risk,
        )
