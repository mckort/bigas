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
from bigas.eval.discover import (
    OFFICIAL_MODEL_PAGES,
    discover_flagship_models,
    extract_ids_from_text,
    is_not_reasoning,
    match_catalog_id,
)
from bigas.eval.registry import (
    DEFAULT_PRO_MODELS,
    ModelCandidate,
    discover_pro_models,
    estimate_model_cost_usd,
    get_candidate_models,
    parse_model_ref,
    update_eval_state_after_run,
)
from bigas.eval.html import build_html_report
from bigas.eval.reporter import build_markdown_report
from bigas.eval.readable import build_full_markdown, humanize_step_output
from bigas.eval.runner import EvalRunner
from bigas.eval.pack import load_pack
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
        models = get_candidate_models(
            "vc-field-assistant",
            explicit_models=["gpt-4o", "gemini:gemini-2.5-pro"],
            include_baseline=False,
        )
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0].model_id, "gpt-4o")

    @patch.dict("os.environ", {"EVAL_BASELINE_MODEL": ""}, clear=False)
    def test_explicit_models_still_include_baseline(self):
        models = get_candidate_models(
            "vc-field-assistant",
            explicit_models=["openai:gpt-4o"],
            baseline_model="gemini:gemini-2.5-pro",
        )
        self.assertEqual([m.key for m in models], ["gemini:gemini-2.5-pro", "openai:gpt-4o"])

    @patch.dict("os.environ", {"EVAL_BASELINE_MODEL": ""}, clear=False)
    def test_baseline_survives_elimination(self):
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
            models = get_candidate_models(
                "vc-field-assistant",
                storage=storage,
                baseline_model="gemini:gemini-2.5-pro",
            )
        keys = [m.key for m in models]
        self.assertEqual(keys[0], "gemini:gemini-2.5-pro")
        self.assertIn("openai:gpt-4o", keys)
        self.assertIn("openai:gpt-5", keys)

    def test_parse_model_ref(self):
        model = parse_model_ref("gemini:gemini-2.5-pro")
        self.assertEqual(model.key, "gemini:gemini-2.5-pro")

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
            models = get_candidate_models(
                "vc-field-assistant",
                storage=storage,
                include_baseline=False,
            )
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

    @patch.dict("os.environ", {"EVAL_MODELS_PER_PROVIDER": "2"}, clear=False)
    @patch("bigas.eval.discover.load_provider_catalogs", return_value={})
    @patch("bigas.eval.discover.gather_flagship_snippets", return_value="")
    def test_discover_falls_back_when_official_pages_empty(self, _snippets, _catalogs):
        models = discover_pro_models()
        self.assertLessEqual(len(models), 6)
        providers = {m.provider for m in models}
        self.assertIn("anthropic", providers)
        self.assertTrue(all(sum(1 for m in models if m.provider == p) <= 2 for p in providers))

    def test_fallback_excludes_retired_gemini_25_pro(self):
        self.assertNotIn(("gemini", "gemini-2.5-pro"), DEFAULT_PRO_MODELS)
        self.assertIn(("gemini", "gemini-3.1-pro-preview"), DEFAULT_PRO_MODELS)


