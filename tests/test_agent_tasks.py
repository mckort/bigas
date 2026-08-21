"""Tests for A2A-aligned agent tasks (nudge, review, no LLM on status)."""
from __future__ import annotations

import os
from datetime import timedelta

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas.agents.task_runtime import (
    finish_task,
    maybe_nudge,
    project_message,
    review_finished_task,
    tick_thread,
)
from bigas.chat.db import get_chat_store
from bigas.chat.tasks import (
    NUDGE_AFTER,
    STATE_COMPLETED,
    STATE_WORKING,
    create_agent_task,
    get_task,
    patch_task,
    utcnow,
)


def test_status_projection_does_not_call_llm(monkeypatch):
    store = get_chat_store()
    chief = store.create_thread("task-user", "chief")
    devops = store.create_thread("task-user", "devops")
    called = {"llm": False}

    def boom(*_a, **_k):
        called["llm"] = True
        raise AssertionError("status updates must not call the LLM")

    monkeypatch.setattr("bigas.agents.task_runtime.get_llm_client", boom)

    task = create_agent_task(
        user_id="task-user",
        from_agent_id="chief",
        to_agent_id="devops",
        instruction="deploy VFA",
        thread_ids=[chief["thread_id"], devops["thread_id"]],
        source_thread_id=chief["thread_id"],
        review_result=True,
        metadata={"kind": "deploy"},
    )
    project_message(task, "⏳ **Post-check:** waiting for GitHub Actions.", role="system", status="in_progress")
    assert called["llm"] is False
    for thread in (chief, devops):
        blob = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
        assert "Post-check" in blob


def test_review_runs_only_on_final_when_flagged(monkeypatch):
    store = get_chat_store()
    chief = store.create_thread("review-user", "chief")
    reviews = []

    class FakeLlm:
        def complete(self, messages, temperature=0.3):
            reviews.append(messages[-1]["content"])
            return '{"action":"answer","text":"Deploy looks good."}'

    monkeypatch.setattr(
        "bigas.agents.task_runtime.get_llm_client",
        lambda feature="chat": (FakeLlm(), "gpt-test"),
    )

    flagged = create_agent_task(
        user_id="review-user",
        from_agent_id="chief",
        to_agent_id="product",
        instruction="Draft release notes",
        thread_ids=[chief["thread_id"]],
        source_thread_id=chief["thread_id"],
        review_result=True,
    )
    silent = create_agent_task(
        user_id="review-user",
        from_agent_id="chief",
        to_agent_id="devops",
        instruction="deploy VFA",
        thread_ids=[chief["thread_id"]],
        source_thread_id=chief["thread_id"],
        review_result=False,
    )
    finish_task(silent["task_id"], "Triggered workflows.", project=True)
    assert reviews == []
    finish_task(flagged["task_id"], "Notes drafted for GPWW-1.")
    assert len(reviews) == 1
    assert "Notes drafted" in reviews[0]
    chief_blob = "\n".join(m["content"] for m in store.list_messages(chief["thread_id"]))
    assert "Deploy looks good" in chief_blob


def test_nudge_once_after_five_minutes(monkeypatch):
    store = get_chat_store()
    chief = store.create_thread("nudge-user", "chief")
    task = create_agent_task(
        user_id="nudge-user",
        from_agent_id="chief",
        to_agent_id="cto",
        instruction="Review PR",
        thread_ids=[chief["thread_id"]],
        source_thread_id=chief["thread_id"],
        review_result=False,
    )
    assert maybe_nudge(get_task(task["task_id"])) is False
    past = (utcnow() - NUDGE_AFTER - timedelta(seconds=1)).isoformat()
    patch_task(task["task_id"], nudge_at=past, state=STATE_WORKING)
    assert maybe_nudge(get_task(task["task_id"])) is True
    assert maybe_nudge(get_task(task["task_id"])) is False
    messages = [m["content"] for m in store.list_messages(chief["thread_id"])]
    assert sum("hasn't posted a final result" in (c or "") for c in messages) == 1


def test_tick_thread_idle_without_open_task():
    store = get_chat_store()
    thread = store.create_thread("idle-user", "chief")
    result = tick_thread(thread["thread_id"])
    assert result["active"] is False
    assert result["status"] == "complete"


def test_callback_final_completes_task(monkeypatch):
    from bigas.agents.chief_of_staff import post_agent_callback

    store = get_chat_store()
    chief = store.create_thread("cb-user", "chief")
    task = create_agent_task(
        user_id="cb-user",
        from_agent_id="chief",
        to_agent_id="marketing",
        instruction="Weekly report",
        thread_ids=[chief["thread_id"]],
        source_thread_id=chief["thread_id"],
        review_result=False,
    )
    post_agent_callback(
        chief["thread_id"],
        "Report is ready.",
        agent_id="marketing",
        task_id=task["task_id"],
        final=True,
    )
    updated = get_task(task["task_id"])
    assert updated["state"] == STATE_COMPLETED
    blob = "\n".join(m["content"] for m in store.list_messages(chief["thread_id"]))
    assert "Report is ready" in blob
