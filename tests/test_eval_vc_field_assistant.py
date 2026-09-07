"""Integration-style tests for VC Field Assistant pack eval."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bigas.eval.base import EvalFixture, get_use_case_evaluator, list_use_cases
from bigas.eval.pack import load_pack
from bigas.eval.runner import EvalRunner
from bigas.llm.completion import LLMCompletion
from bigas.llm.usage import TokenUsage


class EvalEndpointTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(
            "os.environ",
            {
                "GA4_PROPERTY_ID": "123456789",
                "BIGAS_ACCESS_MODE": "restricted",
                "BIGAS_ACCESS_KEYS": "test-key",
                "CHAT_ENABLED": "false",
            },
        ):
            from app import create_app

            self.app = create_app()
        self.client = self.app.test_client()

    @patch("bigas.eval.endpoints.EvalRunner")
    def test_endpoint_requires_auth(self, mock_runner):
        resp = self.client.post("/tasks/eval/vc-field-assistant", json={})
        self.assertEqual(resp.status_code, 401)
        mock_runner.assert_not_called()

    @patch("bigas.eval.endpoints.EvalRunner")
    def test_endpoint_runs_with_auth(self, mock_runner):
        from bigas.eval.base import EvalRunResult

        mock_runner.return_value.run.return_value = EvalRunResult(
            use_case="vc-field-assistant",
            run_id="run1",
            fixture=EvalFixture("VC Field Assistant", "https://vcfieldassistant.com"),
        )
        resp = self.client.post(
            "/tasks/eval/vc-field-assistant",
            json={"company": "VC Field Assistant", "url": "https://vcfieldassistant.com", "dry_run": True},
            headers={"X-Bigas-Access-Key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["use_case"], "vc-field-assistant")

    @patch("bigas.eval.endpoints.EvalRunner")
    def test_pack_id_endpoint_works(self, mock_runner):
        from bigas.eval.base import EvalRunResult

        mock_runner.return_value.run.return_value = EvalRunResult(
            use_case="vc-field-assistant",
            run_id="run1",
            fixture=EvalFixture("VC Field Assistant", "https://vcfieldassistant.com"),
        )
        resp = self.client.post(
            "/tasks/eval/vfa-living-analysis",
            json={"dry_run": True},
            headers={"X-Bigas-Access-Key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200)
        mock_runner.return_value.run.assert_called_once()

    def test_endpoint_rejects_customer_company_id(self):
        resp = self.client.post(
            "/tasks/eval/vc-field-assistant",
            json={"companyId": "cust-999"},
            headers={"X-Bigas-Access-Key": "test-key"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("companyId", resp.get_json()["error"])


class VFAPackIntegrationTests(unittest.TestCase):
    def test_aliases_resolve_to_same_evaluator(self):
        import bigas.eval.use_cases.vc_field_assistant  # noqa: F401

        self.assertIn("vc-field-assistant", list_use_cases())
        self.assertIn("vfa-living-analysis", list_use_cases())
        self.assertEqual(
            type(get_use_case_evaluator("vc-field-assistant")),
            type(get_use_case_evaluator("vfa-living-analysis")),
        )

    def test_shipped_pack_is_self_contained(self):
        pack = load_pack("vfa-living-analysis")
        classify = next(step for step in pack.steps if step.id == "classify")
        self.assertFalse(classify.prompt_from)
        self.assertIn("Classify this portfolio company", classify.resolved_prompt)
        self.assertNotIn("{namn}", classify.resolved_prompt)
        self.assertNotIn("{corpus", classify.resolved_prompt)
        primary = next(step for step in pack.steps if step.id == "primary")
        for token in (
            "<sektionsprompt>",
            "om uppdatering",
            "{beskrivningssektioner}",
            "{bekräftade KPI",
            "{uppladdat material",
        ):
            self.assertNotIn(token, primary.resolved_prompt, msg=f"unresolved placeholder {token!r}")
        landscape = next(step for step in pack.steps if step.id == "landscape")
        self.assertEqual(landscape.research.provider, "web")
        self.assertIn("Write three buckets", landscape.resolved_prompt)
        self.assertIn("Bucket assignment:", landscape.resolved_prompt)
        self.assertNotIn("{guardrails", landscape.resolved_prompt)

    @patch("bigas.eval.runner.LLMJudge")
    @patch("bigas.eval.runner.get_candidate_models")
    @patch("bigas.eval.use_cases.vc_field_assistant.complete_eval_model")
    @patch("bigas.eval.use_cases.vc_field_assistant.run_web_research", return_value="")
    @patch("bigas.eval.use_cases.vc_field_assistant.fetch_page_text", return_value="Public homepage.")
    def test_full_run_mocks_llm_not_vfa_http(
        self,
        mock_fetch,
        _mock_research,
        mock_complete,
        mock_candidates,
        mock_judge_cls,
    ):
        mock_candidates.return_value = [
            MagicMock(provider="openai", model_id="gpt-4o", key="openai:gpt-4o")
        ]
        mock_complete.return_value = LLMCompletion(
            text='{"category":"investor workspace","customerSegment":"VCs"}',
            usage=TokenUsage(prompt_tokens=2000, candidates_tokens=800, total_tokens=2800),
        )
        mock_judge_cls.return_value.score.return_value = (88.0, "High quality output.")

        storage = MagicMock()
        storage.get_json.return_value = {"use_cases": {}}

        from bigas.eval.pack import pack_from_mapping
        from bigas.eval.use_cases.vc_field_assistant import VCFieldAssistantEvaluator

        pack = pack_from_mapping(
            {
                "id": "vfa-living-analysis",
                "fixture": {"company": "VC Field Assistant", "url": "https://vcfieldassistant.com"},
                "steps": [
                    {"id": "classify", "prompt": "Classify {{fixture.page}}"},
                    {
                        "id": "landscape",
                        "prompt": "Landscape {{research.snippets}}",
                        "research": {"provider": "web", "queries": ["{{steps.classify.category}} alternatives"]},
                    },
                ],
                "rubric": "No invented figures.",
            }
        )

        with patch(
            "bigas.eval.runner.get_use_case_evaluator",
            return_value=VCFieldAssistantEvaluator(pack),
        ):
            runner = EvalRunner(storage=storage, judge=mock_judge_cls.return_value)
            with patch("bigas.eval.runner.publish_report") as mock_publish:
                result = runner.run(
                    "vc-field-assistant",
                    fixture=EvalFixture("VC Field Assistant", "https://vcfieldassistant.com"),
                    models=["gpt-4o"],
                    skip_judge=False,
                    post_discord=False,
                    post_chat=False,
                )

        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].score, 88.0)
        self.assertEqual(mock_complete.call_count, 2)
        storage.store_json.assert_called()
        mock_publish.assert_called_once()
        mock_fetch.assert_called()


if __name__ == "__main__":
    unittest.main()
