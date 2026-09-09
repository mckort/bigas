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

from bigas.agents.proactive_prompts import (
    IN_PROGRESS_EPIC_SYSTEM_PROMPT,
    PLAN_EPIC_SYSTEM_PROMPT,
    RESEARCH_EPIC_SYSTEM_PROMPT,
)
from bigas.llm.completion import LLMCompletion, ToolCall
from bigas.okr.context import format_evidence_pack
from bigas.okr.model import normalize_key_results
from bigas.okr.plan import MAX_TASKS_TOTAL, OKR_PLAN_SYSTEM, _normalize_plan_tasks, is_mechanical_okr_task
from bigas.okr.research import OKR_RESEARCH_SYSTEM, _extract_json_object, _merge_key_results

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
- Title shape: Increase <parameter> from <baseline> to <target> (or Decrease).
- Set source to ga4|stripe|ads|github|jira|manual when you know where the number is.
- Tasks are concrete actions that would move a KR (or an Epic), scoped to one
  person or AI agent. Prefer fewer, sharper tickets (1–3 per KR, at most 10).
- ai_doable=true when an AI agent can do the first pass: landing page, site copy,
  tracking snippet, small UI change, draft outreach. Human-only work (partnerships,
  pricing calls, budget, legal) is ai_doable=false.
- Off-track KRs get proposed To Do work. Never auto-start or auto-advance cards.
- If a number is missing from evidence, mark the KR measurable=false.
- Research/plan that only reads is a failure. You must call propose_key_results
  or propose_tasks (empty list + set_notes reason is ok). Do not keep leftover
  SaaS-kit Key Results such as “weekly active founders”.
