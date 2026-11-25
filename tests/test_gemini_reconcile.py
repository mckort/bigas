"""Tests for Gemini invoice vs llm_logs reconciliation."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bigas.llm.logging_client import LoggingLLMClient
from bigas.llm.completion import LLMCompletion
from bigas.llm.usage import TokenUsage, estimate_cost_usd
from bigas.resources.cto.usage.gemini_reconcile import (
    format_gemini_reconciliation_block,
    reconcile_gemini_saving_action,
    summarize_gemini_gap,
)
from bigas.resources.cto.usage.service import build_weekly_cfo_ai_report


class GeminiReconcileTests(unittest.TestCase):
    def _sample_report(self):
        return {
            "days": 7,
            "totals": {"est_cost_usd": 50.0},
            "events": [
                {
                    "provider": "gcp_billing",
                    "feature": "gcp.gemini_invoice",
                    "est_cost_usd": 200.0,
                    "meta": {"gcp_sku": "Gemini 3.8 Flash Output"},
                },
                {
                    "provider": "gcp_billing",
                    "feature": "gcp.gemini_invoice",
                    "est_cost_usd": 20.0,
                    "meta": {"gcp_sku": "Context Cache Storage Time"},
                },
                {
                    "provider": "llm_logs",
                    "feature": "cto_pr_review",
                    "model": "gemini-3.8-flash",
                    "est_cost_usd": 94.0,
                    "input_tokens": 2_000_000,
                    "output_tokens": 400_000,
                    "meta": {"app": "bigas", "gcp_project": "bigas-503008"},
                },
                {
                    "provider": "llm_logs",
                    "feature": "llm.living_analysis",
                    "model": "gemini-pro-latest",
                    "est_cost_usd": 20.0,
                    "input_tokens": 100_000,
                    "output_tokens": 10_000,
                    "meta": {"app": "vcfieldassistant", "gcp_project": "vcfieldassistant"},
                },
            ],
        }

    def test_summarize_gap_includes_non_token_skus(self):
        summary = summarize_gemini_gap(self._sample_report())
        self.assertEqual(summary["invoice_usd"], 220.0)
        self.assertEqual(summary["list_price_usd"], 114.0)
        self.assertEqual(summary["gap_usd"], 106.0)
        self.assertGreaterEqual(summary["non_token_invoice_usd"], 20.0)
        self.assertTrue(summary["hypotheses"])

    def test_format_block_shows_gap_and_skus(self):
        block = format_gemini_reconciliation_block(self._sample_report())
        self.assertIn("gap", block.lower())
        self.assertIn("Context Cache Storage Time", block)
        self.assertIn("cto_pr_review", block)

    def test_saving_action_for_material_gap(self):
        action = reconcile_gemini_saving_action(self._sample_report())
        self.assertIsNotNone(action)
        self.assertEqual(action["id"], "reconcile-gemini-gap")
        self.assertGreaterEqual(action["impact_usd"], 10.0)

    def test_saving_action_skips_small_gap(self):
        report = {
            "events": [
                {
                    "provider": "gcp_billing",
                    "feature": "gcp.gemini_invoice",
                    "est_cost_usd": 50.0,
                    "meta": {"gcp_sku": "Gemini Output"},
                },
                {
                    "provider": "llm_logs",
                    "feature": "chat",
                    "model": "gemini-2.5-flash",
                    "est_cost_usd": 45.0,
                },
            ]
        }
        self.assertIsNone(reconcile_gemini_saving_action(report))

    def test_cached_token_discount_lowers_list_price(self):
        usage = TokenUsage(
            prompt_tokens=1_000_000,
            candidates_tokens=100_000,
            total_tokens=1_100_000,
            cached_content_tokens=800_000,
        )
        full = TokenUsage(
            prompt_tokens=1_000_000,
            candidates_tokens=100_000,
            total_tokens=1_100_000,
        )
        cached_cost = estimate_cost_usd("gemini-2.5-flash", usage)
        full_cost = estimate_cost_usd("gemini-2.5-flash", full)
        self.assertIsNotNone(cached_cost)
        self.assertIsNotNone(full_cost)
        self.assertLess(cached_cost, full_cost)

    def test_cfo_report_injects_reconcile_action_when_llm_omits_it(self):
        report = self._sample_report()
        with patch(
            "bigas.resources.cto.usage.service.analyze_weekly_ai_spend",
            return_value="### Drivers\n- A.\n### Savings\n- B.\n### Model\n- Stay.\n",
        ), patch(
            "bigas.resources.cto.usage.service.savings_tickets_section",
            side_effect=lambda actions, opener: "\n".join(a["id"] for a in actions),
        ), patch(
            "bigas.resources.cto.usage.service.enrich_with_prior_period",
            return_value=report,
        ):
            msg = build_weekly_cfo_ai_report(report, include_prior_period=False)
        self.assertIn("reconcile-gemini-gap", msg)


class MultimodalLoggingTests(unittest.TestCase):
    def test_complete_multimodal_emits_usage(self):
        inner = MagicMock()
        inner.complete_multimodal.return_value = LLMCompletion(
            text="screenshot ok",
            finish_reason="STOP",
            usage=TokenUsage(prompt_tokens=100, candidates_tokens=20, total_tokens=120),
        )
        client = LoggingLLMClient(inner, feature="ticket_attachments", model="gemini-2.5-flash")
        with patch.object(client, "_log_usage") as log_usage:
            result = client.complete_multimodal(["prompt", {"data": b"x"}])
        self.assertEqual(result.text, "screenshot ok")
        log_usage.assert_called_once()


if __name__ == "__main__":
    unittest.main()