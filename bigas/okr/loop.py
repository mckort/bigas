"""Tool loop for OKR / Epic goal work.

Production clients that expose ``complete_detailed`` look up evidence and open
work instead of guessing from a dumped prompt. Tests that only implement
``complete()`` keep the previous one-shot JSON path.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from bigas.llm.completion import LLMCompletion, ToolCall
from bigas.okr.context import format_evidence_pack
from bigas.okr.model import normalize_key_results
from bigas.okr.plan import MAX_TASKS_TOTAL, _normalize_plan_tasks, is_mechanical_okr_task
from bigas.okr.research import _extract_json_object, _merge_key_results

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 8
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

KIND_OBJECTIVE = "objective"
KIND_EPIC = "epic"
PHASE_RESEARCH = "research"
PHASE_PLAN = "plan"
PHASE_IN_PROGRESS = "in_progress"

LOOP_SYSTEM = """You are the Chief of Staff. Use tools to inspect the goal, evidence, and
open work before you propose anything. Do not invent numbers. Do not clone a
Key Result title into a ticket. Do not create analytics-wiring tickets
("wire weekly snapshot", "Instrument: …"). Call done when you are finished.

Rules:
- Key Results are measurable from→to improvements grounded in evidence.
- Tasks are concrete actions that would move a KR (or an Epic), scoped to one
  person or AI agent. Prefer fewer, sharper tickets (1–3 per KR, at most 10).
