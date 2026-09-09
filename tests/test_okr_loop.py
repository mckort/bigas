"""Tool loop for OKR / Epic goal work."""

from __future__ import annotations

import json

from bigas.llm.completion import LLMCompletion, ToolCall
from bigas.okr.loop import (
    GoalSnapshot,
    evidence_numbers,
    run_goal_loop,
    snapshot_from_okr,
    tools_for_phase,
)


class _ScriptedLlm:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def complete_detailed(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        turn = self.turns.pop(0) if self.turns else LLMCompletion(text="")
        if isinstance(turn, LLMCompletion):
            return turn
        return LLMCompletion(text=str(turn))


def _call(name, **arguments):
    return LLMCompletion(
        text="",
        tool_calls=(ToolCall(id=f"call_{name}", name=name, arguments=arguments),),
    )


def _snapshot(**overrides):
    base = GoalSnapshot(
        kind="objective",
        phase="research",
        key="GPWW-15",
        title="10 paying customers",
        brand="Green Promo Wear",
        evidence={
            "brand": "Green Promo Wear",
            "ga4": "sessions 43, sample-to-paid conversion 5%",
            "website": "SAMPLE30 on site",
        },
        key_results=[],
        open_work=[{"key": "GPWW-1", "title": "Update catalog", "summary": "Update catalog"}],
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_tools_research_includes_kr_proposal_not_scoreboard_update():
    names = {tool["function"]["name"] for tool in tools_for_phase("research", kind="objective")}
    assert "propose_key_results" in names
    assert "get_evidence" in names
    assert "update_kr_current" not in names


def test_tools_plan_can_update_currents_and_tasks():
    names = {tool["function"]["name"] for tool in tools_for_phase("plan", kind="objective")}
    assert "propose_tasks" in names
    assert "update_kr_current" in names
    assert "get_scoreboard" in names
    assert "propose_key_results" not in names


def test_epic_research_has_tasks_not_krs():
    names = {tool["function"]["name"] for tool in tools_for_phase("research", kind="epic")}
    assert "propose_tasks" in names
    assert "propose_key_results" not in names


def test_loop_looks_up_then_proposes_grounded_krs():
    llm = _ScriptedLlm(
        [
            _call("get_evidence"),
            _call(
                "propose_key_results",
                key_results=[
                    {
                        "title": "Increase sample-to-paid conversion from 5 to 15",
                        "metric": "conversion",
                        "baseline": 5,
                        "target": 15,
                        "current": 5,
                        "source": "ga4",
                        "measurable": True,
                    }
                ],
            ),
            _call("set_notes", briefing="Grounded in GA4.", markdown="Used sessions and conversion."),
            _call("done"),
        ]
    )
    result = run_goal_loop(llm, snapshot=_snapshot())
    assert result.used_tools
    assert result.done
    assert result.tool_trace[:4] == [
        "get_evidence",
        "propose_key_results",
        "set_notes",
        "done",
    ]
    assert result.key_results[0]["measurable"] is True
    assert "5" in result.key_results[0]["title"] or result.key_results[0]["baseline"] == 5
    assert result.briefing == "Grounded in GA4."


def test_loop_marks_invented_baseline_unmeasured():
    llm = _ScriptedLlm(
        [
            _call(
                "propose_key_results",
                key_results=[
                    {
                        "title": "Increase NPS from 72 to 80",
                        "metric": "NPS",
                        "baseline": 72,
                        "target": 80,
                        "current": 72,
                        "source": "manual",
                        "measurable": True,
                    }
                ],
            ),
            _call("done"),
        ]
    )
    result = run_goal_loop(llm, snapshot=_snapshot())
    assert result.key_results[0]["measurable"] is False
    assert "72" in (result.key_results[0].get("measurement_gap") or "")


def test_loop_drops_kr_clone_and_wiring_tasks():
    krs = [
        {
            "id": "kr-sess",
            "title": "Increase website sessions from 43 to 1000",
            "status": "committed",
            "measurable": True,
            "baseline": 43,
            "target": 1000,
            "current": 43,
        }
    ]
    llm = _ScriptedLlm(
        [
            _call(
                "propose_tasks",
                tasks=[
                    {
                        "title": "Increase website sessions from 43 to 1000",
                        "description": "Clone",
                        "kr_id": "kr-sess",
                    },
                    {
                        "title": "Wire weekly snapshot for sessions",
                        "description": "Wiring",
                        "kr_id": "kr-sess",
                    },
                    {
                        "title": "Publish a wholesale kit landing page",
                        "description": "Convert sessions into inquiries.",
                        "kr_id": "kr-sess",
                    },
                ],
            ),
            _call("done"),
        ]
    )
    result = run_goal_loop(
        llm,
        snapshot=_snapshot(phase="plan", key_results=krs),
    )
    titles = [t["title"] for t in result.tasks]
    assert titles == ["Publish a wholesale kit landing page"]


def test_loop_rejects_duplicate_open_work():
    krs = [
        {
            "id": "kr-sess",
            "title": "Increase website sessions from 43 to 1000",
            "status": "committed",
            "measurable": True,
        }
    ]
    llm = _ScriptedLlm(
        [
            _call(
                "propose_tasks",
                tasks=[
                    {
                        "title": "Update catalog",
                        "description": "Already open.",
                        "kr_id": "kr-sess",
                    }
                ],
            ),
            _call("done"),
        ]
    )
    result = run_goal_loop(
        llm,
        snapshot=_snapshot(
            phase="plan",
            key_results=krs,
            open_work=[{"title": "Update catalog", "summary": "Update catalog"}],
        ),
    )
    assert result.tasks == []


def test_update_kr_current_requires_evidence_number():
    llm = _ScriptedLlm(
        [
            _call("update_kr_current", updates=[{"id": "kr-sess", "current": 999}]),
            _call("update_kr_current", updates=[{"id": "kr-sess", "current": 43}]),
            _call("done"),
        ]
    )
    result = run_goal_loop(llm, snapshot=_snapshot(phase="plan"))
    assert result.current_updates == [{"id": "kr-sess", "current": 43.0}]
    assert any("999" in note for note in result.rejected)


def test_oneshot_json_still_works_without_tool_calls():
    llm = _ScriptedLlm(
        [
            LLMCompletion(
                text=json.dumps(
                    {
                        "briefing": "ok",
                        "research_markdown": "from dump",
                        "key_results": [
                            {
                                "title": "Increase sessions from 43 to 80",
                                "baseline": 43,
                                "target": 80,
                                "current": 43,
                                "measurable": True,
                            }
                        ],
                    }
                )
            )
        ]
    )
    result = run_goal_loop(llm, snapshot=_snapshot())
    assert not result.used_tools
    assert result.key_results
    assert result.wrote
    assert result.key_results[0].get("source") == "ga4"
    assert result.notes_markdown == "from dump"


def test_snapshot_from_okr_copies_open_work():
    snap = snapshot_from_okr(
        {"key": "GPWW-15", "title": "Win", "key_results": []},
        phase="plan",
        evidence={"brand": "Green Promo Wear"},
        open_work=[{"title": "A"}],
    )
    assert snap.kind == "objective"
    assert snap.brand == "Green Promo Wear"
    assert snap.open_work[0]["title"] == "A"


def test_evidence_numbers_strip_commas():
    assert "12000" in evidence_numbers({"ga4": "sessions 12,000"})


def test_read_only_research_drops_saas_kr_and_is_not_success():
    from bigas.okr.loop import system_prompt_for

    llm = _ScriptedLlm([_call("get_evidence"), _call("done"), _call("done")])
    snap = _snapshot(
        key_results=[
            {
                "id": "kr-saas01",
                "title": "40 weekly active founders",
                "status": "proposed",
                "measurable": True,
                "baseline": 12,
                "target": 40,
                "current": 12,
            }
        ]
    )
    result = run_goal_loop(llm, snapshot=snap)
    assert not result.used_llm
    assert not result.wrote
    assert result.key_results == []
    system = llm.calls[0]["messages"][0]["content"]
    assert "from <baseline> to <target>" in system
    assert "weekly active founders" in system_prompt_for(snap).lower() or "SaaS-kit" in system


def test_nudge_then_propose_counts_as_write():
    llm = _ScriptedLlm(
        [
            _call("get_evidence"),
            _call("done"),
            _call(
                "propose_key_results",
                key_results=[
                    {
                        "title": "Increase sessions from 43 to 80",
                        "baseline": 43,
                        "target": 80,
                        "current": 43,
                        "measurable": True,
                    }
                ],
            ),
            _call("done"),
        ]
    )
    result = run_goal_loop(llm, snapshot=_snapshot())
    assert result.wrote
    assert result.used_llm
    assert result.key_results[0]["title"].startswith("Increase")
    assert result.key_results[0].get("source") == "ga4"


def test_rejects_saas_kit_title_shape():
    llm = _ScriptedLlm(
        [
            _call(
                "propose_key_results",
                key_results=[
                    {
                        "title": "40 weekly active founders",
                        "baseline": 12,
                        "target": 40,
                        "current": 12,
                        "measurable": True,
                    }
                ],
            ),
            _call("done"),
            _call("done"),
        ]
    )
    result = run_goal_loop(llm, snapshot=_snapshot())
    assert not result.wrote
    assert result.key_results == []
    assert any("Increase/Decrease" in note for note in result.rejected)


def test_done_after_nudge_without_write_is_rejected():
    llm = _ScriptedLlm([_call("get_evidence"), _call("done"), _call("done")])
    result = run_goal_loop(llm, snapshot=_snapshot(), max_turns=3)
    assert not result.wrote
    assert any("done without propose_*" in note for note in result.rejected)
    assert result.tool_trace.count("done") == 2


def test_kr_title_normalizes_quotes_and_whitespace():
    llm = _ScriptedLlm(
        [
            _call(
                "propose_key_results",
                key_results=[
                    {
                        "title": '"Increase sessions\nfrom 43\nto 80"',
                        "baseline": 43,
                        "target": 80,
                        "current": 43,
                        "measurable": True,
                    }
                ],
            ),
            _call("done"),
        ]
    )
    result = run_goal_loop(llm, snapshot=_snapshot())
    assert result.wrote
    assert result.key_results[0]["title"] == "Increase sessions from 43 to 80"
