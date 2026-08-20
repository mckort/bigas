"""Chat-side production deploy pipeline with progress messages."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bigas.portfolio import resolve_project
from bigas.resources.devops.service import (
    DevOpsError,
    check_deployment_risk,
    check_website_health,
    get_deployment_status,
    trigger_deployment,
)

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_SEC = 45 * 60

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


def clear_stale_pending_deploy(user_message: str, thread_id: Optional[str] = None) -> None:
    """Drop deploy confirmation state when the user sends an unrelated message."""
    if should_run_deploy_pipeline(user_message, thread_id):
        return
    if _pending(thread_id):
        _set_pending(thread_id, None)


def should_run_deploy_pipeline(user_message: str, thread_id: Optional[str] = None) -> bool:
    if is_deploy_start(user_message):
        return True
    pending = _pending(thread_id)
    if pending and (is_confirm(user_message) or is_cancel(user_message)):
        return True
    return False


def _format_risk_for_chat(risk: Dict[str, Any]) -> str:
    lines = ["**Pre-check klar.**", risk.get("summary") or ""]
    findings = risk.get("findings") or {}
    risky = []
    for key in ("database_migration", "dependency_change", "infrastructure_config", "other_risky"):
        items = findings.get(key) or []
        if items:
            risky.append(f"- {key}: " + ", ".join(items[:8]))
    if risky:
        lines.append("Ändrade riskfiler:")
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
    _store().patch_thread(thread_id, pending_deploy_poll=payload)


def _parse_started_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _finalize_deploy_postcheck(thread_id: str, poll: Dict[str, Any]) -> None:
    repo = poll.get("repo") or ""
    triggered = poll.get("triggered") or []
    site_urls = poll.get("site_urls") or []
    finished = list(poll.get("finished_lines") or [])
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
            f"⏳ Timeout: fortfarande igång: {leftover}. Fråga om status senare."
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
        parts.append("Sajthälsa:")
        parts.extend(f"- {line}" for line in health_lines)
    _complete_pipeline_progress(thread_id)
    _post(thread_id, "\n".join(parts))
    _set_deploy_poll(thread_id, None)


def poll_deploy_postcheck(thread_id: str) -> Dict[str, Any]:
    """Single client-driven poll step for post-deploy workflow status and health."""
    poll = _deploy_poll(thread_id)
    if not poll:
        return {"status": "complete", "active": False}

    repo = poll.get("repo") or ""
    triggered = poll.get("triggered") or []
    if not triggered:
        _post(
            thread_id,
            "Jag fick ingen GitHub Actions run-id tillbaka. Kolla Actions-fliken manuellt.",
        )
        _complete_pipeline_progress(thread_id)
        _set_deploy_poll(thread_id, None)
        return {"status": "complete", "active": False}

    started_at = poll.get("started_at") or datetime.now(timezone.utc).isoformat()
    deadline = _parse_started_at(started_at) + timedelta(seconds=_POLL_TIMEOUT_SEC)
    finished = list(poll.get("finished_lines") or [])
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

    remaining = [
        item
        for item in triggered
        if item.get("run_id") and int(item["run_id"]) not in done_ids
    ]
    timed_out = datetime.now(timezone.utc) >= deadline
    if remaining and not timed_out:
        _set_deploy_poll(
            thread_id,
            {
                **poll,
                "started_at": started_at,
                "finished_lines": finished,
                "done_run_ids": sorted(done_ids),
            },
        )
        return {"status": "in_progress", "active": True}

    _finalize_deploy_postcheck(
        thread_id,
        {
            **poll,
            "finished_lines": finished,
            "done_run_ids": sorted(done_ids),
        },
    )
    return {"status": "complete", "active": False}


def run_chat_deploy_pipeline(
    *,
    thread_id: Optional[str],
    user_message: str,
    project_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Run pre-check → (optional confirm) → trigger → client-polled post-check."""
    pending = _pending(thread_id)
    if pending and is_cancel(user_message):
        _set_pending(thread_id, None)
        _complete_pipeline_progress(thread_id)
        _post(thread_id, "Avbröt deployen. Ingen workflow har startats.")
        return {"status": "complete", "summary": "Deploy cancelled."}

    confirmed = bool(pending and is_confirm(user_message))
    key = (
        project_key
        or (pending or {}).get("project_key")
        or resolve_project(user_message)
    )
    if not key:
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            "Vilken sajt ska deployas? Nämn projektnyckel (t.ex. VFA, GPWW) eller produktnamn.",
        )
        return {"status": "complete", "summary": "Project not specified."}

    _post(
        thread_id,
        "🔎 **Pre-check:** jämför koden som ska ut mot det som körs i prod…",
        role="system",
        status="in_progress",
    )
    try:
        risk = check_deployment_risk(project_key=key)
    except DevOpsError as e:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Pre-check misslyckades: {e}")
        return {"status": "complete", "summary": str(e)}
    except Exception as e:
        logger.exception("Pre-check failed")
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Pre-check misslyckades: {e}")
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
            f"Risknivån är **{risk_level}**. Svara **ja** för att deploya ändå, eller **nej** för att avbryta.",
        )
        return {"status": "complete", "summary": risk.get("summary") or ""}

    _set_pending(thread_id, None)
    _post(
        thread_id,
        "🚀 **Deploy:** startar GitHub Actions (backend + web)…",
        role="system",
        status="in_progress",
    )
    try:
        result = trigger_deployment(project_key=key)
    except DevOpsError as e:
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Kunde inte starta deploy: {e}")
        return {"status": "complete", "summary": str(e)}
    except Exception as e:
        logger.exception("trigger_deployment failed")
        _complete_pipeline_progress(thread_id)
        _post(thread_id, f"Kunde inte starta deploy: {e}")
        return {"status": "complete", "summary": str(e)}

    _post(thread_id, result.get("summary") or "Deploy triggad.")
    triggered = result.get("triggered") or []
    if not triggered:
        errors = result.get("errors") or []
        _complete_pipeline_progress(thread_id)
        _post(
            thread_id,
            "Ingen workflow startade."
            + ((" " + "; ".join(errors)) if errors else ""),
        )
        return {"status": "complete", "summary": result.get("summary") or ""}

    _post(
        thread_id,
        "⏳ **Post-check:** väntar på GitHub Actions. Jag skriver här när det är klart (kan ta flera minuter).",
        role="system",
        status="in_progress",
    )
    if thread_id:
        _set_deploy_poll(
            thread_id,
            {
                "repo": result.get("repo") or risk.get("repo") or "",
                "triggered": triggered,
                "site_urls": result.get("site_urls") or risk.get("site_urls") or [],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_lines": [],
                "done_run_ids": [],
            },
        )
        return {
            "status": "in_progress",
            "summary": result.get("summary") or "",
            "deploy_poll_active": True,
        }

    return {"status": "complete", "summary": result.get("summary") or ""}