"""

LOOP_PHASE_PREFACE = (
    "The phase rules below still apply. Use the tools — do not dump a JSON object."
)

_JSON_DUMP_MARKERS = (
    "Return JSON only",
    "Output ONLY valid JSON",
    "JSON shape:",
    "JSON schema:",
)

NUDGE_WRITE = (
    "You looked but proposed nothing. Call propose_key_results or propose_tasks now. "
    "If the honest answer is an empty list, set_notes with the reason and call the "
    "propose tool with []. Do not keep leftover SaaS-kit Key Results."
)

_KR_TITLE_RE = re.compile(
    r"^(Increase|Decrease|Öka|Minska)\s+.+\s+(from|från)\s+.+\s+(to|till)\s+.+$",
    re.I,
)
_KNOWN_SOURCES = frozenset({"ga4", "stripe", "ads", "github", "jira", "manual", "unknown"})


def _normalize_kr_title(title: str) -> str:
    title = title.strip().strip("\"'")
    return re.sub(r"\s+", " ", title)


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
        "title": {
            "type": "string",
            "description": "Increase <parameter> from <baseline> to <target> (or Decrease).",
        },
        "metric": {"type": "string"},
        "unit": {"type": "string"},
        "baseline": {"type": "number"},
        "target": {"type": "number"},
        "current": {"type": "number"},
        "source": {
            "type": "string",
            "description": "ga4|stripe|ads|github|jira|manual|unknown. Use ga4 when the number is in the GA4 evidence.",
        },
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
        "ai_doable": {
            "type": "boolean",
            "description": (
                "true if an AI agent can do the first pass in the mapped repo or with "
                "existing tools: landing page, first-pass site copy, tracking snippet, "
                "small UI/copy change, draft outreach. false for partnerships, pricing "
                "calls, budget, legal, in-person sales."
            ),
        },
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
        "Replace proposed Key Results. Committed KRs stay. Research phase only. "
        "Empty list is allowed only with reason (or after set_notes).",
        {
            "key_results": {"type": "array", "items": KR_ITEM_SCHEMA},
            "reason": {"type": "string", "description": "Required when key_results is empty."},
        },
        ["key_results"],
    ),
    _fn(
        "propose_tasks",
        "Propose To Do tasks. Not KR titles. Not analytics wiring. "
        "Empty list is allowed only with reason (or after set_notes).",
        {
            "tasks": {"type": "array", "items": TASK_ITEM_SCHEMA},
            "reason": {"type": "string", "description": "Required when tasks is empty."},
        },
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
    wrote: bool = False
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
        self.proposed_krs = False
        self.proposed_tasks = False
        self.nudged = False

    def required_write(self) -> Optional[str]:
        snap = self.snapshot
        if snap.kind == KIND_OBJECTIVE and snap.phase == PHASE_RESEARCH:
            return "krs"
        if snap.phase == PHASE_PLAN:
            return "tasks"
        if snap.kind == KIND_EPIC and snap.phase == PHASE_RESEARCH:
            return "tasks"
        return None

    def write_ok(self) -> bool:
        need = self.required_write()
        if need == "krs":
            return self.proposed_krs
        if need == "tasks":
            return self.proposed_tasks or bool(self.tasks)
        return True

    def result(self, *, used_tools: bool, used_llm: bool, turns: int, trace: List[str]) -> GoalLoopResult:
        wrote = self.write_ok()
        if not wrote and self.required_write() == "krs":
            self.proposed = []
        if self.required_write() and not wrote:
            used_llm = False
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
            wrote=wrote,
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


def _session_task_titles(session: _Session) -> set[str]:
    titles: set[str] = set()
    for item in session.tasks:
        text = (item.get("title") or item.get("summary") or "").strip().lower()
        if text:
            titles.add(text)
    return titles


def _parse_tool_arguments(arguments: Any) -> Dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_call_arguments_json(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments or {})


def _infer_kr_source(kr: Dict[str, Any], snapshot: GoalSnapshot) -> str:
    raw = str(kr.get("source") or "").strip().lower()
    if raw in _KNOWN_SOURCES and raw != "unknown":
        return raw
    tokens: List[str] = []
    for key in ("baseline", "current"):
        val = kr.get(key)
        if val in (None, ""):
            continue
        token = str(val).replace(",", "")
        tokens.append(token)
        if _NUMBER_RE.fullmatch(token):
            try:
                tokens.append(str(int(float(token))))
            except ValueError:
                pass
    for ev_key, ev_val in snapshot.evidence.items():
        blob = str(ev_val).replace(",", "")
        if not any(token and token in blob for token in tokens):
            continue
        key = ev_key.lower()
        if "ga4" in key or "analytics" in key:
            return "ga4"
        if "stripe" in key:
            return "stripe"
        if "ad" in key:
            return "ads"
        if "git" in key:
            return "github"
        if "jira" in key or "board" in key:
            return "jira"
    return raw if raw in _KNOWN_SOURCES else "unknown"


def _ground_key_results(raw: Any, *, snapshot: GoalSnapshot) -> tuple[List[Dict[str, Any]], List[str]]:
    numbers = evidence_numbers(snapshot.evidence, extra=snapshot.title + " " + snapshot.description)
    grounded: List[Dict[str, Any]] = []
    rejected_titles: List[str] = []
    for kr in normalize_key_results(raw if isinstance(raw, list) else []):
        title = _normalize_kr_title(str(kr.get("title") or ""))
        if kr.get("measurable") and not _KR_TITLE_RE.match(title):
            rejected_titles.append(title or "(untitled)")
            continue
        if title:
            kr["title"] = title
        kr["source"] = _infer_kr_source(kr, snapshot)
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
    return grounded, rejected_titles


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
        raw_krs = arguments.get("key_results")
        reason = str(arguments.get("reason") or "").strip()
        if isinstance(raw_krs, list) and not raw_krs:
            if reason or session.briefing or session.notes_markdown:
                session.proposed = []
                session.proposed_krs = True
                return {"ok": True, "accepted": 0}
            return {
                "ok": False,
                "error": "Empty key_results needs a reason (or set_notes first).",
            }
        grounded, rejected_titles = _ground_key_results(raw_krs, snapshot=snap)
        for title in rejected_titles:
            session.rejected.append(
                f"propose_key_results: title must be Increase/Decrease X from A to B ({title})"
            )
        if not grounded:
            session.rejected.append("propose_key_results: empty after grounding")
            return {
                "ok": False,
                "error": "No usable Key Results. Titles must be Increase/Decrease X from A to B.",
            }
        session.proposed = grounded
        session.proposed_krs = True
        return {"ok": True, "accepted": len(grounded), "rejected_titles": rejected_titles}
    if name == "propose_tasks":
        raw = arguments.get("tasks") if isinstance(arguments.get("tasks"), list) else []
        reason = str(arguments.get("reason") or "").strip()
        if isinstance(arguments.get("tasks"), list) and not raw:
            if reason or session.briefing or session.notes_markdown:
                session.proposed_tasks = True
                return {"ok": True, "accepted": 0}
            return {"ok": False, "error": "Empty tasks needs a reason (or set_notes first)."}
        if snap.kind == KIND_OBJECTIVE:
            krs = _merge_key_results(committed=session.committed, proposed=session.proposed) or snap.key_results
            accepted = _normalize_plan_tasks(
                raw,
                key_results=krs,
                existing_titles=_open_work_titles(snap) | _session_task_titles(session),
            )
        else:
            accepted = _normalize_epic_tasks(
                raw,
                existing_titles=_open_work_titles(snap) | _session_task_titles(session),
            )
        dropped = max(0, len(raw) - len(accepted))
        remaining_slots = MAX_TASKS_TOTAL - len(session.tasks)
        if remaining_slots <= 0:
            session.proposed_tasks = True
            session.rejected.append(
                f"propose_tasks: session already at {MAX_TASKS_TOTAL} tasks; dropped {len(accepted)}"
            )
            return {"ok": True, "accepted": 0, "dropped": dropped + len(accepted)}
        if len(accepted) > remaining_slots:
            overflow = len(accepted) - remaining_slots
            accepted = accepted[:remaining_slots]
            session.rejected.append(
                f"propose_tasks: capped session at {MAX_TASKS_TOTAL} total ({overflow} dropped)"
            )
        session.tasks.extend(accepted)
        session.proposed_tasks = True
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
        if not session.write_ok():
            if session.nudged:
                session.rejected.append("done without propose_*")
            session.nudged = True
            return {"ok": False, "error": NUDGE_WRITE}
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
        grounded, _rejected = _ground_key_results(parsed.get("key_results"), snapshot=session.snapshot)
        session.proposed = grounded
        session.proposed_krs = True
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
                "function": {
                    "name": call.name,
                    "arguments": _tool_call_arguments_json(call.arguments),
                },
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


def _phase_rules_for_loop(text: str) -> str:
    """Drop one-shot JSON instructions so Gemini does not stop talking instead of calling tools."""
    cut = len(text)
    for marker in _JSON_DUMP_MARKERS:
        idx = text.lower().find(marker.lower())
        if idx >= 0:
            cut = min(cut, idx)
    return text[:cut].strip()


def system_prompt_for(snapshot: GoalSnapshot) -> str:
    if snapshot.kind == KIND_OBJECTIVE:
        phase_rules = OKR_RESEARCH_SYSTEM if snapshot.phase == PHASE_RESEARCH else OKR_PLAN_SYSTEM
    elif snapshot.phase == PHASE_RESEARCH:
        phase_rules = RESEARCH_EPIC_SYSTEM_PROMPT
    elif snapshot.phase == PHASE_PLAN:
        phase_rules = PLAN_EPIC_SYSTEM_PROMPT
    else:
        phase_rules = IN_PROGRESS_EPIC_SYSTEM_PROMPT
    return f"{LOOP_SYSTEM}\n\n{LOOP_PHASE_PREFACE}\n\n{_phase_rules_for_loop(phase_rules)}"


def _forced_write_tool(session: _Session) -> Optional[str]:
    need = session.required_write()
    if need == "krs":
        return "propose_key_results"
    if need == "tasks":
        return "propose_tasks"
    return None


def _tool_choice_for_turn(session: _Session, *, used_tools: bool, turn: int) -> Optional[Dict[str, Any]]:
    name = _forced_write_tool(session)
    if not name or session.write_ok():
        return None
    if used_tools or turn >= 2:
        return {"type": "function", "function": {"name": name}}
    return None


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
        "Inspect with tools, then propose, then call done. "
        "Stopping after get_* without propose_* is a failure."
    )
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt_for(snapshot)},
        {"role": "user", "content": user},
    ]
    trace: List[str] = []
    used_tools = False
    used_llm = False
    executed_turns = 0

    for turn in range(1, turns + 1):
        executed_turns = turn
        turn_extra = dict(extra)
        choice = _tool_choice_for_turn(session, used_tools=used_tools, turn=turn)
        if choice:
            turn_extra["tool_choice"] = choice
        try:
            completion = _invoke(
                llm,
                messages,
                tools=tools,
                max_tokens=4096,
                temperature=0.2,
                extra=turn_extra,
            )
        except Exception:
            logger.warning("Goal loop LLM failed on turn %s", turn, exc_info=True)
            if (
                session.required_write()
                and not session.write_ok()
                and not session.nudged
                and turn < turns
            ):
                session.nudged = True
                messages.append({"role": "user", "content": NUDGE_WRITE})
                trace.append("nudge-error")
                continue
            break
        used_llm = True
        calls: Sequence[ToolCall] = completion.tool_calls or ()
        if not calls:
            if completion.text and _apply_oneshot(session, completion.text):
                trace.append(f"turn {turn}: oneshot-json")
                if session.write_ok():
                    break
            if (
                session.required_write()
                and not session.write_ok()
                and not session.nudged
                and turn < turns
            ):
                session.nudged = True
                messages.append(
                    {"role": "assistant", "content": completion.text.strip() or "(no response)"}
                )
                messages.append({"role": "user", "content": NUDGE_WRITE})
                trace.append("nudge-silence")
                continue
            break
        used_tools = True
        messages.append(_assistant_message(completion))
        for call in calls:
            payload = _dispatch(session, call.name, _parse_tool_arguments(call.arguments))
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

    return session.result(
        used_tools=used_tools,
        used_llm=used_llm,
        turns=min(executed_turns, turns),
        trace=trace,
    )


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
