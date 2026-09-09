"""LLM-as-a-judge scoring for eval outputs (two-model panel)."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from bigas.eval.base import BaseUseCaseEvaluator, EvalFixture
from bigas.eval.readable import humanize_model_output
from bigas.eval.registry import parse_model_ref
from bigas.llm.completion import LLMCompletion

logger = logging.getLogger(__name__)

SUBSCORE_WEIGHTS: Dict[str, float] = {
    "grounding": 0.30,
    "structure": 0.20,
    "landscape": 0.25,
    "hallucination": 0.25,
}
OKR_SUBSCORE_WEIGHTS: Dict[str, float] = {
    "grounding": 0.25,
    "replace_saas": 0.25,
    "concrete_tasks": 0.20,
    "no_clones": 0.15,
    "measurement": 0.15,
}
DEFAULT_JUDGE_INTRO = (
    "You are an expert evaluator for AI model quality on investment analysis tasks.\n"
)
OKR_JUDGE_INTRO = (
    "You are an expert evaluator for AI model quality on OKR / goal-loop work.\n"
)
DEFAULT_JUDGE_DIMENSIONS = (
    "- grounding: no invented figures, claims tied to the fixture page / research.\n"
    "- structure: required analysis sections present and usable.\n"
    "- landscape: three buckets (overlapping, ad-hoc, complementary); names evidenced.\n"
    "- hallucination: does not invent competitors, metrics, or citations.\n"
)
OKR_JUDGE_DIMENSIONS = (
    "- grounding: every KR number appears in the fixture evidence.\n"
    "- replace_saas: drops weekly active founders / SaaS kit; proposes brand-specific KRs.\n"
    "- concrete_tasks: actions that move a KR (landing page, campaign), not a KR restatement.\n"
    "- no_clones: no KR-title tickets, no wire-weekly/Instrument tickets, no Update catalog dupe.\n"
    "- measurement: honest measurable=false / source when a number is missing; no invented currents.\n"
)
DEFAULT_JUDGE_MODELS = (
    "gemini:gemini-3.1-pro-preview",
    "anthropic:claude-sonnet-5",
)
_STEP_CHAR_CAP = 5000


@dataclass
class JudgeVerdict:
    model_id: str
    provider: str
    score: Optional[float] = None
    rationale: str = ""
    subscores: Dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model_id}" if self.provider else self.model_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "score": self.score,
            "rationale": self.rationale,
            "subscores": dict(self.subscores),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "JudgeVerdict":
        subs = raw.get("subscores") or {}
        return cls(
            model_id=str(raw.get("model_id") or ""),
            provider=str(raw.get("provider") or ""),
            score=raw.get("score"),
            rationale=str(raw.get("rationale") or ""),
            subscores={str(k): float(v) for k, v in subs.items()} if isinstance(subs, Mapping) else {},
            error=raw.get("error"),
        )


def resolve_judge_models() -> List[str]:
    raw = (os.environ.get("MODEL_EVAL_JUDGE_MODELS") or "").strip()
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    single = (os.environ.get("MODEL_EVAL_JUDGE_MODEL") or "").strip()
    if single:
        first = single if ":" in single else f"{_provider_of(single)}:{single}"
        second = next((item for item in DEFAULT_JUDGE_MODELS if not _same_family(item, first)), "")
        return [first] + ([second] if second else [])
    return list(DEFAULT_JUDGE_MODELS)


def _provider_of(model_id: str) -> str:
    parsed = parse_model_ref(model_id)
    return parsed.provider if parsed else "unknown"


def _same_family(left: str, right: str) -> bool:
    left_parsed = parse_model_ref(left)
    right_parsed = parse_model_ref(right)
    if left_parsed and right_parsed:
        if left_parsed.provider != "unknown" or right_parsed.provider != "unknown":
            return left_parsed.provider == right_parsed.provider
        return left_parsed.key == right_parsed.key
    return _provider_of(left) == _provider_of(right)


def _clip(value: float) -> float:
    return max(0.0, min(100.0, value))


def judge_weights_for(evaluator: Any) -> Dict[str, float]:
    pack_id = getattr(evaluator, "pack_id", None)
    use_case = getattr(evaluator, "use_case_id", None)
    if pack_id == "okr-goal-loop" or use_case == "okr-goal-loop":
        return dict(OKR_SUBSCORE_WEIGHTS)
    return dict(SUBSCORE_WEIGHTS)


def judge_intro_for(evaluator: Any) -> str:
    pack_id = getattr(evaluator, "pack_id", None)
    use_case = getattr(evaluator, "use_case_id", None)
    if pack_id == "okr-goal-loop" or use_case == "okr-goal-loop":
        return OKR_JUDGE_INTRO
    return DEFAULT_JUDGE_INTRO


def judge_dimensions_for(evaluator: Any) -> str:
    pack_id = getattr(evaluator, "pack_id", None)
    use_case = getattr(evaluator, "use_case_id", None)
    if pack_id == "okr-goal-loop" or use_case == "okr-goal-loop":
        return OKR_JUDGE_DIMENSIONS
    return DEFAULT_JUDGE_DIMENSIONS


def weighted_score(
    subscores: Mapping[str, float],
    weights: Optional[Mapping[str, float]] = None,
) -> Optional[float]:
    if not subscores:
        return None
    table = dict(weights or SUBSCORE_WEIGHTS)
    total = 0.0
    weight = 0.0
    for key, share in table.items():
        if key in subscores:
            total += _clip(float(subscores[key])) * share
            weight += share
    if weight <= 0:
        return None
    return round(total / weight, 1)


def mean_score(values: Sequence[Optional[float]]) -> Optional[float]:
    nums = [float(item) for item in values if item is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 1)


def _output_for_judge(output: Mapping[str, Any]) -> str:
    steps = output.get("steps") or output.get("sections")
    if isinstance(steps, Mapping):
        parts: List[str] = []
        for key, value in steps.items():
            text = "" if value is None else str(value)
            if len(text) > _STEP_CHAR_CAP:
                text = text[:_STEP_CHAR_CAP] + "\n... [truncated this step]"
            parts.append(f"## {key}\n{text}")
        if parts:
            return "\n\n".join(parts)
    text = humanize_model_output(output)
    if len(text) > 24000:
        return text[:24000] + "\n... [truncated]"
    return text


class LLMJudge:
    """Score model outputs against a use-case rubric with one or more judges."""

    def __init__(self, *, models: Optional[Sequence[str]] = None, model: Optional[str] = None):
        if models:
            self.models = [item.strip() for item in models if (item or "").strip()]
        elif model:
            self.models = [model.strip()]
        else:
            self.models = resolve_judge_models()

    def score(
        self,
        *,
        evaluator: BaseUseCaseEvaluator,
        fixture: EvalFixture,
        output: Dict[str, Any],
    ) -> Tuple[Optional[float], str]:
        verdicts = self.score_panel(evaluator=evaluator, fixture=fixture, output=output)
        mean = mean_score([item.score for item in verdicts])
        bits = [item.rationale for item in verdicts if item.rationale]
        return mean, " ".join(bits)

    def score_panel(
        self,
        *,
        evaluator: BaseUseCaseEvaluator,
        fixture: EvalFixture,
        output: Dict[str, Any],
    ) -> List[JudgeVerdict]:
        rubric = evaluator.get_judge_rubric()
        weights = judge_weights_for(evaluator)
        prompt = self._build_prompt(
            rubric=rubric,
            fixture=fixture,
            output=output,
            evaluator=evaluator,
            weights=weights,
        )
        verdicts: List[JudgeVerdict] = []
        for raw in self.models:
            parsed = parse_model_ref(raw)
            if parsed is None:
                continue
            try:
                completion = self._complete(parsed.model_id, prompt)
                verdicts.append(
                    self._parse_verdict(
                        completion, parsed.model_id, parsed.provider, weights=weights
                    )
                )
            except Exception as exc:
                logger.exception("Judge %s failed", parsed.key)
                verdicts.append(
                    JudgeVerdict(
                        model_id=parsed.model_id,
                        provider=parsed.provider,
                        error=str(exc),
                        rationale=f"Judge error: {exc}",
                    )
                )
        return verdicts

    def _build_prompt(
        self,
        *,
        rubric: str,
        fixture: EvalFixture,
        output: Mapping[str, Any],
        evaluator: Any = None,
        weights: Optional[Mapping[str, float]] = None,
    ) -> str:
        table = dict(weights or judge_weights_for(evaluator))
        keys = list(table)
        json_keys = ", ".join(f'"{key}": 0-100' for key in keys)
        return (
            f"{judge_intro_for(evaluator)}"
            "Score each dimension 0-100 independently, then write a short prose rationale "
            "(no JSON in the rationale).\n\n"
            f"Fixture company: {fixture.company_name}\n"
            f"Website: {fixture.website_url}\n\n"
            f"Rubric:\n{rubric}\n\n"
            "Dimensions:\n"
            f"{judge_dimensions_for(evaluator)}\n"
            "Model output:\n"
            f"{_output_for_judge(output)}\n\n"
            "Respond with JSON only:\n"
            "{" + json_keys + ', "rationale": "<2-4 sentences of prose>"}\n'
            "Legacy {\"score\": N, \"rationale\": \"...\"} is also accepted."
        )

    def _complete(self, model_id: str, prompt: str) -> LLMCompletion:
        from bigas.eval.complete import complete_eval_model

        return complete_eval_model(model_id, prompt, max_tokens=1200, temperature=0.1)

    def _parse_verdict(
        self,
        completion: LLMCompletion,
        model_id: str,
        provider: str,
        weights: Optional[Mapping[str, float]] = None,
    ) -> JudgeVerdict:
        text = (completion.text or "").strip()
        parsed = self._extract_json(text) or {}
        table = dict(weights or SUBSCORE_WEIGHTS)
        subscores: Dict[str, float] = {}
        for key in table:
            if key in parsed:
                try:
                    subscores[key] = _clip(float(parsed[key]))
                except (TypeError, ValueError):
                    pass
        score = weighted_score(subscores, weights=table)
        if score is None and parsed.get("score") is not None:
            try:
                score = _clip(float(parsed["score"]))
            except (TypeError, ValueError):
                score = None
        rationale = str(parsed.get("rationale") or parsed.get("motivation") or "").strip()
        if not rationale:
            rationale = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
            if rationale.startswith("{") and '"rationale"' in rationale:
                nested = self._extract_json(rationale) or {}
                rationale = str(nested.get("rationale") or "").strip() or rationale
        if score is None:
            match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*/\s*100", text)
            if match:
                score = _clip(float(match.group(1)))
        if score is None:
            logger.warning("Judge returned unparseable score: %s", text[:200])
            score = 50.0
            rationale = rationale or text or "Judge did not return a structured score."
        return JudgeVerdict(
            model_id=model_id,
            provider=provider,
            score=score,
            rationale=rationale,
            subscores=subscores,
        )

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        blob = (text or "").strip()
        if blob.startswith("```"):
            blob = re.sub(r"^```(?:json)?\s*", "", blob)
            blob = re.sub(r"\s*```$", "", blob)
        try:
            data = json.loads(blob)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
        start = blob.find("{")
        end = blob.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(blob[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None
