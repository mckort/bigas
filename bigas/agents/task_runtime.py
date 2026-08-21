"""Project agent tasks into chat threads, nudge, and optionally review finals."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Dict, List, Optional

from bigas.chat.db import get_chat_store
from bigas.chat.tasks import (
    STATE_CANCELED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INPUT_REQUIRED,
    STATE_WORKING,
    coerce_review_result,
    create_agent_task,
    get_open_task_for_thread,
    get_task,
    is_open,
    is_terminal,
    list_open_tasks,
    list_open_tasks_for_thread,
    parse_iso,
    patch_task,
    utcnow,
)
from bigas.llm.factory import get_llm_client

logger = logging.getLogger(__name__)

_TICK_LOCK = threading.RLock()

_REVIEW_PROMPT = (
    "You are the Chief of Staff. A specialist finished a task you delegated. "
    "Be autonomous: summarize for the user, take the next step yourself, or show "
    "approval buttons only when a human should confirm something irreversible.\n"
    "Respond with ONLY JSON:\n"
    '{"action":"answer","text":"<markdown for the user>"}\n'
    '{"action":"delegate","agent_id":"marketing|product|cto|devops","task":"<next task>",'
    '"review_result":true}\n'
    '{"action":"propose","label":"<button>","kind":"delegate|tool",'
    '"params":{"agent_id":"...","task":"..."} or {"tool_name":"...","arguments":{}}}\n'
)


def _agent_name(store, agent_id: Optional[str]) -> str:
    aid = (agent_id or "").strip() or "agent"
    agent = store.get_agent(aid) or {}
    return str(agent.get("name") or aid.replace("_", " ").title())


def ensure_task(
    *,
    thread_id: Optional[str],
    to_agent_id: str,
    instruction: str,
    from_agent_id: Optional[str] = None,
    extra_thread_ids: Optional[List[str]] = None,
    review_result: bool = True,
    kind: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the open task for this thread, or create one."""
    existing = get_open_task_for_thread(thread_id, to_agent_id=to_agent_id)
    if existing:
        if kind and not (existing.get("metadata") or {}).get("kind"):
            patch_task(existing["task_id"], metadata={"kind": kind, **(metadata or {})})
            return get_task(existing["task_id"])
        return existing
    if not thread_id:
        return None
    store = get_chat_store()
    thread = store.get_thread(thread_id) or {}
    ids: List[str] = []
    seen = set()
    for tid in [thread_id, *(extra_thread_ids or [])]:
        if tid and tid not in seen:
            seen.add(tid)
            ids.append(tid)
    meta = dict(metadata or {})
    if kind:
        meta.setdefault("kind", kind)
    return create_agent_task(
        user_id=thread.get("user_id") or "",
        from_agent_id=from_agent_id or thread.get("agent_id") or "chief",
        to_agent_id=to_agent_id,
        instruction=instruction,
        thread_ids=ids,
        source_thread_id=thread_id,
        review_result=review_result,
        metadata=meta,
    )


