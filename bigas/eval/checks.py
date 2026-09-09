"""Mechanical eval checks — objective deductions on top of LLM judges."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from bigas.eval.base import EvalFixture
from bigas.eval.readable import iter_step_outputs

DEFAULT_REQUIRED_STEPS = ("classify", "primary", "landscape", "moats", "thesis")
_MONEY_RE = re.compile(
    r"(?:\$|€|£)\s?\d[\d,]*(?:\.\d+)?(?:\s*(?:million|billion|m|bn|k))?",
    re.I,
)
_PERCENT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")
_NAME_KEY_RE = re.compile(r"^[A-Z][\w.&+\- ]{1,60}$")


@dataclass
class MechanicalCheck:
    penalty: float = 0.0
    notes: List[str] = field(default_factory=list)
    missing_steps: List[str] = field(default_factory=list)
    unsupported_names: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "penalty": self.penalty,
            "notes": list(self.notes),
            "missing_steps": list(self.missing_steps),
            "unsupported_names": list(self.unsupported_names),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MechanicalCheck":
        return cls(
            penalty=float(raw.get("penalty") or 0),
            notes=[str(item) for item in (raw.get("notes") or [])],
            missing_steps=[str(item) for item in (raw.get("missing_steps") or [])],
            unsupported_names=[str(item) for item in (raw.get("unsupported_names") or [])],
        )


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _source_corpus(output: Mapping[str, Any], fixture: EvalFixture) -> str:
    sources = output.get("sources") if isinstance(output.get("sources"), Mapping) else {}
    page = str((sources or {}).get("page") or fixture.input_text or "")
    snippets = str((sources or {}).get("snippets") or "")
    extra = " ".join(str(item) for item in (fixture.extra_urls or ()))
    return f"{fixture.company_name} {fixture.website_url} {page} {snippets} {extra}"


def _step_map(output: Mapping[str, Any]) -> Dict[str, str]:
    return {key: text for key, text in iter_step_outputs(output)}


def _parse_jsonish(text: str) -> Any:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _landscape_names(text: str) -> List[str]:
    parsed = _parse_jsonish(text)
    names: List[str] = []
    if isinstance(parsed, Mapping):
        for key in ("overlapping", "adHoc", "complementary"):
            rows = parsed.get(key) or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, Mapping):
                    name = str(row.get("name") or "").strip()
                    if name:
                        names.append(name)
    return names


def _looks_like_company_name(name: str) -> bool:
    cleaned = (name or "").strip()
    if len(cleaned) < 2 or cleaned.lower() in {"n/a", "none", "unknown", "—", "-"}:
        return False
    return bool(_NAME_KEY_RE.match(cleaned))


def run_mechanical_checks(
    output: Mapping[str, Any],
    fixture: EvalFixture,
    *,
    required_steps: Sequence[str] = DEFAULT_REQUIRED_STEPS,
) -> MechanicalCheck:
    check = MechanicalCheck()
    steps = _step_map(output)
    corpus = _norm(_source_corpus(output, fixture))
    body = "\n".join(steps.values())

    for step_id in required_steps:
        text = (steps.get(step_id) or "").strip()
        if not text:
            check.missing_steps.append(step_id)
            check.notes.append(f"Missing step `{step_id}`.")
    if check.missing_steps:
        check.penalty += min(24.0, 8.0 * len(check.missing_steps))

    landscape_text = steps.get("landscape") or ""
    for name in _landscape_names(landscape_text):
        if not _looks_like_company_name(name):
            continue
        if _norm(name) not in corpus:
            check.unsupported_names.append(name)
    if check.unsupported_names:
        extra = min(15.0, 3.0 * len(check.unsupported_names))
        check.penalty += extra
        shown = ", ".join(check.unsupported_names[:6])
        check.notes.append(f"Landscape names not in sources: {shown}.")

    invented = []
    for match in list(_MONEY_RE.findall(body)) + list(_PERCENT_RE.findall(body)):
        token = _norm(str(match))
        if token and token not in corpus:
            invented.append(str(match).strip())
    invented = list(dict.fromkeys(invented))
    if invented:
        check.penalty += min(20.0, 5.0 * len(invented))
        check.notes.append("Figures not found in fixture page or research snippets: " + ", ".join(invented[:6]))

    if str(output.get("pack_id") or "") == "okr-goal-loop":
        _apply_okr_loop_checks(check, output, fixture)
    check.penalty = min(40.0, round(check.penalty, 1))
    return check


_WIRE_RE = re.compile(r"^(wire weekly snapshot for\b|instrument:\s*)", re.I)
_SAAS_KIT_RE = re.compile(
    r"weekly active founders|7-day activation|\bnps\b|\bactive users\b",
    re.I,
)


def _baseline_in_corpus(baseline: Any, corpus: str) -> bool:
    baseline_str = str(baseline)
    if _norm(baseline_str) in corpus or baseline_str in corpus:
        return True
    try:
        num = float(baseline)
    except (TypeError, ValueError):
        return False
    if num == int(num):
        int_token = str(int(num))
        if _norm(int_token) in corpus or int_token in corpus:
            return True
    float_token = str(num)
    return _norm(float_token) in corpus or float_token in corpus


def _okr_step_json(output: Mapping[str, Any], step_id: str) -> Dict[str, Any]:
    steps = output.get("steps") if isinstance(output.get("steps"), Mapping) else {}
    raw = steps.get(step_id) if isinstance(steps, Mapping) else None
    if isinstance(raw, Mapping):
        return dict(raw)
    parsed = _parse_jsonish(str(raw or ""))
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _apply_okr_loop_checks(
    check: MechanicalCheck,
    output: Mapping[str, Any],
    fixture: EvalFixture,
) -> None:
    research = _okr_step_json(output, "research")
    plan = _okr_step_json(output, "plan")
    followup = _okr_step_json(output, "followup")
    krs = research.get("key_results") if isinstance(research.get("key_results"), list) else []
    if not krs:
        check.notes.append("Research produced no Key Results.")
        check.penalty += 8.0
    elif not (2 <= len(krs) <= 4):
        check.notes.append(f"Research should propose 2–4 KRs, got {len(krs)}.")
        check.penalty += 4.0

    corpus = _norm(_source_corpus(output, fixture) + " " + str(output.get("evidence") or ""))
    for kr in krs:
        if not isinstance(kr, Mapping):
            continue
        title = str(kr.get("title") or "")
        if _SAAS_KIT_RE.search(title):
            check.notes.append(f"SaaS-kit KR: {title}")
            check.penalty += 6.0
        if kr.get("measurable") and kr.get("baseline") is not None:
            baseline = kr.get("baseline")
            if not _baseline_in_corpus(baseline, corpus):
                check.notes.append(f"Measurable KR baseline {baseline} not in evidence.")
                check.penalty += 4.0

    plan_tasks = plan.get("tasks") if isinstance(plan.get("tasks"), list) else []
    kr_titles = {
        str(kr.get("title") or "").strip().lower()
        for kr in krs
        if isinstance(kr, Mapping) and str(kr.get("title") or "").strip()
    }
    for task in plan_tasks:
        if not isinstance(task, Mapping):
            continue
        title = str(task.get("title") or task.get("summary") or "").strip()
        if not title:
            continue
        if title.lower() in kr_titles or _WIRE_RE.match(title):
            check.notes.append(f"Plan opened a KR clone or wiring ticket: {title}")
            check.penalty += 6.0
        if title.lower() == "update catalog":
            check.notes.append("Plan duplicated open work `Update catalog`.")
            check.penalty += 6.0

    follow_tasks = followup.get("tasks") if isinstance(followup.get("tasks"), list) else []
    if len(follow_tasks) > 10:
        check.notes.append(f"Follow-up opened {len(follow_tasks)} tasks (max 10).")
        check.penalty += 5.0

    research_trace = research.get("tool_trace")
    kept_founders = any(
        isinstance(kr, Mapping) and "weekly active founders" in str(kr.get("title") or "").lower()
        for kr in krs
    )
    if isinstance(research_trace, list):
        if "propose_key_results" not in research_trace:
            check.notes.append("Research used no write tool (propose_key_results).")
            check.penalty += 10.0
            if kept_founders:
                check.notes.append("Kept weekly active founders because research never proposed KRs.")
                check.penalty += 8.0
    plan_trace = plan.get("tool_trace")
    if isinstance(plan_trace, list) and "propose_tasks" not in plan_trace:
        check.notes.append("Plan used no write tool (propose_tasks).")
        check.penalty += 10.0
