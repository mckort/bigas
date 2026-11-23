"""Tag and complete DevOps pipeline progress messages per project.

Ship cards share one DevOps chat thread. Progress drawers must filter by
``metadata.project_key`` so AuraLearn does not show Bigas deploy steps.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional

from bigas.portfolio import normalize_project_key

_current_project: ContextVar[str] = ContextVar("devops_pipeline_project", default="")


@contextmanager
def pipeline_project(project_key: Optional[str]) -> Iterator[str]:
    """Bind ``project_key`` for ``_post`` / complete calls in this stack."""
    key = normalize_project_key(project_key)
    token = _current_project.set(key)
    try:
        yield key
    finally:
        _current_project.reset(token)


def current_pipeline_project() -> str:
    return _current_project.get() or ""


def pipeline_metadata(
    *,
    status: Optional[str] = None,
    project_key: Optional[str] = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"agent_id": "devops", "pipeline": True}
    key = normalize_project_key(project_key) or current_pipeline_project()
    if key:
        meta["project_key"] = key
    if status:
        meta["status"] = status
    return meta


def complete_pipeline_progress(
    store: Any,
    thread_id: Optional[str],
    *,
    project_key: Optional[str] = None,
) -> None:
    """Mark in-progress pipeline messages complete.

    When ``project_key`` is set, only that project's messages (or legacy
    untagged ones) are cleared — concurrent deploys keep their spinners.
    """
    if not thread_id or not hasattr(store, "patch_message"):
        return
    wanted = normalize_project_key(project_key) or current_pipeline_project()
    for message in store.list_messages(thread_id):
        meta = message.get("metadata") or {}
        if not meta.get("pipeline") or meta.get("status") != "in_progress":
            continue
        tagged = normalize_project_key(meta.get("project_key"))
        if wanted and tagged and tagged != wanted:
            continue
        if wanted and tagged and tagged == wanted:
            store.patch_message(message["message_id"], metadata={"status": "complete"})
            continue
        if wanted and not tagged:
            # Pre-tagging messages: clear only when finishing this project.
            store.patch_message(message["message_id"], metadata={"status": "complete"})
            continue
        if not wanted:
            store.patch_message(message["message_id"], metadata={"status": "complete"})
