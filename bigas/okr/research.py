"""LLM Key Result proposals grounded in live brand evidence."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bigas.llm.factory import get_llm_client
from bigas.okr.context import format_evidence_pack, gather_okr_evidence
from bigas.okr.model import normalize_key_results

logger = logging.getLogger(__name__)

DEFAULT_THINKING_BUDGET = 8_192
MAX_KRS = 4

OKR_RESEARCH_SYSTEM = """You are a Chief of Staff. A thinking model must analyze the evidence pack
and propose Key Results. Do not use canned metrics, template scorecards, or
numbers that are not in the evidence.

The Objective is the goal. Key Results are the 2–4 measurable *improvements*
that most increase the chance of hitting that goal in the timebox. They are
not new goals and not a restatement of the Objective.

How to work:
1. Read the brand, timebox, Objective, GA4, website, repo, and board tickets.
2. Infer the actual funnel / growth model for THIS brand and THIS Objective.
3. Rank parameters by how much moving them would help the Objective.
4. Keep only the top 2–4. Drop vanity metrics that do not causally matter here.

Title shape (required), in the same language as the Objective:
  Increase <parameter> from <baseline> to <target>
  Decrease <parameter> from <baseline> to <target>
Put the same numbers in baseline, target, and current.

Numbers:
- baseline/current = a figure from the evidence pack (cite the source in ai_note).
- target = a stretch you justify from that baseline and the remaining days.
- If the evidence has no number for a parameter you still believe is critical,
  measurable=false, do not invent the baseline, fill measurement_gap.
- Never copy numbers from another brand or from memory of SaaS OKRs.

What a KR is not:
- A sibling Objective (a new volume target of a different outcome).
- A generic founder/SaaS kit (active users, activation rate, NPS, case studies)
  unless the evidence shows this brand and Objective actually run on those.
- Metrics from a different project than the one in the evidence pack.

Tasks are not created in this step. After human approval, Design and plan
reads live status (GA4 and other sources) and opens concrete work items
toward each KR — not a ticket named after the KR, and not a wiring ticket.

Return JSON only — no markdown fences, no preamble.

JSON shape:
{
  "briefing": "2-4 sentences for the human reviewing Description approval.",
  "research_markdown": "Markdown the human will read: brand, timebox, evidence used, why these KRs, gaps.",
  "key_results": [
    {
      "title": "Increase {parameter} from {baseline} to {target}",
      "metric": "short metric name",
      "unit": "customers|orders|SEK|%|...",
      "baseline": 0,
      "target": 0,
      "current": 0,
      "source": "ga4|stripe|ads|github|jira|manual|unknown",
      "measurable": true,
      "measurement_gap": "",
      "direction": "increase|decrease|maintain",
      "ai_note": "causal link to the Objective + evidence cited + why the target is challenging in this timebox"
    }
  ]
}
"""


@dataclass
class OkrResearchResult:
    key_results: List[Dict[str, Any]]
    research_markdown: str
    briefing: str
    model: str = ""
    used_llm: bool = False
    evidence: Dict[str, str] = field(default_factory=dict)


def _thinking_budget() -> Optional[int]:
    raw = (os.environ.get("BIGAS_OKR_RESEARCH_THINKING_BUDGET") or "").strip()
    if raw.lower() in {"0", "none", "off", "false"}:
        return None
    if raw.lower() in {"", "default"}:
        return DEFAULT_THINKING_BUDGET
    try:
        value = int(raw)
        return value if value > 0 else None
    except ValueError:
        return DEFAULT_THINKING_BUDGET


def fallback_key_results(ticket: Dict[str, Any], evidence: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Honest gap KR — never a SaaS template with made-up numbers."""
    title = (ticket.get("title") or "this objective").strip()
    brand = (evidence or {}).get("brand") or "this brand"
    return normalize_key_results(
        [
            {
                "title": f"Define the outcome metric that proves “{title[:80]}”",
                "metric": "Primary outcome",
                "unit": "",
                "baseline": 0,
                "target": 1,
                "current": 0,
                "source": "unknown",
                "measurable": False,
                "measurement_gap": (
                    f"Could not propose grounded Key Results for {brand} from live sources. "
                    "Do not treat placeholder SaaS metrics as real. Add a baseline from GA4, "
                    "Stripe, or sales records, then re-run Research."
                ),
                "status": "proposed",
                "ai_note": "Research fallback — no invented targets.",
            }
        ]
    )


def _first_balanced_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in LLM response")
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    raise ValueError("no JSON object in LLM response")


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty LLM response")
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    parsed = json.loads(_first_balanced_json_object(raw))
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON was not an object")
    return parsed


