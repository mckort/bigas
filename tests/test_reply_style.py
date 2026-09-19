"""Tests for default human-friendly chat reply style."""

from __future__ import annotations

import json

from bigas.chat.reply_style import (
    looks_like_incomplete_chat_reply,
    looks_like_raw_tool_dump,
    looks_like_ticket_dump,
)
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


def test_looks_like_ticket_dump_detects_inline_status_lookup_line():
    dump = "[Add catalog modules](/board?ticket=GPWW-40) — In Progress (AI)"
    assert looks_like_ticket_dump(dump)
    assert looks_like_incomplete_chat_reply(dump)


def test_looks_like_ticket_dump_detects_lookup_metadata_blocks():
    dump = (
        "[Add catalog modules](/board?ticket=GPWW-40) — In Progress (AI)\n\n"
        "Parent (Epic): [Platform work](/board?ticket=GPWW-1)\n\n"
        "Open Epics:\n"
        "- [Another epic](/board?ticket=GPWW-2)\n\n"
        "Missing: GPWW-99"
    )
    assert looks_like_ticket_dump(dump)


def test_looks_like_ticket_dump_detects_title_and_move_button():
    dump = (
        "[Add catalog modules](/board?ticket=GPWW-40)\n\n"
        "[Move to next column](bigas://action/jira_transition?issue=GPWW-40)\n"
        "Status: In Progress (AI)"
    )
    assert looks_like_ticket_dump(dump)
    assert looks_like_incomplete_chat_reply(dump)
    assert not looks_like_ticket_dump(
        "GPWW-40 is still in progress. The implement agent is here: "
        "https://cursor.com/agents/bc-1\n\n"
        "[Add catalog modules](/board?ticket=GPWW-40)\n\n"
        "[Move to next column](bigas://action/jira_transition?issue=GPWW-40)"
    )


def test_looks_like_ticket_dump_ignores_prose_with_links_and_bullets():
    prose_link = (
        "[PROJ-123: Feature Title](https://example.atlassian.net/browse/PROJ-123) — "
        "The fix has been deployed to production and verified."
    )
    assert not looks_like_ticket_dump(prose_link)
    two_links = (
        "[Docs](https://example.com/docs) for auth can be found "
        "[here](https://example.com/auth)"
    )
    assert not looks_like_ticket_dump(two_links)
    changelog = (
        "Finished this sprint:\n\n"
        "- [GPWW-1: Login](/board?ticket=GPWW-1) — shipped SSO to all tenants.\n"
        "- [GPWW-2: Billing](/board?ticket=GPWW-2) — fixed proration edge case."
    )
    assert not looks_like_ticket_dump(changelog)


def test_looks_like_ticket_dump_detects_atlassian_move_button_only():
    dump = (
        "[Fix checkout](https://example.atlassian.net/browse/GPWW-40)\n\n"
        "Status: In Progress (AI)\n\n"
        "[Move to next column](bigas://action/jira_transition?issue=GPWW-40)"
    )
    assert looks_like_ticket_dump(dump)
    answer = (
        "GPWW-40 is still **In Progress (AI)**. The Cursor agent is running and "
        "there is no PR yet.\n\n"
        "[Fix checkout](/board?ticket=GPWW-40)\n\n"
        "[Move to next column](bigas://action/jira_transition?issue=GPWW-40)"
    )
    assert not looks_like_ticket_dump(answer)


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


def test_native_tool_loop_rewrites_ticket_dump_as_answer():
    from bigas.agents.chief_of_staff import _run_native_tool_loop

    ticket_dump = (
        "[Add catalog modules](/board?ticket=GPWW-40)\n\n"
        "[Move to next column](bigas://action/jira_transition?issue=GPWW-40)\n"
        "Status: In Progress (AI)\n"
        "Agent: https://cursor.com/agents/bc-1"
    )

    class FakeLLM:
        def __init__(self):
            self.turns = 0

        def complete_detailed(self, messages, **kwargs):
            self.turns += 1
            if self.turns == 1:
                return LLMCompletion(
                    text="",
                    tool_calls=(
                        ToolCall(id="c1", name="lookup_ticket", arguments={"issue_key": "GPWW-40"}),
                    ),
                )
            return LLMCompletion(text=ticket_dump)

        def complete(self, messages, **kwargs):
            return (
                "GPWW-40 is still in progress. Follow the agent: "
                "https://cursor.com/agents/bc-1"
            )

    result = _run_native_tool_loop(
        FakeLLM(),
        [
            {
                "role": "user",
                "content": "What is the status of GPWW-40? Where is the agent link?",
            }
        ],
        [{"type": "function", "function": {"name": "lookup_ticket", "parameters": {}}}],
        run_tool=lambda name, args: ticket_dump,
    )
    assert "Follow the agent" in result
    assert "https://cursor.com/agents/bc-1" in result
    assert "Move to next column" not in result


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