def project_message(
    task: Optional[Dict[str, Any]],
    content: str,
    *,
    role: str = "assistant",
    status: Optional[str] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> None:
    text = (content or "").strip()
    if not task or not text:
        return
    store = get_chat_store()
    meta: Dict[str, Any] = {
        "agent_id": task.get("to_agent_id") or "system",
        "task_id": task.get("task_id"),
        **(extra_meta or {}),
    }
    if status:
        meta["status"] = status
    seen = set()
    for tid in task.get("thread_ids") or []:
        if not tid or tid in seen:
            continue
        seen.add(tid)
        store.add_message(tid, role=role, content=text, metadata=dict(meta))


def complete_in_progress_messages(task: Optional[Dict[str, Any]]) -> None:
    if not task:
        return
    store = get_chat_store()
    if not hasattr(store, "patch_message"):
        return
    task_id = task.get("task_id")
    for tid in task.get("thread_ids") or []:
        for message in store.list_messages(tid):
            meta = message.get("metadata") or {}
            if meta.get("status") != "in_progress":
                continue
            if task_id and meta.get("task_id") not in (None, task_id) and not meta.get("pipeline"):
                continue
            if meta.get("task_id") == task_id or meta.get("pipeline") or meta.get("agent_id") == task.get("to_agent_id"):
                store.patch_message(message["message_id"], metadata={"status": "complete"})


def set_task_working(task_id: str, status_message: str = "", **metadata: Any) -> Optional[Dict[str, Any]]:
    fields: Dict[str, Any] = {"state": STATE_WORKING, "status_message": status_message}
    if metadata:
        fields["metadata"] = metadata
    return patch_task(task_id, **fields)


def set_task_input_required(task_id: str, status_message: str, **metadata: Any) -> Optional[Dict[str, Any]]:
    fields: Dict[str, Any] = {"state": STATE_INPUT_REQUIRED, "status_message": status_message}
    if metadata:
        fields["metadata"] = metadata
    return patch_task(task_id, **fields)


def _append_artifact(task: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
    artifacts = list(task.get("artifacts") or [])
    blob = (text or "").strip()
    if not blob:
        return artifacts
    artifacts.append(
        {
            "artifact_id": str(uuid.uuid4()),
            "name": "final_answer",
            "parts": [{"kind": "text", "text": blob}],
        }
    )
    return artifacts


def _claim_review(task: Dict[str, Any]) -> bool:
    if not task.get("review_result"):
        return False
    if task.get("reviewed_at"):
        return False
    if (task.get("from_agent_id") or "") == (task.get("to_agent_id") or ""):
        patch_task(task["task_id"], reviewed_at=utcnow().isoformat())
        return False
    if (task.get("from_agent_id") or "") != "chief":
        patch_task(task["task_id"], reviewed_at=utcnow().isoformat())
        return False
    patched = patch_task(task["task_id"], reviewed_at=utcnow().isoformat())
    return bool(patched)


def finish_task(
    task_id: str,
    artifact: str,
    *,
    state: str = STATE_COMPLETED,
    project: bool = True,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    task = get_task(task_id)
    if not task or is_terminal(task):
        return task
    artifacts = _append_artifact(task, artifact)
    updated = patch_task(
        task_id,
        state=state,
        status_message=artifact[:500] if artifact else task.get("status_message") or "",
        artifacts=artifacts,
        metadata={"poll": None, "pending_deploy": None},
    )
    complete_in_progress_messages(updated or task)
    if project and artifact:
        project_message(updated or task, artifact, extra_meta=extra_meta)
    review_finished_task(updated or get_task(task_id))
    return get_task(task_id)


def cancel_task(task_id: str, message: str = "Cancelled.", *, project: bool = True) -> Optional[Dict[str, Any]]:
    task = get_task(task_id)
    if not task or is_terminal(task):
        return task
    updated = patch_task(
        task_id,
        state=STATE_CANCELED,
        status_message=message,
        reviewed_at=utcnow().isoformat(),
        metadata={"poll": None, "pending_deploy": None},
    )
    complete_in_progress_messages(updated or task)
    if project and message:
        project_message(updated or task, message)
    return get_task(task_id)


def maybe_nudge(task: Optional[Dict[str, Any]]) -> bool:
    if not task or task.get("state") != STATE_WORKING or task.get("nudged_at"):
        return False
    due = parse_iso(task.get("nudge_at"))
    if not due or utcnow() < due:
        return False
    store = get_chat_store()
    name = _agent_name(store, task.get("to_agent_id"))
    text = (
        f"⏳ Reminder: {name} hasn't posted a final result yet "
        "(still in progress). I'll keep waiting."
    )
    patch_task(task["task_id"], nudged_at=utcnow().isoformat(), status_message=text)
    project_message(get_task(task["task_id"]), text, role="system")
    return True


def _artifact_text(task: Dict[str, Any]) -> str:
    chunks: List[str] = []
    for item in task.get("artifacts") or []:
        for part in item.get("parts") or []:
            text = (part.get("text") or "").strip()
            if text:
                chunks.append(text)
    return "\n\n".join(chunks) or (task.get("status_message") or "")


def review_finished_task(task: Optional[Dict[str, Any]]) -> None:
    if not task or not _claim_review(task):
        return
    store = get_chat_store()
    specialist = _agent_name(store, task.get("to_agent_id"))
    artifact = _artifact_text(task)
    user = (
        f"Specialist: {specialist} ({task.get('to_agent_id')})\n"
        f"State: {task.get('state')}\n"
        f"Instruction:\n{task.get('instruction') or ''}\n\n"
        f"Final result:\n{artifact}"
    )
    try:
        llm, _ = get_llm_client(feature="chat")
        raw = llm.complete(
            [
                {"role": "system", "content": _REVIEW_PROMPT},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
        )
    except Exception:
        logger.exception("COS task review LLM failed")
        return
    _apply_review_action(task, raw)


def _parse_review_json(raw: str) -> Optional[Dict[str, Any]]:
    from bigas.agents.chief_of_staff import _parse_json_action

    return _parse_json_action(raw)


def _apply_review_action(task: Dict[str, Any], raw: str) -> None:
    store = get_chat_store()
    source = task.get("source_thread_id")
    action = _parse_review_json(raw) or {}
    kind = (action.get("action") or "answer").strip().lower()
    if kind == "delegate":
        from bigas.agents.chief_of_staff import _resolve_delegate_target, run_specialist_task

        target = _resolve_delegate_target(action.get("agent_id") or action.get("agent"))
        next_task = (action.get("task") or "").strip()
        if target and next_task and source:
            run_specialist_task(
                target,
                next_task,
                thread_id=source,
                async_mode=True,
                review_result=coerce_review_result(action.get("review_result"), default=True),
            )
            return
        kind = "answer"
    if kind == "propose" and source:
        label = str(action.get("label") or "Take next step").strip()
        params = action.get("params") if isinstance(action.get("params"), dict) else {}
        proposal_kind = str(action.get("kind") or "delegate").strip()
        store.add_message(
            source,
            role="assistant",
            content=str(action.get("text") or label),
            metadata={
                "agent_id": "chief",
                "type": "action_proposal",
                "status": "pending",
                "proposal_id": str(uuid.uuid4()),
                "task_id": task.get("task_id"),
                "actions": [
                    {
                        "id": "review_next",
                        "label": label,
                        "kind": proposal_kind,
                        "params": params,
                    }
                ],
            },
        )
        return
    text = str(action.get("text") or raw or "").strip()
    if text and source:
        store.add_message(
            source,
            role="assistant",
            content=text,
            metadata={"agent_id": "chief", "task_id": task.get("task_id"), "reviewed": True},
        )


def tick_task(task_id: str) -> Dict[str, Any]:
    task = get_task(task_id)
    if not task:
        return {"status": "complete", "active": False}
    maybe_nudge(task)
    task = get_task(task_id) or task
    if (task.get("metadata") or {}).get("kind") == "deploy" and is_open(task):
        from bigas.resources.devops.pipeline import poll_deploy_task

        return poll_deploy_task(task_id)
    return {"status": "complete" if is_terminal(task) else "in_progress", "active": is_open(task)}


def tick_thread(thread_id: str) -> Dict[str, Any]:
    with _TICK_LOCK:
        tasks = list_open_tasks_for_thread(thread_id)
        active = False
        last: Dict[str, Any] = {"status": "complete", "active": False}
        if not tasks:
            from bigas.resources.devops.pipeline import poll_deploy_postcheck

            last = poll_deploy_postcheck(thread_id)
            return last
        for task in tasks:
            last = tick_task(task["task_id"])
            if last.get("active"):
                active = True
        return {"status": "in_progress" if active else "complete", "active": active}


def tick_all_open_tasks() -> Dict[str, Any]:
    with _TICK_LOCK:
        tasks = list_open_tasks()
        nudged = 0
        advanced = 0
        for task in tasks:
            before = task.get("nudged_at")
            result = tick_task(task["task_id"])
            updated = get_task(task["task_id"]) or {}
            if not before and updated.get("nudged_at"):
                nudged += 1
            if result.get("status") == "complete":
                advanced += 1
        return {"ok": True, "open": len(tasks), "nudged": nudged, "completed": advanced}
