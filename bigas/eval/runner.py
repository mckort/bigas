"""Orchestrates model discovery, adapter invocation, judging, and artifact storage."""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from bigas.eval.base import (
    BaseUseCaseEvaluator,
    EvalFixture,
    EvalModelResult,
    EvalRunResult,
    EvalUsage,
    get_use_case_evaluator,
)
from bigas.eval.judge import LLMJudge
from bigas.eval.registry import (
    ModelCandidate,
    estimate_model_cost_usd,
    get_candidate_models,
    resolve_baseline_model,
    update_eval_state_after_run,
)
from bigas.eval.reporter import attach_report_paths, build_markdown_report, publish_report
from bigas.eval.storage import EvalStorage

logger = logging.getLogger(__name__)


def _pack_baseline_model(evaluator: Any) -> str:
    pack = getattr(evaluator, "pack", None)
    baseline = getattr(pack, "baseline_model", None)
    return baseline.strip() if isinstance(baseline, str) else ""


def _budget_cap_usd() -> float:
    raw = (os.environ.get("MODEL_EVAL_BUDGET_USD") or "15").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 15.0


class EvalRunner:
    """Run a modular use-case evaluation across candidate models."""

    def __init__(
        self,
        *,
        storage: Optional[EvalStorage] = None,
        judge: Optional[LLMJudge] = None,
    ):
        self.storage = storage or EvalStorage()
        self.judge = judge or LLMJudge()

    def run(
        self,
        use_case: str,
        *,
        fixture: Optional[EvalFixture] = None,
        models: Optional[List[str]] = None,
        dry_run: bool = False,
        skip_judge: bool = False,
        skip_report: bool = False,
        post_discord: bool = True,
        post_chat: bool = True,
        include_baseline: bool = True,
    ) -> EvalRunResult:
        evaluator = get_use_case_evaluator(use_case)
        resolved_fixture = fixture or evaluator.default_fixture()
        run_id = uuid.uuid4().hex[:12]
        pack_baseline = _pack_baseline_model(evaluator)
        baseline = resolve_baseline_model(pack_baseline) if include_baseline else None
        candidates = get_candidate_models(
            use_case,
            storage=self.storage,
            explicit_models=models,
            baseline_model=pack_baseline,
            include_baseline=include_baseline,
        )

        run = EvalRunResult(
            use_case=evaluator.use_case_id,
            run_id=run_id,
            fixture=resolved_fixture,
            baseline_model=baseline.key if baseline else "",
            dry_run=dry_run,
        )

        if dry_run:
            run.results = [
                EvalModelResult(
                    model_id=c.model_id,
                    provider=c.provider,
                    output={"dry_run": True, "fixture": resolved_fixture.to_dict()},
                    usage=EvalUsage(),
                    score=None,
                    score_rationale="Dry run — adapter not invoked.",
                )
                for c in candidates
            ]
            attach_report_paths(run)
            run.report_markdown = build_markdown_report(run)
            if not skip_report:
                publish_report(run, post_discord=False, post_chat=False)
            return run

        spent_usd = 0.0
        budget = _budget_cap_usd()
        results: List[EvalModelResult] = []

        for candidate in candidates:
            if budget > 0 and spent_usd >= budget:
                logger.warning(
                    "Eval budget cap reached ($%.2f); skipping remaining models", budget
                )
                break
            result = self._evaluate_model(
                evaluator, resolved_fixture, candidate, skip_judge, run_id
            )
            results.append(result)
            if result.usage.cost_usd:
                spent_usd += result.usage.cost_usd

        run.results = results
        ranked = run.ranked_results()
        if ranked:
            ranked_candidates = [
                ModelCandidate(r.provider, r.model_id) for r in ranked
            ] + [
                ModelCandidate(r.provider, r.model_id)
                for r in results
                if r not in ranked and r.error is None
            ]
            run.champion_model = update_eval_state_after_run(
                evaluator.use_case_id,
                ranked_candidates,
                storage=self.storage,
            )

        attach_report_paths(run)
        run.report_markdown = build_markdown_report(run)
        self._persist_run_artifacts(run)

        if not skip_report:
            publish_report(run, post_discord=post_discord, post_chat=post_chat)

        return run

    def _evaluate_model(
        self,
        evaluator: BaseUseCaseEvaluator,
        fixture: EvalFixture,
        candidate: ModelCandidate,
        skip_judge: bool,
        run_id: str,
    ) -> EvalModelResult:
        started = time.perf_counter()
        try:
            output, usage = evaluator.run(fixture, candidate.model_id)
        except Exception as exc:
            logger.exception("Eval failed for %s", candidate.model_id)
            latency_ms = (time.perf_counter() - started) * 1000
            return EvalModelResult(
                model_id=candidate.model_id,
                provider=candidate.provider,
                output={},
                usage=EvalUsage(latency_ms=latency_ms),
                error=str(exc),
            )

        latency_ms = (time.perf_counter() - started) * 1000
        usage.latency_ms = latency_ms
        if usage.cost_usd is None:
            usage.cost_usd = estimate_model_cost_usd(
                candidate.provider,
                candidate.model_id,
                prompt_tokens=usage.prompt_tokens,
                output_tokens=usage.output_tokens,
            )

        score: Optional[float] = None
        rationale = ""
        if not skip_judge:
            try:
                score, rationale = self.judge.score(
                    evaluator=evaluator,
                    fixture=fixture,
                    output=output,
                )
            except Exception as exc:
                logger.exception("Judge failed for %s", candidate.model_id)
                rationale = f"Judge error: {exc}"

        blob_name = (
            f"{evaluator.artifact_prefix()}/{run_id}/"
            f"{candidate.model_id.replace('/', '_')}.json"
        )
        payload = {
            "model_id": candidate.model_id,
            "provider": candidate.provider,
            "fixture": fixture.to_dict(),
            "output": output,
            "usage": usage.to_dict(),
            "score": score,
            "score_rationale": rationale,
        }
        self.storage.store_json(blob_name, payload)

        return EvalModelResult(
            model_id=candidate.model_id,
            provider=candidate.provider,
            output=output,
            usage=usage,
            score=score,
            score_rationale=rationale,
            output_blob=blob_name,
        )

    def _persist_run_artifacts(self, run: EvalRunResult) -> None:
        prefix = f"eval-runs/{run.use_case}/{run.run_id}"
        self.storage.store_json(f"{prefix}/summary.json", run.to_dict())
        self.storage.store_json(
            f"{prefix}/fixture.json",
            {"fixture": run.fixture.to_dict(), "stored_at": time.time()},
        )
