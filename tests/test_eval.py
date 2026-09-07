"""Unit tests for the modular model evaluation engine."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from bigas.eval.base import (
    EvalFixture,
    EvalModelResult,
    EvalRunResult,
    EvalUsage,
    reject_customer_identifiers,
)
from bigas.eval.judge import LLMJudge
from bigas.eval.registry import (
    ModelCandidate,
    discover_pro_models,
    estimate_model_cost_usd,
    get_candidate_models,
    update_eval_state_after_run,
)
from bigas.eval.reporter import build_markdown_report
from bigas.eval.runner import EvalRunner
from bigas.eval.use_cases.vc_field_assistant import VCFieldAssistantEvaluator
from bigas.llm.completion import LLMCompletion
from bigas.llm.usage import TokenUsage


class FixtureIsolationTests(unittest.TestCase):
    def test_reject_workspace_id(self):
        with self.assertRaises(ValueError) as ctx:
            reject_customer_identifiers({"company_name": "Acme", "workspace_id": "ws-1"})
        self.assertIn("workspace_id", str(ctx.exception))

    def test_reject_company_id(self):
        with self.assertRaises(ValueError):
            reject_customer_identifiers({"companyId": "cust-123"})

    def test_fixture_from_dict_ok(self):
        fixture = EvalFixture.from_dict(
            {"company_name": "VC Field Assistant", "website_url": "https://vcfieldassistant.com"}
        )
        self.assertEqual(fixture.company_name, "VC Field Assistant")


class RegistryTests(unittest.TestCase):
    def test_estimate_openai_cost(self):
        cost = estimate_model_cost_usd("openai", "gpt-4o", prompt_tokens=1_000_000, output_tokens=100_000)
        self.assertIsNotNone(cost)
        self.assertGreater(cost, 0)

    def test_estimate_anthropic_cost(self):
        cost = estimate_model_cost_usd(
            "anthropic", "claude-sonnet-4-20250514", prompt_tokens=1_000_000, output_tokens=100_000
        )
        self.assertIsNotNone(cost)
        self.assertGreater(cost, 2.0)

    def test_get_candidate_models_explicit(self):
        models = get_candidate_models("vc-field-assistant", explicit_models=["gpt-4o", "gemini:gemini-2.5-pro"])
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0].model_id, "gpt-4o")

    def test_eliminated_models_skipped(self):
        storage = MagicMock()
        storage.get_json.return_value = {
            "use_cases": {
                "vc-field-assistant": {
                    "champion": "openai:gpt-4o",
                    "eliminated": ["gemini:gemini-2.5-pro"],
                }
            }
        }
        with patch("bigas.eval.registry.discover_pro_models") as discover:
            discover.return_value = [
                ModelCandidate("openai", "gpt-4o"),
                ModelCandidate("gemini", "gemini-2.5-pro"),
                ModelCandidate("openai", "gpt-5"),
            ]
            models = get_candidate_models("vc-field-assistant", storage=storage)
        keys = {m.key for m in models}
        self.assertIn("openai:gpt-4o", keys)
        self.assertIn("openai:gpt-5", keys)
        self.assertNotIn("gemini:gemini-2.5-pro", keys)

    def test_update_state_after_run(self):
        storage = MagicMock()
        storage.get_json.return_value = {"use_cases": {}}
        ranked = [
            ModelCandidate("openai", "gpt-5"),
            ModelCandidate("gemini", "gemini-2.5-pro"),
        ]
        champion = update_eval_state_after_run("vc-field-assistant", ranked, storage=storage)
        self.assertEqual(champion, "openai:gpt-5")
        storage.store_json.assert_called_once()

    @patch.dict("os.environ", {}, clear=True)
    @patch("bigas.eval.registry._discover_gemini_models", return_value=[])
    @patch("bigas.eval.registry._discover_openai_models", return_value=[])
    def test_discover_anthropic_models_when_other_providers_fail(self, *_mocks):
        models = discover_pro_models()
        self.assertGreaterEqual(len(models), 2)
        providers = {m.provider for m in models}
        self.assertIn("anthropic", providers)


class JudgeTests(unittest.TestCase):
    def test_parse_structured_score(self):
        judge = LLMJudge()
        completion = LLMCompletion(
            text='{"score": 87.5, "rationale": "Strong structure and sourcing."}',
            usage=TokenUsage(),
        )
        score, rationale = judge._parse_score(completion)
        self.assertEqual(score, 87.5)
        self.assertIn("Strong structure", rationale)


class ReporterTests(unittest.TestCase):
    def test_build_markdown_report(self):
        run = EvalRunResult(
            use_case="vc-field-assistant",
            run_id="abc123",
            fixture=EvalFixture("VC Field Assistant", "https://vcfieldassistant.com"),
            results=[
                EvalModelResult(
                    model_id="gpt-4o",
                    provider="openai",
                    output={"sections": {"summary": "Good"}},
                    usage=EvalUsage(prompt_tokens=1000, output_tokens=500, latency_ms=1200, cost_usd=0.05),
                    score=90.0,
                    score_rationale="Best overall quality.",
                ),
                EvalModelResult(
                    model_id="gemini-2.5-pro",
                    provider="gemini",
                    output={"sections": {"summary": "OK"}},
                    usage=EvalUsage(prompt_tokens=900, output_tokens=400, latency_ms=900, cost_usd=0.03),
                    score=75.0,
                    score_rationale="Solid but less detailed.",
                ),
            ],
        )
        md = build_markdown_report(run)
        self.assertIn("Champion", md)
        self.assertIn("gpt-4o", md)
        self.assertIn("90.0", md)


class VFAPackEvaluatorTests(unittest.TestCase):
    def _inline_pack(self):
        from bigas.eval.pack import pack_from_mapping

        return pack_from_mapping(
            {
                "id": "vfa-living-analysis",
                "name": "VC Field Assistant living analysis",
                "fixture": {
                    "company": "VC Field Assistant",
                    "url": "https://vcfieldassistant.com",
                },
                "steps": [{"id": "classify", "prompt": "Classify {{fixture.company}}"}],
                "rubric": "No invented figures.",
            }
        )

    def test_default_fixture(self):
        with patch.dict(
            "os.environ",
            {
                "EVAL_VFA_DEFAULT_COMPANY": "VC Field Assistant",
                "EVAL_VFA_DEFAULT_URL": "https://vcfieldassistant.com",
            },
        ):
            evaluator = VCFieldAssistantEvaluator(self._inline_pack())
            fixture = evaluator.default_fixture()
            self.assertEqual(fixture.website_url, "https://vcfieldassistant.com")

    @patch("bigas.eval.use_cases.vc_field_assistant.complete_eval_model")
    @patch("bigas.eval.use_cases.vc_field_assistant.fetch_page_text", return_value="Public homepage.")
    def test_run_success_without_product_http(self, mock_fetch, mock_complete):
        mock_complete.return_value = LLMCompletion(
            text='{"category":"investor workspace"}',
            usage=TokenUsage(prompt_tokens=100, candidates_tokens=50, total_tokens=150),
        )
        evaluator = VCFieldAssistantEvaluator(self._inline_pack())
        output, usage = evaluator.run(
            EvalFixture("Test Co", "https://example.com"),
            "gpt-4o",
        )
        self.assertIn("classify", output["steps"])
        self.assertEqual(usage.prompt_tokens, 100)
        mock_fetch.assert_called_once()
        mock_complete.assert_called_once()
        prompt = mock_complete.call_args[0][1]
        self.assertIn("Test Co", prompt)
        self.assertNotIn("workspaceId", prompt)


class EvalRunnerTests(unittest.TestCase):
    @patch("bigas.eval.runner.publish_report")
    @patch("bigas.eval.runner.get_use_case_evaluator")
    @patch("bigas.eval.runner.get_candidate_models")
    def test_dry_run(self, mock_candidates, mock_get_evaluator, mock_publish):
        mock_candidates.return_value = [ModelCandidate("openai", "gpt-4o")]
        evaluator = MagicMock()
        evaluator.use_case_id = "vc-field-assistant"
        evaluator.default_fixture.return_value = EvalFixture("Co", "https://example.com")
        mock_get_evaluator.return_value = evaluator

        runner = EvalRunner(storage=MagicMock())
        result = runner.run("vc-field-assistant", dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(len(result.results), 1)
        evaluator.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