class DiscoverTests(unittest.TestCase):
    def test_official_overview_urls_are_stable_paths(self):
        self.assertEqual(
            OFFICIAL_MODEL_PAGES["anthropic"],
            "https://platform.claude.com/docs/en/models/overview",
        )
        self.assertEqual(
            OFFICIAL_MODEL_PAGES["openai"],
            "https://developers.openai.com/api/docs/models",
        )
        self.assertEqual(
            OFFICIAL_MODEL_PAGES["gemini"],
            "https://ai.google.dev/gemini-api/docs/models",
        )

    def test_extract_claude_ids_from_overview_copy(self):
        text = (
            "Claude Fable 5.1 claude-fable-5-1 for demanding reasoning. "
            "Claude Opus 5 claude-opus-5 for complex agentic coding. "
            "Claude Sonnet 5 claude-sonnet-5. Claude Haiku 4.5 claude-haiku-4-5-20251001."
        )
        ids = extract_ids_from_text("anthropic", text)
        self.assertEqual(ids, ["claude-fable-5-1", "claude-opus-5"])
        self.assertNotIn("claude-haiku-4-5-20251001", ids)

    def test_extract_skips_sora_and_lyria(self):
        openai_ids = extract_ids_from_text("openai", "Flagship gpt-6-astra plus sora-2-pro and gpt-image-1")
        gemini_ids = extract_ids_from_text("gemini", "gemini-3.1-pro-preview and lyria-3-pro-preview")
        self.assertEqual(openai_ids, ["gpt-6-astra"])
        self.assertEqual(gemini_ids, ["gemini-3.1-pro-preview"])

    def test_gemini_id_is_not_excluded_as_mini(self):
        self.assertFalse(is_not_reasoning("gemini-3.1-pro-preview"))
        self.assertTrue(is_not_reasoning("gpt-4o-mini"))
        self.assertTrue(is_not_reasoning("claude-haiku-4-5"))

    def test_extract_gemini_pro_skips_flash(self):
        ids = extract_ids_from_text(
            "gemini",
            "Latest gemini-3-flash and gemini-3.1-pro-preview plus gemini-2.5-pro",
        )
        self.assertEqual(ids, ["gemini-3.1-pro-preview", "gemini-2.5-pro"])

    def test_caps_at_two_per_provider(self):
        models = discover_flagship_models(
            catalog={
                "openai": ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.4"],
                "anthropic": ["claude-fable-5-1", "claude-opus-5", "claude-sonnet-5"],
                "gemini": ["gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-2.0-pro"],
            },
            snippets="unused",
            picked={
                "openai": ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.4"],
                "anthropic": ["claude-fable-5-1", "claude-opus-5", "claude-sonnet-5"],
                "gemini": ["gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-2.0-pro"],
            },
        )
        self.assertEqual(len(models), 6)
        self.assertTrue(all(sum(1 for m in models if m.provider == p) <= 2 for p in {m.provider for m in models}))

    def test_match_catalog_prefers_undated_alias(self):
        match = match_catalog_id(
            "claude-opus-5",
            ["claude-opus-5-20260301", "claude-opus-5"],
        )
        self.assertEqual(match, "claude-opus-5")


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

    def test_score_uses_complete_detailed(self):
        judge = LLMJudge()
        evaluator = MagicMock()
        evaluator.get_judge_rubric.return_value = "Be accurate."
        client = MagicMock()
        client.complete_detailed.return_value = LLMCompletion(
            text='{"score": 80, "rationale": "Solid."}',
            usage=TokenUsage(),
        )
        with patch("bigas.llm.factory.get_llm_client", return_value=(client, "gemini-3.1-pro-preview")):
            score, rationale = judge.score(
                evaluator=evaluator,
                fixture=EvalFixture("Co", "https://example.com"),
                output={"summary": "ok"},
            )
        self.assertEqual(score, 80)
        self.assertIn("Solid", rationale)
        client.complete_detailed.assert_called_once()
        client.complete.assert_not_called()

    def test_score_when_only_complete_returns_str(self):
        judge = LLMJudge()
        evaluator = MagicMock()
        evaluator.get_judge_rubric.return_value = "Be accurate."
        client = MagicMock(spec=["complete"])
        client.complete.return_value = '{"score": 64, "rationale": "Plain string."}'
        with patch("bigas.llm.factory.get_llm_client", return_value=(client, "gemini-3.1-pro-preview")):
            score, rationale = judge.score(
                evaluator=evaluator,
                fixture=EvalFixture("Co", "https://example.com"),
                output={"summary": "ok"},
            )
        self.assertEqual(score, 64)
        self.assertIn("Plain string", rationale)


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

    def test_full_report_unwraps_json_steps(self):
        run = EvalRunResult(
            use_case="vc-field-assistant",
            run_id="abc123",
            fixture=EvalFixture("VC Field Assistant", "https://vcfieldassistant.com"),
            baseline_model="gemini:gemini-2.5-pro",
            results=[
                EvalModelResult(
                    model_id="gemini-2.5-pro",
                    provider="gemini",
                    output={
                        "steps": {
                            "primary": '{"sections":[{"key":"company_snapshot","title":"Company overview","body":"A portfolio workspace for VCs."}]}',
                            "landscape": '{"overlapping":[{"name":"Affinity","product":"CRM","strength":"Network","relevance":"High"}],"adHoc":[],"complementary":[]}',
                        }
                    },
                    usage=EvalUsage(latency_ms=800, cost_usd=0.02),
                    score=80.0,
                    score_rationale="Grounded snapshot.",
                )
            ],
        )
        md = build_full_markdown(run)
        self.assertIn("In production", md)
        self.assertIn("A portfolio workspace for VCs.", md)
        self.assertIn("Affinity", md)
        self.assertNotIn('"sections"', md)
        html_page = build_html_report(run)
        self.assertIn("A portfolio workspace for VCs.", html_page)
        self.assertIn("<table>", html_page)

    def test_humanize_classify_json(self):
        text = humanize_step_output(
            "classify",
            '{"category":"investor workspace","productFunction":"living analysis"}',
        )
        self.assertIn("investor workspace", text)
        self.assertIn("productFunction", text)


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

    def test_pack_exposes_baseline_model(self):
        pack = load_pack("vfa-living-analysis")
        self.assertEqual(pack.baseline_model, "gemini:gemini-2.5-pro")

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


class AnthropicCompleteTests(unittest.TestCase):
    def _ok_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        }
        return resp

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("bigas.eval.complete.requests.post")
    def test_claude_5_omits_temperature(self, mock_post):
        from bigas.eval.complete import complete_eval_model

        mock_post.return_value = self._ok_response()
        complete_eval_model("claude-opus-5", "hello", max_tokens=256, temperature=0.2)
        payload = mock_post.call_args.kwargs["json"]
        self.assertNotIn("temperature", payload)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("bigas.eval.complete.requests.post")
    def test_claude_4_keeps_temperature(self, mock_post):
        from bigas.eval.complete import complete_eval_model

        mock_post.return_value = self._ok_response()
        complete_eval_model("claude-sonnet-4-20250514", "hello", max_tokens=256, temperature=0.2)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["temperature"], 0.2)


if __name__ == "__main__":
    unittest.main()
