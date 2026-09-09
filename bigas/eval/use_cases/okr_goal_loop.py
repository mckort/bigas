"""Bigas OKR goal-loop eval — frozen fixture, same tools as production."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from bigas.eval.base import (
    BaseUseCaseEvaluator,
    EvalFixture,
    EvalUsage,
    register_use_case,
    register_use_case_alias,
    reject_customer_identifiers,
)
from bigas.eval.complete import EvalChatClient
from bigas.eval.pack import EvalPack, load_pack
from bigas.okr.loop import run_goal_loop, snapshot_from_okr
from bigas.okr.model import normalize_key_results

PACK_ID = "okr-goal-loop"


class _UsageTrackingClient:
    def __init__(self, model_id: str, usage: EvalUsage):
        self._inner = EvalChatClient(model_id)
        self.usage = usage

    def complete_detailed(self, messages, **kwargs):
        completion = self._inner.complete_detailed(messages, **kwargs)
        raw = getattr(completion, "usage", None)
        prompt = int(getattr(raw, "prompt_tokens", None) or 0)
        output = int(getattr(raw, "candidates_tokens", None) or 0)
        total = int(getattr(raw, "total_tokens", None) or 0)
        self.usage.prompt_tokens += prompt
        self.usage.output_tokens += output
        self.usage.total_tokens += total or (prompt + output)
        return completion


def _parse_fixture_payload(fixture: EvalFixture, pack: EvalPack) -> Dict[str, Any]:
    reject_customer_identifiers(fixture.to_dict())
    raw = (fixture.input_text or "").strip()
    if not raw:
        raw = str((pack.fixture or {}).get("input") or "").strip()
    if not raw:
        raise ValueError("okr-goal-loop fixture is missing input JSON")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("okr-goal-loop fixture input must be a JSON object")
    reject_customer_identifiers(data)
    return data


def _step_payload(result: Any) -> Dict[str, Any]:
    return {
        "key_results": list(result.key_results),
        "tasks": list(result.tasks),
        "current_updates": list(result.current_updates),
        "briefing": result.briefing,
        "notes_markdown": result.notes_markdown,
        "used_tools": result.used_tools,
        "tool_trace": list(result.tool_trace),
        "rejected": list(result.rejected),
    }


class OkrGoalLoopEvaluator(BaseUseCaseEvaluator):
    use_case_id = "okr-goal-loop"
    display_name = "Bigas OKR goal loop"
    pack_id = PACK_ID
    _pack: Optional[EvalPack] = None

    def __init__(self, pack: Optional[EvalPack] = None):
        self._pack = pack

    @property
    def pack(self) -> EvalPack:
        if self._pack is None:
            self._pack = load_pack(self.pack_id)
        return self._pack

    def default_fixture(self) -> EvalFixture:
        return self.default_fixtures()[0]

    def default_fixtures(self) -> List[EvalFixture]:
        raws = list(self.pack.fixtures or [])
        if not raws and self.pack.fixture:
            raws = [self.pack.fixture]
        return [EvalFixture.from_dict(dict(item)) for item in raws] or [
            EvalFixture("Green Promo Wear", "https://greenpromowear.com")
        ]

    def get_judge_rubric(self) -> str:
        extra = self.pack.rubric or ""
        return (
            "Score the model output on a 0-100 scale.\n"
            "This evaluates the Bigas OKR tool loop on a frozen public fixture "
            "(not a live board or GA4 property).\n\n"
            f"{extra}"
        )

    def run(self, fixture: EvalFixture, model_id: str) -> Tuple[Dict[str, Any], EvalUsage]:
        payload = _parse_fixture_payload(fixture, self.pack)
        usage = EvalUsage()
        llm = _UsageTrackingClient(model_id, usage)
        evidence = dict(payload.get("evidence") or {})
        open_work = list(payload.get("open_work") or [])
        starting_krs = normalize_key_results(payload.get("key_results"))

        research = run_goal_loop(
            llm,
            snapshot=snapshot_from_okr(
                {
                    "key": payload.get("key") or "GPWW-15",
                    "title": payload.get("title") or fixture.company_name,
                    "description": payload.get("description") or "",
                    "okr_cycle": payload.get("cycle") or "",
                    "key_results": starting_krs,
                },
                phase="research",
                evidence=evidence,
                open_work=open_work,
            ),
            model=model_id,
        )
        plan = run_goal_loop(
            llm,
            snapshot=snapshot_from_okr(
                {
                    "key": payload.get("key") or "GPWW-15",
                    "title": payload.get("title") or fixture.company_name,
                    "description": payload.get("description") or "",
                    "key_results": [
                        {**kr, "status": "committed"} for kr in (research.key_results or starting_krs)
                    ],
                },
                phase="plan",
                evidence=evidence,
                open_work=open_work,
                scoreboard=dict(payload.get("scoreboard") or {}),
            ),
            model=model_id,
        )
        follow_open = list(open_work) + [
            {"title": item.get("title"), "summary": item.get("title"), "status": "To Do"}
            for item in plan.tasks
        ]
        followup = run_goal_loop(
            llm,
            snapshot=snapshot_from_okr(
                {
                    "key": payload.get("key") or "GPWW-15",
                    "title": payload.get("title") or fixture.company_name,
                    "key_results": [
                        {**kr, "status": "committed"} for kr in (research.key_results or starting_krs)
                    ],
                },
                phase="in_progress",
                evidence=evidence,
                open_work=follow_open,
                scoreboard=dict(payload.get("scoreboard") or {}),
            ),
            model=model_id,
        )

        steps = {
            "research": json.dumps(_step_payload(research), ensure_ascii=False),
            "plan": json.dumps(_step_payload(plan), ensure_ascii=False),
            "followup": json.dumps(_step_payload(followup), ensure_ascii=False),
        }
        output = {
            "pack_id": self.pack.id,
            "steps": steps,
            "sections": steps,
            "evidence": evidence,
            "sources": {
                "page": json.dumps(evidence, ensure_ascii=False),
                "snippets": format_open_work(open_work),
            },
        }
        return output, usage


def format_open_work(open_work: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"- {item.get('key') or ''} {item.get('title') or item.get('summary') or ''}".strip()
        for item in open_work
    )


register_use_case(OkrGoalLoopEvaluator)
register_use_case_alias("okr-goal-loop", OkrGoalLoopEvaluator)
