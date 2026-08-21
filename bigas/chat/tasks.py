"""A2A-aligned agent task model (internal; chats are projections)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

STATE_SUBMITTED = "submitted"
STATE_WORKING = "working"
STATE_INPUT_REQUIRED = "input-required"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"

OPEN_STATES = (STATE_SUBMITTED, STATE_WORKING, STATE_INPUT_REQUIRED)
TERMINAL_STATES = (STATE_COMPLETED, STATE_FAILED, STATE_CANCELED)
NUDGE_AFTER = timedelta(minutes=5)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_open(task: Optional[Dict[str, Any]]) -> bool:
    return bool(task) and (task.get("state") or "") in OPEN_STATES


def is_terminal(task: Optional[Dict[str, Any]]) -> bool:
    return bool(task) and (task.get("state") or "") in TERMINAL_STATES


def coerce_review_result(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return default


def _store():
    from bigas.chat.db import get_chat_store

    return get_chat_store()


def build_task(
    *,
    user_id: str,
    from_agent_id: str,
    to_agent_id: str,
    instruction: str,
    thread_ids: List[str],
    source_thread_id: Optional[str] = None,
    review_result: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
    state: str = STATE_WORKING,
) -> Dict[str, Any]:
    now = utcnow()
    ids: List[str] = []
    seen = set()
    for tid in thread_ids:
        if tid and tid not in seen:
            seen.add(tid)
            ids.append(tid)
    return {
        "task_id": str(uuid.uuid4()),
        "user_id": user_id or "",
        "from_agent_id": from_agent_id or "chief",
        "to_agent_id": to_agent_id,
        "state": state,
        "review_result": bool(review_result),
        "instruction": instruction or "",
        "thread_ids": ids,
        "source_thread_id": source_thread_id or (ids[0] if ids else None),
        "status_message": "",
        "artifacts": [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "nudge_at": (now + NUDGE_AFTER).isoformat(),
        "nudged_at": None,
        "reviewed_at": None,
        "metadata": dict(metadata or {}),
    }


def create_agent_task(**kwargs: Any) -> Dict[str, Any]:
    task = build_task(**kwargs)
    store = _store()
    if hasattr(store, "create_task"):
        return store.create_task(task)
    return task


def get_task(task_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not task_id:
        return None
    store = _store()
    if hasattr(store, "get_task"):
        return store.get_task(task_id)
    return None


def patch_task(task_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    store = _store()
    if hasattr(store, "patch_task"):
        return store.patch_task(task_id, **fields)
    return None


def list_open_tasks_for_thread(thread_id: Optional[str]) -> List[Dict[str, Any]]:
    if not thread_id:
        return []
    store = _store()
    if hasattr(store, "list_tasks_for_thread"):
        return [t for t in store.list_tasks_for_thread(thread_id) if is_open(t)]
    return []


def get_open_task_for_thread(
    thread_id: Optional[str],
    *,
    kind: Optional[str] = None,
    to_agent_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    tasks = list_open_tasks_for_thread(thread_id)
    if kind:
        tasks = [t for t in tasks if (t.get("metadata") or {}).get("kind") == kind]
    if to_agent_id:
        tasks = [t for t in tasks if t.get("to_agent_id") == to_agent_id]
    if not tasks:
        return None
    tasks.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
    return tasks[0]


def list_open_tasks() -> List[Dict[str, Any]]:
    store = _store()
    if hasattr(store, "list_open_tasks"):
        return store.list_open_tasks()
    return []


def thread_has_open_tasks(thread_id: Optional[str]) -> bool:
    return bool(list_open_tasks_for_thread(thread_id))
