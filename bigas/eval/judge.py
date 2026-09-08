"""LLM-as-a-judge scoring for eval outputs."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from bigas.eval.base import BaseUseCaseEvaluator, EvalFixture
from bigas.llm.completion import LLMCompletion

logger = logging.getLogger(__name__)


class LLMJudge:
    """Score model outputs against a use-case rubric."""

    def __init__(self, *, model: Optional[str] = None):
        self.model = (
            (model or os.environ.get("MODEL_EVAL_JUDGE_MODEL") or "").strip() or None
        )

    def score(
        self,
        *,
        evaluator: BaseUseCaseEvaluator,
        fixture: EvalFixture,
        output: Dict[str, Any],
    ) -> Tuple[float, str]:
        rubric = evaluator.get_judge_rubric()
        prompt = self._build_prompt(rubric=rubric, fixture=fixture, output=output)
        completion = self._complete(prompt)
        return self._parse_score(completion)

    def _build_prompt(
        self,
        *,
        rubric: str,
        fixture: EvalFixture,
        output: Dict[str, Any],
    ) -> str:
        output_text = json.dumps(output, indent=2, ensure_ascii=False)
        if len(output_text) > 12000:
            output_text = output_text[:12000] + "\n... [truncated]"
        return (
            "You are an expert evaluator for AI model quality on investment analysis tasks.\n\n"
            f"Fixture company: {fixture.company_name}\n"
            f"Website: {fixture.website_url}\n\n"
            f"Rubric:\n{rubric}\n\n"
            "Model output (JSON):\n"
            f"{output_text}\n\n"
            "Respond with JSON only:\n"
            '{"score": <number 0-100>, "rationale": "<short motivation>"}'
        )

    def _complete(self, prompt: str) -> LLMCompletion:
        from bigas.llm.factory import get_llm_client

        client, _model = get_llm_client(feature="model_eval_judge", explicit_model=self.model)
        messages = [{"role": "user", "content": prompt}]
        kwargs = {"temperature": 0.1, "max_tokens": 800}
        detailed = getattr(client, "complete_detailed", None)
        if callable(detailed):
            result = detailed(messages=messages, **kwargs)
        else:
            result = client.complete(messages=messages, **kwargs)
        if isinstance(result, LLMCompletion):
            return result
        return LLMCompletion(text=str(result or ""))

    def _parse_score(self, completion: LLMCompletion) -> Tuple[float, str]:
        text = (completion.text or "").strip()
        parsed = self._extract_json(text)
        if parsed:
            score = parsed.get("score")
            rationale = (parsed.get("rationale") or parsed.get("motivation") or "").strip()
            try:
                value = float(score)
                return max(0.0, min(100.0, value)), rationale or text
            except (TypeError, ValueError):
                pass

        match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*/\s*100", text)
        if match:
            return float(match.group(1)), text
        match = re.search(r"score[\"']?\s*[:=]\s*(\d{1,3}(?:\.\d+)?)", text, re.I)
        if match:
            return float(match.group(1)), text
        logger.warning("Judge returned unparseable score: %s", text[:200])
        return 50.0, text or "Judge did not return a structured score."

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None