def _split_existing(ticket: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    existing = normalize_key_results(ticket.get("key_results"))
    committed = [kr for kr in existing if str(kr.get("status") or "").lower() == "committed"]
    proposed = [kr for kr in existing if str(kr.get("status") or "").lower() != "committed"]
    return committed, proposed


def _merge_key_results(
    *,
    committed: List[Dict[str, Any]],
    proposed: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged = list(committed)
    seen_titles = {(kr.get("title") or "").strip().lower() for kr in merged}
    for kr in proposed:
        title = (kr.get("title") or "").strip().lower()
        if title and title in seen_titles:
            continue
        item = dict(kr)
        item["status"] = "proposed"
        merged.append(item)
        if title:
            seen_titles.add(title)
        if len(merged) >= MAX_KRS + len(committed):
            break
    return normalize_key_results(merged[: MAX_KRS + len(committed)])


def _build_user_prompt(
    ticket: Dict[str, Any],
    evidence: Dict[str, str],
    *,
    committed: List[Dict[str, Any]],
    proposed: List[Dict[str, Any]],
) -> str:
    pack = format_evidence_pack(evidence)
    committed_txt = json.dumps(committed, ensure_ascii=False, indent=2) if committed else "[]"
    proposed_txt = json.dumps(proposed, ensure_ascii=False, indent=2) if proposed else "[]"
    return f"""Analyze the evidence pack. Propose 2–4 Key Results for this Objective.

Each title must be one parameter moving from a baseline to a target, using numbers
from the evidence (or measurable=false if the number is missing). Choose the
parameters that most help THIS Objective for THIS brand — do not reuse a metric
kit. Do not add sibling goals or restate the Objective.

Keep committed KRs as-is. Replace proposed KRs that are generic, invented,
wrong-brand, sibling goals, or missing a from-baseline-to-target title.

Committed KRs (keep):
{committed_txt}

Current proposed KRs (replace if ungrounded):
{proposed_txt}

Evidence pack:
{pack}
"""


def run_okr_research(
    ticket: Dict[str, Any],
    *,
    evidence: Optional[Dict[str, str]] = None,
    llm: Any = None,
    model: Optional[str] = None,
) -> OkrResearchResult:
    pack = evidence if evidence is not None else gather_okr_evidence(ticket)
    committed, proposed = _split_existing(ticket)
    briefing_fallback = (
        f"Could not finish OKR research for {(pack.get('brand') or 'this brand')}. "
        "Left a measurement-gap KR instead of inventing targets. Re-run Research after sources work."
    )
    research_fallback = (
        "### Research failed\n\n"
        "Live sources or the model did not return grounded Key Results. "
        "Do not use SaaS template KRs. Fix analytics/site access and re-run.\n\n"
        + format_evidence_pack(pack)
    )

    try:
        client = llm
        model_name = model or ""
        if client is None:
            client, model_name = get_llm_client(feature="okr_research")
        messages = [
            {"role": "system", "content": OKR_RESEARCH_SYSTEM},
            {
                "role": "user",
                "content": _build_user_prompt(
                    ticket, pack, committed=committed, proposed=proposed
                ),
            },
        ]
        call_kwargs: Dict[str, Any] = {
            "max_tokens": 12_288,
            "temperature": 0.2,
        }
        if str(model_name or "").lower().startswith("gemini"):
            budget = _thinking_budget()
            if budget is not None:
                call_kwargs["thinking_budget"] = budget
        raw = client.complete(messages=messages, **call_kwargs)
        parsed = _extract_json_object(raw)
        llm_krs = parsed.get("key_results") if isinstance(parsed.get("key_results"), list) else []
        merged = _merge_key_results(
            committed=committed,
            proposed=normalize_key_results(llm_krs),
        )
        if not merged:
            raise ValueError("LLM returned no usable key results")
        research_md = str(parsed.get("research_markdown") or "").strip() or format_evidence_pack(pack)
        briefing = str(parsed.get("briefing") or "").strip() or (
            f"Proposed {len(merged)} Key Results for {pack.get('brand')} grounded in live sources. "
            "Review, edit, then drag to Design and plan."
        )
        return OkrResearchResult(
            key_results=merged,
            research_markdown=research_md,
            briefing=briefing,
            model=model_name,
            used_llm=True,
            evidence=pack,
        )
    except Exception as exc:
        logger.warning("OKR research LLM failed: %s", exc, exc_info=True)
        return OkrResearchResult(
            key_results=_merge_key_results(
                committed=committed,
                proposed=fallback_key_results(ticket, pack),
            ),
            research_markdown=research_fallback,
            briefing=briefing_fallback,
            model=model or "",
            used_llm=False,
            evidence=pack,
        )
