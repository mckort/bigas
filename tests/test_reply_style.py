"""Tests for default human-friendly chat reply style."""

from __future__ import annotations

import json

from bigas.chat.reply_style import looks_like_raw_tool_dump
from bigas.llm.completion import LLMCompletion, ToolCall


GITHUB_ACTIVITY_DUMP = json.dumps(
    {
        "repo": "mckort/vcfieldassistant",
        "since": "2026-08-17T00:00:00+00:00",
        "commits": [
            {
                "sha": "abc123",
                "subject": "feat: open registration",
                "committed_at": "2026-09-01T10:00:00+00:00",
                "html_url": "https://github.com/mckort/vcfieldassistant/commit/abc123",
            }
        ],
        "pull_requests": 86,
    }
)


def test_looks_like_raw_tool_dump_detects_github_activity():
    assert looks_like_raw_tool_dump(GITHUB_ACTIVITY_DUMP)
    assert looks_like_raw_tool_dump(f"```json\n{GITHUB_ACTIVITY_DUMP}\n```")
    truncated = '{"repo":"mckort/vcfieldassistant","commits":[{"sha":"abc"'
    assert looks_like_raw_tool_dump(truncated)


def test_looks_like_raw_tool_dump_ignores_human_replies():
    assert not looks_like_raw_tool_dump(
        "Här är de viktigaste nyheterna i **VC Field Assistant** sedan 17 augusti."
    )
    assert not looks_like_raw_tool_dump('{"answer": "Traffic is up."}')
    assert not looks_like_raw_tool_dump("")


def test_system_prompt_includes_reply_style():
    from bigas.agents.chief_of_staff import REPLY_STYLE, _agent_system_prompt

    prompt = _agent_system_prompt(
        {"agent_id": "product", "system_prompt_goals": "PM."}
    )
    assert "human-friendly summary" in REPLY_STYLE
    assert "The user never sees tool output" in prompt
    assert "emoji + bold category header" in prompt


def test_native_tool_loop_humanizes_json_answer():
    from bigas.agents.chief_of_staff import _run_native_tool_loop

    class FakeLLM:
        def __init__(self):
            self.turns = 0
            self.rewrites = 0

        def complete_detailed(self, messages, **kwargs):
            self.turns += 1
            if self.turns == 1:
                return LLMCompletion(
                    text="",
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="fetch_github_activity",
                            arguments={"project_key": "VFA", "since": "2026-08-17"},
                        ),
                    ),
                )
            return LLMCompletion(text=GITHUB_ACTIVITY_DUMP)

        def complete(self, messages, **kwargs):
            self.rewrites += 1
            return (
                "Här är de viktigaste nyheterna i **VC Field Assistant** sedan 17 augusti:\n\n"
                "🚀 **Onboarding och åtkomst**\n"
                "**Öppen registrering** — nya användare kan skapa konto utan inbjudan."
            )

    llm = FakeLLM()
    result = _run_native_tool_loop(
        llm,
        [{"role": "user", "content": "Vad för nya funktioner har vi lanserat efter 17 augusti?"}],
        [{"type": "function", "function": {"name": "fetch_github_activity", "parameters": {}}}],
        run_tool=lambda name, args: GITHUB_ACTIVITY_DUMP,
    )
    assert "Öppen registrering" in result
    assert not result.strip().startswith("{")
    assert "commits" not in result
    assert llm.rewrites == 1


def test_native_tool_loop_humanizes_last_tool_text_fallback():
    from bigas.agents.chief_of_staff import _run_native_tool_loop

    class FakeLLM:
        def __init__(self):
            self.turns = 0

        def complete_detailed(self, messages, **kwargs):
            self.turns += 1
            if self.turns == 1:
                return LLMCompletion(
                    text="",
                    tool_calls=(
                        ToolCall(id="c1", name="fetch_github_activity", arguments={}),
                    ),
                )
            return LLMCompletion(text="")

        def complete(self, messages, **kwargs):
            return "Samarbete och delning är den största nyheten."

    result = _run_native_tool_loop(
        FakeLLM(),
        [{"role": "user", "content": "Vad är nytt?"}],
        [{"type": "function", "function": {"name": "fetch_github_activity", "parameters": {}}}],
        run_tool=lambda name, args: GITHUB_ACTIVITY_DUMP,
    )
    assert result == "Samarbete och delning är den största nyheten."


def test_json_agent_loop_humanizes_json_answer(monkeypatch):
    from bigas.agents.chief_of_staff import _run_json_agent_loop

    calls = {"select": 0}

    def fake_select(*args, **kwargs):
        calls["select"] += 1
        if calls["select"] == 1:
            return "", "fetch_github_activity", {"project_key": "VFA"}
        return GITHUB_ACTIVITY_DUMP, None, None

    class RewriteLLM:
        def complete(self, messages, **kwargs):
            return "🤝 **Samarbete och delning**\nFörenklad behörighetshantering."

    monkeypatch.setattr(
        "bigas.agents.chief_of_staff._select_tool_via_llm",
        fake_select,
    )
    monkeypatch.setattr(
        "bigas.agents.chief_of_staff.get_llm_client",
        lambda feature="chat": (RewriteLLM(), "gemini-test"),
    )

    result = _run_json_agent_loop(
        agent_id="product",
        agent_config={"agent_id": "product", "system_prompt_goals": ""},
        user_message="Vad för nya funktioner har vi lanserat efter 17 augusti?",
        tools=[],
        history=[],
        run_tool=lambda name, args: GITHUB_ACTIVITY_DUMP,
    )
    assert "Samarbete och delning" in result
    assert not result.strip().startswith("{")
