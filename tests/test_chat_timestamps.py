"""Chat message timestamps for the LLM, system prompt, and stored content."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas.chat.db import get_chat_store
from bigas.chat.timestamps import (
    current_time_prompt_block,
    format_chat_timestamp,
    parse_chat_datetime,
    prefix_llm_message,
)


def test_format_chat_timestamp_stockholm_dst():
    stamp = format_chat_timestamp("2026-09-04T08:45:00+00:00")
    assert stamp == "[Friday, Sep 4, 2026, 10:45 AM CEST]"


def test_format_chat_timestamp_skips_empty():
    assert format_chat_timestamp(None) == ""
    assert format_chat_timestamp("") == ""
    assert format_chat_timestamp("not-a-date") == ""


def test_parse_chat_datetime_unix_timestamp():
    dt = parse_chat_datetime(1725437100.0)
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2024


def test_prefix_llm_message_leaves_text_when_no_time():
    assert prefix_llm_message("hello", None) == "hello"
    assert prefix_llm_message("hello", "2026-09-04T08:45:00Z").startswith(
        "[Friday, Sep 4, 2026, 10:45 AM"
    )
    assert prefix_llm_message("hello", "2026-09-04T08:45:00Z").endswith("hello")


def test_current_time_prompt_block_uses_clock():
    now = datetime(2026, 9, 4, 8, 50, tzinfo=timezone.utc)
    block = current_time_prompt_block(now=now)
    assert "Current time: Friday, Sep 4, 2026, 10:50 AM CEST." in block
    assert "today" in block


def test_system_prompt_includes_current_time():
    from bigas.agents.chief_of_staff import _agent_system_prompt

    prompt = _agent_system_prompt(
        {"agent_id": "product", "system_prompt_goals": "PM."}
    )
    assert "Current time:" in prompt
    assert "conversation messages are when those messages were sent" in prompt


def test_handle_chat_message_prefixes_current_user_message(monkeypatch):
    from bigas.agents.chief_of_staff import handle_chat_message
    from bigas.llm.completion import LLMCompletion

    store = get_chat_store()
    store.upsert_user("ts-user", "ts@bigas.local")
    thread = store.create_thread("ts-user", "product")
    captured = []

    class FakeLlm:
        def complete_detailed(self, messages, **kwargs):
            captured.extend(messages)
            return LLMCompletion(text="Noted.")

        def complete(self, messages, **kwargs):
            captured.extend(messages)
            return "Noted."

    monkeypatch.setattr(
        "bigas.agents.chief_of_staff.get_llm_client",
        lambda feature="chat": (FakeLlm(), "gemini-test"),
    )
    monkeypatch.setattr(
        "bigas.agents.chief_of_staff._mcp_client",
        lambda: type("C", (), {"list_tools": staticmethod(lambda: [])})(),
    )

    result = handle_chat_message(
        thread_id=thread["thread_id"],
        user_id="ts-user",
        user_message="Deploy VFA",
    )
    stored = next(
        m
        for m in store.list_messages(thread["thread_id"])
        if m.get("role") == "user"
    )
    assert stored["content"] == "Deploy VFA"
    user_llm = next(m for m in captured if m.get("role") == "user")
    stamp = format_chat_timestamp(stored["created_at"])
    assert user_llm["content"].startswith(stamp)
    assert user_llm["content"].endswith("Deploy VFA")
    assert (result.get("message") or {}).get("content") == "Noted."


def test_history_keeps_stored_content_without_timestamp_tag():
    store = get_chat_store()
    store.upsert_user("hist-user", "hist@bigas.local")
    thread = store.create_thread("hist-user", "chief")
    msg = store.add_message(thread["thread_id"], role="user", content="old note")
    assert msg["content"] == "old note"
    assert "[" not in msg["content"]
    dumped = json.dumps(msg)
    assert "old note" in dumped
