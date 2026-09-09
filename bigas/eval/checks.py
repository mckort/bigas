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

    check.penalty = min(40.0, round(check.penalty, 1))
    return check
