"""Unit tests for native LLM tool-calling helpers (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from bigas.llm.completion import LLMCompletion, ToolCall
from bigas.llm.gemini_client import (
    gemini_contents_from_messages,
    tool_calls_from_gemini_parts,
)
from bigas.llm.openai_client import _tool_calls_from_openai_message


def test_openai_parses_tool_calls():
    message = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                function=SimpleNamespace(
                    name="search_jira",
                    arguments='{"jql":"type = Bug"}',
                ),
            )
        ]
    )
    calls = _tool_calls_from_openai_message(message)
    assert len(calls) == 1
    assert calls[0].name == "search_jira"
    assert calls[0].arguments == {"jql": "type = Bug"}


def test_gemini_contents_roundtrip_tool_turns():
    system, contents = gemini_contents_from_messages(
        [
            {"role": "system", "content": "You are Chief."},
            {"role": "user", "content": "open VFA bugs"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_0_search_jira",
                        "type": "function",
                        "function": {
                            "name": "search_jira",
                            "arguments": '{"jql":"project = VFA AND type = Bug"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_0_search_jira",
                "name": "search_jira",
                "content": "VFA-1 Stripe webhook",
            },
        ]
    )
    assert system == "You are Chief."
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["function_call"]["name"] == "search_jira"
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["function_response"]["name"] == "search_jira"


def test_gemini_parses_function_call_parts():
    text, calls = tool_calls_from_gemini_parts(
        [
            {"text": "Checking Jira."},
            {"function_call": {"name": "search_jira", "args": {"jql": "type = Bug"}}},
        ]
    )
    assert text == "Checking Jira."
    assert len(calls) == 1
    assert calls[0].name == "search_jira"
    assert calls[0].arguments["jql"] == "type = Bug"


def test_native_tool_loop_runs_then_answers():
    from bigas.agents.chief_of_staff import _run_native_tool_loop

    calls = []

    class FakeLLM:
        def __init__(self):
            self.turns = 0

        def complete_detailed(self, messages, **kwargs):
            self.turns += 1
            if self.turns == 1:
                return LLMCompletion(
                    text="",
                    tool_calls=(
                        ToolCall(id="c1", name="search_jira", arguments={"jql": "type = Bug"}),
                    ),
                )
            return LLMCompletion(text="One open bug: VFA-1.")

    result = _run_native_tool_loop(
        FakeLLM(),
        [{"role": "user", "content": "open bugs?"}],
        [{"type": "function", "function": {"name": "search_jira", "parameters": {}}}],
        run_tool=lambda name, args: calls.append((name, args)) or "VFA-1 Stripe",
    )
    assert result == "One open bug: VFA-1."
    assert calls == [("search_jira", {"jql": "type = Bug"})]


def test_native_tool_loop_falls_back_when_first_turn_fails():
    from bigas.agents.chief_of_staff import _run_native_tool_loop

    class BoomLLM:
        def complete_detailed(self, messages, **kwargs):
            raise RuntimeError("no tools")

    assert (
        _run_native_tool_loop(
            BoomLLM(),
            [{"role": "user", "content": "hi"}],
            [],
            run_tool=lambda name, args: "nope",
        )
        is None
    )
