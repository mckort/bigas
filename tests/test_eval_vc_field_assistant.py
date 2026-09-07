"""Integration-style tests for VC Field Assistant model eval."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bigas.eval.base import EvalFixture
from bigas.eval.runner import EvalRunner


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

    def test_endpoint_rejects_customer_company_id(self):
        resp = self.client.post(
            "/tasks/eval/vc-field-assistant",
            json={"companyId": "cust-999"},
            headers={"X-Bigas-Access-Key": "test-key"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("companyId", resp.get_json()["error"])


class VFAIntegrationTests(unittest.TestCase):
    @patch("bigas.eval.runner.LLMJudge")
    @patch("bigas.eval.runner.get_candidate_models")
    @patch("bigas.eval.use_cases.vc_field_assistant.requests.post")
    def test_full_run_mocks_adapter(self, mock_post, mock_candidates, mock_judge_cls):
        mock_candidates.return_value = [MagicMock(provider="openai", model_id="gpt-4o", key="openai:gpt-4o")]
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "sections": {"executive_summary": "Analysis complete."},
                "usage": {"prompt_tokens": 2000, "output_tokens": 800},
            },
        )
        mock_judge_cls.return_value.score.return_value = (88.0, "High quality output.")

        storage = MagicMock()
        storage.get_json.return_value = {"use_cases": {}}

        with patch.dict("os.environ", {"EVAL_VFA_ENDPOINT": "https://vfa.example.com"}):
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
        storage.store_json.assert_called()
        mock_publish.assert_called_once()
        mock_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