- ai_doable=true only when an AI agent can do the first pass in the mapped repo.
- Off-track KRs get proposed To Do work. Never auto-start or auto-advance cards.
- If a number is missing from evidence, mark the KR measurable=false.
"""


def _fn(name: str, description: str, properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": schema},
    }


KR_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "metric": {"type": "string"},
        "unit": {"type": "string"},
        "baseline": {"type": "number"},
        "target": {"type": "number"},
        "current": {"type": "number"},
        "source": {"type": "string"},
        "measurable": {"type": "boolean"},
        "measurement_gap": {"type": "string"},
        "direction": {"type": "string"},
        "ai_note": {"type": "string"},
    },
    "required": ["title"],
}

TASK_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "description": {"type": "string"},
        "kr_id": {"type": "string"},
        "ai_doable": {"type": "boolean"},
        "issue_type": {"type": "string"},
        "marketing": {"type": "boolean"},
    },
    "required": ["description"],
}

READ_TOOLS = [
    _fn("get_goal", "Goal, committed/proposed Key Results, and cycle.", {}),
    _fn("get_evidence", "Live evidence pack (brand, GA4, site, repo, board).", {}),
    _fn("get_scoreboard", "Mechanical KR health, pace, stale currents, open work.", {}),
    _fn("list_open_work", "Open tickets already linked to this goal. Do not duplicate.", {}),
]
WRITE_TOOLS = [
    _fn(
        "propose_key_results",
        "Replace proposed Key Results. Committed KRs stay. Research phase only.",
        {"key_results": {"type": "array", "items": KR_ITEM_SCHEMA}},
        ["key_results"],
    ),
    _fn(
        "propose_tasks",
        "Propose To Do tasks. Not KR titles. Not analytics wiring.",
        {"tasks": {"type": "array", "items": TASK_ITEM_SCHEMA}},
        ["tasks"],
    ),
    _fn(
        "update_kr_current",
        "Set current on a measurable KR to a number that is in the evidence.",
        {
            "updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "current": {"type": "number"}},
                    "required": ["id", "current"],
                },
            }
        },
        ["updates"],
    ),
    _fn(
        "set_notes",
        "Human-facing briefing plus research/plan/progress markdown.",
        {
            "briefing": {"type": "string"},
            "markdown": {"type": "string"},
            "analysis": {"type": "string"},
        },
    ),
    _fn("done", "Finish this phase. Call after proposals (or when nothing new is needed).", {}),
]


def tools_for_phase(phase: str, *, kind: str) -> List[Dict[str, Any]]:
    names = {"get_goal", "get_evidence", "list_open_work", "set_notes", "done"}
    if phase in {PHASE_PLAN, PHASE_IN_PROGRESS}:
        names.update({"get_scoreboard", "propose_tasks", "update_kr_current"})
    if kind == KIND_OBJECTIVE and phase == PHASE_RESEARCH:
        names.add("propose_key_results")
    if kind == KIND_EPIC:
        names.add("propose_tasks")
        names.discard("propose_key_results")
        names.discard("update_kr_current")
        if phase == PHASE_RESEARCH:
            names.discard("get_scoreboard")
    return [tool for tool in READ_TOOLS + WRITE_TOOLS if tool["function"]["name"] in names]


@dataclass
class GoalSnapshot:
    kind: str
    phase: str
    key: str
    title: str
    description: str = ""
    brand: str = ""
    cycle: str = ""
    key_results: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, str] = field(default_factory=dict)
    scoreboard: Dict[str, Any] = field(default_factory=dict)
    open_work: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class GoalLoopResult:
    key_results: List[Dict[str, Any]] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    current_updates: List[Dict[str, Any]] = field(default_factory=list)
    briefing: str = ""
    notes_markdown: str = ""
    analysis: str = ""
    used_tools: bool = False
    used_llm: bool = False
    turns: int = 0
    tool_trace: List[str] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    done: bool = False


def llm_supports_tools(llm: Any) -> bool:
    return callable(getattr(llm, "complete_detailed", None))


def max_turns_from_env() -> int:
    raw = (os.environ.get("BIGAS_GOAL_LOOP_MAX_TURNS") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_TURNS
    except ValueError:
        value = DEFAULT_MAX_TURNS
    return max(2, min(16, value))


def evidence_numbers(evidence: Dict[str, str], extra: str = "") -> set[str]:
    blob = " ".join(str(v) for v in evidence.values()) + " " + extra
    blob = blob.replace(",", "")
    return {m.group(0) for m in _NUMBER_RE.finditer(blob)}


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class _Session:
    def __init__(self, snapshot: GoalSnapshot):
        self.snapshot = snapshot
        committed = [kr for kr in snapshot.key_results if str(kr.get("status") or "").lower() == "committed"]
        proposed = [kr for kr in snapshot.key_results if str(kr.get("status") or "").lower() != "committed"]
        self.committed = list(committed)
        self.proposed = list(proposed)
        self.tasks: List[Dict[str, Any]] = []
        self.current_updates: List[Dict[str, Any]] = []
        self.briefing = ""
        self.notes_markdown = ""
        self.analysis = ""
        self.rejected: List[str] = []
        self.done = False

    def result(self, *, used_tools: bool, used_llm: bool, turns: int, trace: List[str]) -> GoalLoopResult:
        merged = (
            _merge_key_results(committed=self.committed, proposed=self.proposed)
            if self.snapshot.kind == KIND_OBJECTIVE
            else list(self.snapshot.key_results)
        )
        return GoalLoopResult(
            key_results=merged,
            tasks=list(self.tasks),
            current_updates=list(self.current_updates),
            briefing=self.briefing,
            notes_markdown=self.notes_markdown,
            analysis=self.analysis,
            used_tools=used_tools,
            used_llm=used_llm,
            turns=turns,
            tool_trace=list(trace),
            rejected=list(self.rejected),
            done=self.done,
        )


def _open_work_titles(snapshot: GoalSnapshot) -> set[str]:
    titles = set()
    for item in snapshot.open_work:
        for key in ("title", "summary"):
            text = str(item.get(key) or "").strip().lower()
            if text:
                titles.add(text)
    return titles


def _ground_key_results(raw: Any, *, snapshot: GoalSnapshot) -> List[Dict[str, Any]]:
    numbers = evidence_numbers(snapshot.evidence, extra=snapshot.title + " " + snapshot.description)
    grounded: List[Dict[str, Any]] = []
    for kr in normalize_key_results(raw if isinstance(raw, list) else []):
        if not kr.get("measurable"):
            grounded.append(kr)
            continue
        baseline = kr.get("baseline")
        current = kr.get("current")
        missing = []
        if baseline not in (None, "") and _NUMBER_RE.fullmatch(str(baseline).replace(",", "")):
            token = str(baseline).replace(",", "")
            if token not in numbers and str(int(float(token))) not in numbers:
                missing.append(f"baseline {token}")
        if current not in (None, "") and _NUMBER_RE.fullmatch(str(current).replace(",", "")):
            token = str(current).replace(",", "")
            if token not in numbers and str(int(float(token))) not in numbers:
                missing.append(f"current {token}")
        if missing:
            kr["measurable"] = False
            kr["measurement_gap"] = (
                (kr.get("measurement_gap") or "").strip()
                or f"Number not in evidence: {', '.join(missing)}."
            )
        grounded.append(kr)
    return grounded


def _dispatch(session: _Session, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    snap = session.snapshot
    if name == "get_goal":
        return {
            "kind": snap.kind,
            "phase": snap.phase,
            "key": snap.key,
            "title": snap.title,
            "description": snap.description,
            "brand": snap.brand,
            "cycle": snap.cycle,
            "key_results": snap.key_results,
        }
    if name == "get_evidence":
        return {"evidence": snap.evidence, "pack": format_evidence_pack(snap.evidence)}
    if name == "get_scoreboard":
        return snap.scoreboard or {"note": "No mechanical scoreboard for this snapshot."}
    if name == "list_open_work":
        return {"open_work": snap.open_work}
    if name == "propose_key_results":
        if snap.kind != KIND_OBJECTIVE or snap.phase != PHASE_RESEARCH:
            return {"ok": False, "error": "propose_key_results is only valid during Objective research."}
        grounded = _ground_key_results(arguments.get("key_results"), snapshot=snap)
        if not grounded:
            session.rejected.append("propose_key_results: empty after grounding")
            return {"ok": False, "error": "No usable Key Results."}
        session.proposed = grounded
        return {"ok": True, "accepted": len(grounded)}
    if name == "propose_tasks":
        raw = arguments.get("tasks") if isinstance(arguments.get("tasks"), list) else []
        if snap.kind == KIND_OBJECTIVE:
            krs = _merge_key_results(committed=session.committed, proposed=session.proposed) or snap.key_results
            accepted = _normalize_plan_tasks(
                raw,
                key_results=krs,
                existing_titles=_open_work_titles(snap) | {t["title"].lower() for t in session.tasks},
            )
        else:
            accepted = _normalize_epic_tasks(raw, existing_titles=_open_work_titles(snap) | {t["title"].lower() for t in session.tasks})
        dropped = max(0, len(raw) - len(accepted))
        session.tasks.extend(accepted)
        if dropped:
            session.rejected.append(f"propose_tasks: dropped {dropped} clone/wiring/duplicate items")
        return {"ok": True, "accepted": len(accepted), "dropped": dropped}
    if name == "update_kr_current":
        numbers = evidence_numbers(snap.evidence)
        applied = 0
        for item in arguments.get("updates") or []:
            if not isinstance(item, dict) or "current" not in item:
                continue
            kr_id = str(item.get("id") or "").strip()
            try:
                current = float(item["current"])
            except (TypeError, ValueError):
                continue
            token = str(int(current)) if current == int(current) else str(current)
            if token not in numbers and str(current) not in numbers:
                session.rejected.append(f"update_kr_current {kr_id}: {token} not in evidence")
                continue
            session.current_updates.append({"id": kr_id, "current": current})
            applied += 1
        return {"ok": True, "applied": applied}
    if name == "set_notes":
        session.briefing = str(arguments.get("briefing") or session.briefing).strip()
        session.notes_markdown = str(arguments.get("markdown") or session.notes_markdown).strip()
        session.analysis = str(arguments.get("analysis") or session.analysis).strip()
        return {"ok": True}
    if name == "done":
        session.done = True
        return {"ok": True}
    return {"ok": False, "error": f"Unknown tool {name}"}


def _normalize_epic_tasks(raw: Any, *, existing_titles: set[str]) -> List[Dict[str, Any]]:
    seen = set(existing_titles)
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("summary") or "").strip()
        description = str(item.get("description") or "").strip()
        if not title or not description:
            continue
        if is_mechanical_okr_task({"title": title}, []):
            continue
        key = title.lower()
        if key in seen:
            continue
        out.append(
            {
                "title": title[:120],
                "summary": title[:120],
                "description": description,
                "issue_type": str(item.get("issue_type") or "Task").strip().title() or "Task",
                "marketing": bool(item.get("marketing")),
                "ai_doable": bool(item.get("ai_doable")),
            }
        )
        seen.add(key)
        if len(out) >= MAX_TASKS_TOTAL:
            break
    return out


def _apply_oneshot(session: _Session, text: str) -> bool:
    try:
        parsed = _extract_json_object(text)
    except (ValueError, json.JSONDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    if parsed.get("key_results"):
        session.proposed = _ground_key_results(parsed.get("key_results"), snapshot=session.snapshot)
    raw_tasks = parsed.get("tasks_to_create") or parsed.get("tasks")
    if raw_tasks:
        _dispatch(session, "propose_tasks", {"tasks": raw_tasks})
    if isinstance(parsed.get("key_result_updates"), list):
        _dispatch(session, "update_kr_current", {"updates": parsed["key_result_updates"]})
    session.briefing = str(parsed.get("briefing") or session.briefing).strip()
    session.notes_markdown = str(
        parsed.get("research_markdown") or parsed.get("plan_markdown") or parsed.get("progress_report") or session.notes_markdown
    ).strip()
    session.analysis = str(parsed.get("analysis") or parsed.get("plan_summary") or session.analysis).strip()
    session.done = True
    return True


def _assistant_message(completion: LLMCompletion) -> Dict[str, Any]:
    tool_calls = []
    for call in completion.tool_calls:
        tool_calls.append(
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
        )
    message: Dict[str, Any] = {"role": "assistant", "content": completion.text or ""}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _invoke(
    llm: Any,
    messages: List[Dict[str, Any]],
    *,
    tools: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    extra: Dict[str, Any],
) -> LLMCompletion:
    detailed = getattr(llm, "complete_detailed", None)
    if callable(detailed):
        return detailed(
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            **extra,
        )
    text = llm.complete(messages=messages, max_tokens=max_tokens, temperature=temperature, **extra)
    return LLMCompletion(text=text or "")


def run_goal_loop(
    llm: Any,
    *,
    snapshot: GoalSnapshot,
    model: str = "",
    max_turns: Optional[int] = None,
    thinking_budget: Optional[int] = None,
) -> GoalLoopResult:
    """Run the tool loop (or one-shot JSON if the model does not call tools)."""
    session = _Session(snapshot)
    tools = tools_for_phase(snapshot.phase, kind=snapshot.kind)
    turns = max_turns if max_turns is not None else max_turns_from_env()
    extra: Dict[str, Any] = {}
    if thinking_budget is not None and str(model or "").lower().startswith("gemini"):
        extra["thinking_budget"] = thinking_budget

    user = (
        f"Phase: {snapshot.phase}. Kind: {snapshot.kind}. "
        f"Goal {snapshot.key}: {snapshot.title or '(untitled)'}. "
        f"Brand: {snapshot.brand or 'unknown'}. "
        "Inspect with tools, then propose, then call done."
    )
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": LOOP_SYSTEM},
        {"role": "user", "content": user},
    ]
    trace: List[str] = []
    used_tools = False
    used_llm = False

    for turn in range(1, turns + 1):
        try:
            completion = _invoke(
                llm,
                messages,
                tools=tools,
                max_tokens=4096,
                temperature=0.2,
                extra=extra,
            )
        except Exception:
            logger.warning("Goal loop LLM failed on turn %s", turn, exc_info=True)
            break
        used_llm = True
        calls: Sequence[ToolCall] = completion.tool_calls or ()
        if not calls:
            if completion.text and _apply_oneshot(session, completion.text):
                trace.append(f"turn {turn}: oneshot-json")
            break
        used_tools = True
        messages.append(_assistant_message(completion))
        for call in calls:
            payload = _dispatch(session, call.name, call.arguments if isinstance(call.arguments, dict) else {})
            trace.append(call.name)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(_json_ready(payload), ensure_ascii=False),
                }
            )
        if session.done:
            break
    else:
        session.done = True
        trace.append("max-turns")

    return session.result(used_tools=used_tools, used_llm=used_llm, turns=min(turn, turns), trace=trace)


def snapshot_from_okr(
    ticket: Dict[str, Any],
    *,
    phase: str,
    evidence: Optional[Dict[str, str]] = None,
    open_work: Optional[Sequence[Dict[str, Any]]] = None,
    scoreboard: Optional[Dict[str, Any]] = None,
    key_results: Optional[Sequence[Dict[str, Any]]] = None,
) -> GoalSnapshot:
    krs = normalize_key_results(key_results if key_results is not None else ticket.get("key_results"))
    pack = dict(evidence or {})
    return GoalSnapshot(
        kind=KIND_OBJECTIVE,
        phase=phase,
        key=str(ticket.get("key") or ""),
        title=str(ticket.get("title") or ""),
        description=str(ticket.get("description") or ticket.get("brief") or ""),
        brand=str(pack.get("brand") or ""),
        cycle=str(ticket.get("okr_cycle") or pack.get("cycle_label") or ""),
        key_results=krs,
        evidence=pack,
        scoreboard=dict(scoreboard or {}),
        open_work=[dict(item) for item in (open_work or [])],
    )


def snapshot_from_epic(
    *,
    phase: str,
    key: str,
    title: str,
    description: str = "",
    evidence: Optional[Dict[str, str]] = None,
    open_work: Optional[Sequence[Dict[str, Any]]] = None,
    scoreboard: Optional[Dict[str, Any]] = None,
) -> GoalSnapshot:
    return GoalSnapshot(
        kind=KIND_EPIC,
        phase=phase,
        key=key,
        title=title,
        description=description,
        evidence=dict(evidence or {}),
        scoreboard=dict(scoreboard or {}),
        open_work=[dict(item) for item in (open_work or [])],
    )
