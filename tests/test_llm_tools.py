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


def test_native_tool_loop_passes_generation_kwargs():
    from bigas.agents.chief_of_staff import _run_native_tool_loop

    seen = []

    class CaptureLLM:
        def complete_detailed(self, messages, **kwargs):
            seen.append(kwargs)
            return LLMCompletion(text="Baseline is 120 sessions.")

    result = _run_native_tool_loop(
        CaptureLLM(),
        [{"role": "user", "content": "how do we get to 1000 sessions?"}],
        [],
        run_tool=lambda name, args: "unused",
        generation_kwargs={"temperature": 0.4, "max_tokens": 16384, "thinking_budget": 8192},
    )
    assert result == "Baseline is 120 sessions."
    assert seen[0]["temperature"] == 0.4
    assert seen[0]["max_tokens"] == 16384
    assert seen[0]["thinking_budget"] == 8192


def test_json_agent_loop_passes_generation_kwargs(monkeypatch):
    from bigas.agents.chief_of_staff import _run_json_agent_loop

    seen = []

    def fake_select(
        agent_id,
        agent_config,
        user_message,
        tools,
        history,
        *,
        user_id=None,
        generation_kwargs=None,
    ):
        seen.append(generation_kwargs)
        return "Strategy complete.", None, None

    monkeypatch.setattr(
        "bigas.agents.chief_of_staff._select_tool_via_llm",
        fake_select,
    )

    result = _run_json_agent_loop(
        agent_id="marketing",
        agent_config={"agent_id": "marketing", "system_prompt_goals": ""},
        user_message="how do we get to 1000 sessions?",
        tools=[],
        history=[],
        run_tool=lambda name, args: "unused",
        generation_kwargs={
            "temperature": 0.4,
            "max_tokens": 16384,
            "thinking_budget": 8192,
        },
    )
    assert result == "Strategy complete."
    assert seen[0]["temperature"] == 0.4
    assert seen[0]["max_tokens"] == 16384
    assert seen[0]["thinking_budget"] == 8192


def test_gemini_safe_schema_strips_unsupported_keywords():
    from bigas.llm.gemini_client import _gemini_safe_schema, _openai_tools_to_gemini_decls

    cleaned = _gemini_safe_schema(
        {
            "$schema": "https://json-schema.org/draft/07/schema",
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "jql": {"type": ["string", "null"], "description": "JQL query"},
            },
            "anyOf": [{"type": "object"}],
        }
    )
    assert "$schema" not in cleaned
    assert "additionalProperties" not in cleaned
    assert cleaned["properties"]["jql"]["type"] == "string"

    decls = _openai_tools_to_gemini_decls(
        [
            {
                "type": "function",
                "function": {
                    "name": "search_jira",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"jql": {"type": "string"}},
                    },
                },
            }
        ]
    )
    assert decls[0]["parameters"]["properties"]["jql"]["type"] == "string"
    assert "additionalProperties" not in decls[0]["parameters"]


def test_is_malformed_function_call():
    from bigas.llm.gemini_client import is_malformed_function_call

    err = 'Error: index: 0 content { parts { text: "" } role: "model" } finish_reason: MALFORMED_FUNCTION_CALL'
    assert is_malformed_function_call(err)
    assert is_malformed_function_call("MALFORMED_FUNCTION_CALL")
    assert not is_malformed_function_call("STOP")


def test_native_tool_loop_falls_back_on_empty_first_turn():
    from bigas.agents.chief_of_staff import _run_native_tool_loop
    from bigas.llm.completion import LLMCompletion

    class EmptyLLM:
        def complete_detailed(self, messages, **kwargs):
            return LLMCompletion(text="", finish_reason="MALFORMED_FUNCTION_CALL")

    assert (
        _run_native_tool_loop(
            EmptyLLM(),
            [{"role": "user", "content": "hi"}],
            [{"type": "function", "function": {"name": "lookup_jira", "parameters": {}}}],
            run_tool=lambda name, args: "nope",
        )
        is None
    )


def test_run_agent_with_tools_swallows_malformed_function_call():
    from unittest.mock import patch

    from bigas.agents.chief_of_staff import _run_agent_with_tools

    class BoomLLM:
        def complete_detailed(self, messages, **kwargs):
            raise ValueError(
                'Error: index: 0 content { parts { text: "" } role: "model" } '
                "finish_reason: MALFORMED_FUNCTION_CALL"
            )

        def complete(self, messages, **kwargs):
            raise ValueError(
                'Error: index: 0 content { parts { text: "" } role: "model" } '
                "finish_reason: MALFORMED_FUNCTION_CALL"
            )

    with patch(
        "bigas.agents.chief_of_staff.get_llm_client",
        lambda feature="chat": (BoomLLM(), "gemini-test"),
    ):
        text = _run_agent_with_tools(
            agent_id="chief",
            agent_config={"system_prompt_goals": ""},
            user_message="Let's discuss ticket GPWW-17",
            tools=[],
            history=[],
            run_tool=lambda name, args: "nope",
        )
    assert "model error" in text.lower()
    assert "MALFORMED_FUNCTION_CALL" not in text


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


def _fake_gemini_text_response(text: str = "ok"):
    part = SimpleNamespace(text=text, function_call=None)
    candidate = SimpleNamespace(
        finish_reason="STOP",
        content=SimpleNamespace(parts=[part]),
        safety_ratings=None,
    )
    return SimpleNamespace(candidates=[candidate], usage_metadata=None)


def test_gemini_maps_timeout_to_request_options():
    from unittest.mock import MagicMock, patch

    from bigas.llm.gemini_client import GeminiLLMClient

    model = MagicMock()
    model.generate_content.return_value = _fake_gemini_text_response("done")

    with patch("bigas.llm.gemini_client.genai") as genai:
        genai.GenerativeModel.return_value = model
        client = GeminiLLMClient(api_key="test-key", model="gemini-test")

    result = client.complete_detailed(
        [{"role": "user", "content": "analyze this page"}],
        max_tokens=800,
        temperature=0.7,
        timeout=30,
    )

    assert result.text == "done"
    kwargs = model.generate_content.call_args.kwargs
    assert "timeout" not in kwargs
    assert kwargs["request_options"] == {"timeout": 30}
