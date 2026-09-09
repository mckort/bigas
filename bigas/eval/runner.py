"""Orchestrates model discovery, adapter invocation, judging, and artifact storage."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

EVAL_TZ = ZoneInfo("Europe/Stockholm")


def new_eval_run_id(now: Optional[datetime] = None) -> str:
    """URL-safe local timestamp, e.g. 2026-09-09-07-54-12."""
    stamp = now or datetime.now(EVAL_TZ)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=EVAL_TZ)
    else:
        stamp = stamp.astimezone(EVAL_TZ)
    return stamp.strftime("%Y-%m-%d-%H-%M-%S")

from bigas.eval.base import (
    BaseUseCaseEvaluator,
    EvalFixture,
    EvalModelResult,
    EvalRunResult,
    EvalUsage,
    get_use_case_evaluator,
)
from bigas.eval.checks import DEFAULT_REQUIRED_STEPS, run_mechanical_checks
from bigas.eval.judge import LLMJudge, JudgeVerdict, mean_score
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


def _resolve_fixtures(
    evaluator: BaseUseCaseEvaluator,
    fixture: Optional[EvalFixture],
) -> List[EvalFixture]:
    if fixture is not None:
        return [fixture]
    resolved = list(evaluator.default_fixtures() or [])
    if resolved and all(isinstance(item, EvalFixture) for item in resolved):
        return resolved
    return [evaluator.default_fixture()]


def _required_steps(evaluator: BaseUseCaseEvaluator) -> Sequence[str]:
    pack = getattr(evaluator, "pack", None)
    steps = getattr(pack, "steps", None) or []
    ids = [getattr(step, "id", "") for step in steps if getattr(step, "id", "")]
    return ids or DEFAULT_REQUIRED_STEPS


def _merge_usage(total: EvalUsage, part: EvalUsage) -> None:
    total.prompt_tokens += part.prompt_tokens
    total.output_tokens += part.output_tokens
    total.cached_tokens += part.cached_tokens
    total.total_tokens += part.total_tokens
    total.latency_ms += part.latency_ms
    if part.cost_usd is not None:
        total.cost_usd = (total.cost_usd or 0.0) + part.cost_usd


def _clip(value: float) -> float:
    return max(0.0, min(100.0, value))


def _mean_map(rows: Sequence[Dict[str, float]]) -> Dict[str, float]:
    keys: List[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    out: Dict[str, float] = {}
    for key in keys:
        values = [float(row[key]) for row in rows if key in row]
        if values:
            out[key] = round(sum(values) / len(values), 1)
    return out


def _combine_output(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    if len(rows) == 1:
        return dict(rows[0].get("output") or {})
    return {
        "fixtures": [
            {
                "company": row["company"],
                "url": row["url"],
                **(row.get("output") or {}),
            }
            for row in rows
        ]
    }


def _human_rationale(
    fixture_rows: Sequence[Dict[str, Any]],
    judge_scores: Dict[str, float],
    mechanical_penalty: float,
) -> str:
    bits: List[str] = []
    if judge_scores:
        per_judge = " · ".join(
            f"{key.split(':', 1)[-1]} {value:.0f}" for key, value in judge_scores.items()
        )
        bits.append(f"Judges: {per_judge}.")
    if mechanical_penalty:
        bits.append(f"Mechanical penalty −{mechanical_penalty:.0f}.")
    for row in fixture_rows:
        company = str(row.get("company") or "Fixture")
        notes: List[str] = []
        for verdict in row.get("judges") or []:
            if not isinstance(verdict, dict):
                continue
            rationale = str(verdict.get("rationale") or "").strip()
            if not rationale:
                continue
            label = str(verdict.get("model_id") or "judge")
            notes.append(f"{label}: {rationale}")
        mech = [str(item) for item in (row.get("mechanical_notes") or [])]
        if mech:
            notes.append("Mechanical: " + "; ".join(mech))
        if notes:
            bits.append(f"{company} — " + " ".join(notes))
    return " ".join(bits).strip()


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
        fixtures = _resolve_fixtures(evaluator, fixture)
        resolved_fixture = fixtures[0]
        run_id = new_eval_run_id()
        pack_baseline = _pack_baseline_model(evaluator)
        baseline = resolve_baseline_model(pack_baseline) if include_baseline else None
        candidates = get_candidate_models(
            use_case,
            storage=self.storage,
            explicit_models=models,
            baseline_model=pack_baseline,
            include_baseline=include_baseline,
        )
        judge_models = [str(item) for item in (getattr(self.judge, "models", []) or []) if isinstance(item, str)]
        rubric_raw = evaluator.get_judge_rubric()
        rubric = rubric_raw if isinstance(rubric_raw, str) else ""

        run = EvalRunResult(
            use_case=evaluator.use_case_id,
            run_id=run_id,
            fixture=resolved_fixture,
            fixtures=fixtures,
            baseline_model=baseline.key if baseline else "",
            dry_run=dry_run,
            judge_models=judge_models,
            rubric=rubric,
        )

        if dry_run:
            run.results = [
                EvalModelResult(
                    model_id=c.model_id,
                    provider=c.provider,
                    output={
                        "dry_run": True,
                        "fixtures": [item.to_dict() for item in fixtures],
                    },
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
                evaluator, fixtures, candidate, skip_judge, run_id
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
        fixtures: Sequence[EvalFixture],
        candidate: ModelCandidate,
        skip_judge: bool,
        run_id: str,
    ) -> EvalModelResult:
        started = time.perf_counter()
        usage = EvalUsage()
        fixture_rows: List[Dict[str, Any]] = []
        first_error: Optional[str] = None

        for fixture in fixtures:
            try:
                output, step_usage = evaluator.run(fixture, candidate.model_id)
            except Exception as exc:
                logger.exception("Eval failed for %s on %s", candidate.model_id, fixture.company_name)
                first_error = first_error or str(exc)
                continue
            if step_usage.cost_usd is None:
                step_usage.cost_usd = estimate_model_cost_usd(
                    candidate.provider,
                    candidate.model_id,
                    prompt_tokens=step_usage.prompt_tokens,
                    output_tokens=step_usage.output_tokens,
                )
            _merge_usage(usage, step_usage)

            verdicts: List[JudgeVerdict] = []
            mechanical = run_mechanical_checks(
                output,
                fixture,
                required_steps=_required_steps(evaluator),
            )
            if not skip_judge:
                try:
                    verdicts = self.judge.score_panel(
                        evaluator=evaluator,
                        fixture=fixture,
                        output=output,
                    )
                except Exception as exc:
                    logger.exception("Judge failed for %s", candidate.model_id)
                    verdicts = [
                        JudgeVerdict(
                            model_id="judge",
                            provider="",
                            error=str(exc),
                            rationale=f"Judge error: {exc}",
                        )
                    ]

            judge_mean = mean_score([item.score for item in verdicts])
            score = None
            if judge_mean is not None:
                score = round(_clip(judge_mean - mechanical.penalty), 1)
            elif skip_judge:
                score = None
            else:
                score = round(_clip(50.0 - mechanical.penalty), 1)

            fixture_rows.append(
                {
                    "company": fixture.company_name,
                    "url": fixture.website_url,
                    "score": score,
                    "judge_mean": judge_mean,
                    "mechanical_penalty": mechanical.penalty,
                    "mechanical_notes": list(mechanical.notes),
                    "judges": [item.to_dict() for item in verdicts],
                    "judge_scores": {
                        item.key: float(item.score)
                        for item in verdicts
                        if item.score is not None
                    },
                    "subscores": _mean_map([item.subscores for item in verdicts if item.subscores]),
                    "output": output,
                }
            )

        latency_ms = (time.perf_counter() - started) * 1000
        usage.latency_ms = latency_ms
        if first_error and not fixture_rows:
            return EvalModelResult(
                model_id=candidate.model_id,
                provider=candidate.provider,
                output={},
                usage=usage,
                error=first_error,
            )

        judge_scores = _mean_map([row["judge_scores"] for row in fixture_rows])
        subscores = _mean_map([row["subscores"] for row in fixture_rows])
        mechanical_penalty = mean_score(
            [row.get("mechanical_penalty") for row in fixture_rows]
        ) or 0.0
        notes: List[str] = []
        for row in fixture_rows:
            notes.extend(str(item) for item in (row.get("mechanical_notes") or []))
        score = mean_score([row.get("score") for row in fixture_rows])
        judges = []
        if fixture_rows:
            # Keep one row per judge key from the first fixture, overwrite scores with means.
            seen: Dict[str, Dict[str, Any]] = {}
            for row in fixture_rows:
                for verdict in row.get("judges") or []:
                    if not isinstance(verdict, dict):
                        continue
                    provider = verdict.get("provider")
                    model_id = verdict.get("model_id") or ""
                    key = f"{provider}:{model_id}" if provider else model_id
                    if key not in seen:
                        seen[key] = dict(verdict)
            for key, verdict in seen.items():
                if key in judge_scores:
                    verdict["score"] = judge_scores[key]
                judges.append(verdict)

        rationale = _human_rationale(fixture_rows, judge_scores, mechanical_penalty)
        output = _combine_output(fixture_rows)
        stored_rows = [{k: v for k, v in row.items() if k != "output"} for row in fixture_rows]

        blob_name = (
            f"{evaluator.artifact_prefix()}/{run_id}/"
            f"{candidate.model_id.replace('/', '_')}.json"
        )
        payload = {
            "model_id": candidate.model_id,
            "provider": candidate.provider,
            "fixtures": [item.to_dict() for item in fixtures],
            "output": output,
            "usage": usage.to_dict(),
            "score": score,
            "score_rationale": rationale,
            "error": first_error,
            "judge_scores": judge_scores,
            "judges": judges,
            "subscores": subscores,
            "mechanical_penalty": mechanical_penalty,
            "fixture_scores": stored_rows,
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
            error=first_error,
            judge_scores=judge_scores,
            judges=judges,
            subscores=subscores,
            mechanical_penalty=mechanical_penalty,
            mechanical_notes=list(dict.fromkeys(notes)),
            fixture_scores=stored_rows,
        )

    def _persist_run_artifacts(self, run: EvalRunResult) -> None:
        prefix = f"eval-runs/{run.use_case}/{run.run_id}"
        self.storage.store_json(f"{prefix}/summary.json", run.to_dict())
        self.storage.store_json(
            f"{prefix}/fixture.json",
            {
                "fixture": run.fixture.to_dict(),
                "fixtures": [item.to_dict() for item in run.all_fixtures()],
                "stored_at": time.time(),
            },
        )
