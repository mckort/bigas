"""Tests for CTO AI usage pricing, Discord formatting, and providers."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bigas.providers.usage.base import UsageEvent
from bigas.providers.usage.cursor import CursorCloudAgentUsageProvider
from bigas.providers.usage.llm_logs import (
    CloudRunLlmUsageProvider,
    _parse_log_payload,
    _usage_project_ids,
)
from bigas.resources.cto.usage.pricing import (
    CursorTokenUsage,
    estimate_cursor_cost_usd,
    resolve_cursor_price_usd_per_mtok,
)
from bigas.resources.cto.usage.service import (
    fetch_ai_usage,
    format_cursor_usage_discord_lines,
    format_weekly_cto_ai_report,
    build_weekly_cfo_ai_report,
    publish_weekly_cfo_ai_report,
)


class CursorPricingTests(unittest.TestCase):
    def test_composer_standard_price(self):
        prices = resolve_cursor_price_usd_per_mtok("composer-2.5")
        self.assertEqual(prices, (0.50, 2.50, 0.50, 0.20))

    def test_composer_fast_price(self):
        prices = resolve_cursor_price_usd_per_mtok("composer-2.5-fast")
        self.assertEqual(prices, (3.00, 15.00, 3.00, 0.20))

    def test_estimate_includes_cache(self):
        usage = CursorTokenUsage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_write_tokens=0,
            cache_read_tokens=1_000_000,
        )
        # 0.50 + 2.50 + 0.20
        cost = estimate_cursor_cost_usd("composer-2.5", usage)
        self.assertEqual(cost, 3.2)

    def test_from_mapping_camel_case(self):
        usage = CursorTokenUsage.from_mapping(
            {
                "inputTokens": 10,
                "outputTokens": 5,
                "cacheWriteTokens": 2,
                "cacheReadTokens": 20,
            }
        )
        self.assertEqual(usage.total_tokens, 37)


class DiscordFormatTests(unittest.TestCase):
    def test_discord_lines(self):
        usage = CursorTokenUsage(
            input_tokens=6320,
            output_tokens=1450,
            cache_write_tokens=7100,
            cache_read_tokens=21300,
        )
        lines = format_cursor_usage_discord_lines(
            usage=usage, model="composer-2.5", est_cost_usd=0.42
        )
        self.assertTrue(any("Cursor usage:" in line for line in lines))
        self.assertTrue(any("0.4200" in line for line in lines))


class WeeklyReportTests(unittest.TestCase):
    def test_format_weekly_report(self):
        report = {
            "days": 7,
            "totals": {
                "est_cost_usd": 12.34,
                "events": 4,
                "by_provider": {"cursor": 8.1, "llm_logs": 4.24},
                "by_feature": {"cto_autofix": 8.1, "cto_pr_review": 4.24, "chat": 1.1},
                "activity_by_feature": {
                    "cto_autofix": 2,
                    "cto_pr_review": 1,
                    "chat": 1,
                },
            },
            "events": [
                {"feature": "cto_autofix"},
                {"feature": "cto_autofix"},
                {"feature": "cto_pr_review"},
                {"feature": "chat"},
            ],
            "top_prs": [
                {"pr_url": "https://github.com/o/r/pull/1", "est_cost_usd": 2.1},
            ],
            "errors": [],
        }
        msg = format_weekly_cto_ai_report(report)
        self.assertIn("Bigas AI usage", msg)
        self.assertIn("$12.3400", msg)
        self.assertIn("cursor:", msg)
        self.assertIn("- cto_autofix: 2", msg)
        self.assertIn("- cto_pr_review: 1", msg)
        self.assertIn("- chat: 1", msg)
        self.assertIn("chat:", msg)


class FetchAiUsageTests(unittest.TestCase):
    def test_aggregates_providers(self):
        class FakeProvider:
            name = "cursor"
            display_name = "Fake"

            def fetch_usage(self, *, start, end, feature_prefix=None):
                return [
                    UsageEvent(
                        provider="cursor",
                        source_id="bc-1",
                        started_at=start.isoformat(),
                        feature="cto_autofix",
                        model="composer-2.5",
                        input_tokens=1000,
                        output_tokens=100,
                        total_tokens=1100,
                        est_cost_usd=1.25,
                        meta={"pr_url": "https://github.com/o/r/pull/9"},
                    )
                ]

        now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
        report = fetch_ai_usage(
            days=7,
            provider="all",
            feature_prefix="cto_",
            providers=[FakeProvider()],
            now=now,
        )
        self.assertTrue(report["success"])
        self.assertEqual(report["totals"]["events"], 1)
        self.assertEqual(report["totals"]["activity_by_feature"], {"cto_autofix": 1})
        self.assertEqual(report["totals"]["est_cost_usd"], 1.25)
        self.assertEqual(report["totals"]["by_app"]["bigas"], 1.25)
        self.assertEqual(report["top_prs"][0]["pr_url"], "https://github.com/o/r/pull/9")

    def test_aggregates_app_and_model_tier(self):
        class FakeProvider:
            name = "llm_logs"
            display_name = "Fake"

            def fetch_usage(self, *, start, end, feature_prefix=None):
                return [
                    UsageEvent(
                        provider="llm_logs",
                        source_id="v1",
                        started_at=start.isoformat(),
                        feature="llm.living_analysis",
                        model="gemini-2.5-pro",
                        est_cost_usd=1.0,
                        meta={
                            "app": "vcfieldassistant",
                            "model_tier": "judgment",
                            "empty_response": True,
                            "empty_fallback": True,
                        },
                    ),
                    UsageEvent(
                        provider="llm_logs",
                        source_id="v2",
                        started_at=start.isoformat(),
                        feature="llm.living_analysis",
                        model="gemini-2.5-flash",
                        est_cost_usd=0.2,
                        meta={"app": "vcfieldassistant", "model_tier": "helper"},
                    ),
                ]

        now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
        report = fetch_ai_usage(
            days=7,
            provider="all",
            feature_prefix=None,
            providers=[FakeProvider()],
            now=now,
        )
        self.assertEqual(report["totals"]["by_app"]["vcfieldassistant"], 1.2)
        self.assertEqual(report["totals"]["by_model_tier"]["judgment"], 1.0)
        self.assertEqual(report["totals"]["by_model_tier"]["helper"], 0.2)
        self.assertEqual(report["totals"]["empty_response_events"], 1)
        self.assertEqual(report["totals"]["empty_fallback_events"], 1)
        msg = format_weekly_cto_ai_report(report)
        self.assertIn("vcfieldassistant", msg)
        self.assertIn("judgment", msg)

    @patch.dict("os.environ", {"BIGAS_LLM_USAGE_PROJECTS": ""}, clear=False)
    def test_default_usage_projects_include_vcfa(self):
        ids = _usage_project_ids("bigas-503008")
        self.assertEqual(ids, ["bigas-503008", "vcfieldassistant"])

    @patch.dict("os.environ", {"BIGAS_LLM_USAGE_PROJECTS": "a, b"}, clear=False)
    def test_usage_projects_env_override(self):
        self.assertEqual(_usage_project_ids("ignored"), ["a", "b"])

    def test_weekly_report_uses_totals_not_truncated_events(self):
        report = {
            "days": 7,
            "totals": {
                "est_cost_usd": 5.0,
                "events": 250,
                "activity_by_feature": {"chat": 200, "cto_pr_review": 50},
            },
            "events": [{"feature": "chat"}] * 200,
        }
        msg = format_weekly_cto_ai_report(report)
        self.assertIn("LLM calls:", msg)
        self.assertIn("- chat: 200", msg)
        self.assertIn("- cto_pr_review: 50", msg)

    def test_cfo_weekly_report_appends_analysis(self):
        report = {
            "days": 7,
            "totals": {"est_cost_usd": 3.0, "events": 2},
            "events": [{"model": "gemini-2.5-pro", "feature": "llm.living_analysis"}],
        }

        class FakeLlm:
            def complete(self, messages, **kwargs):
                return "Flash fallback waste is high. Watch Gemini 3 Flash."

        with patch(
            "bigas.llm.factory.get_llm_client",
            return_value=(FakeLlm(), "gemini-3.1-pro-preview"),
        ):
            msg = build_weekly_cfo_ai_report(report)
        self.assertIn("CFO: AI usage", msg)
        self.assertIn("CFO analysis", msg)
        self.assertIn("Gemini 3 Flash", msg)

    def test_cfo_prompt_may_challenge_pro_if_quality_holds(self):
        from bigas.resources.cto.usage.service import CFO_WEEKLY_ANALYSIS_INSTRUCTIONS

        text = CFO_WEEKLY_ANALYSIS_INSTRUCTIONS
        self.assertIn("challenge keeping Gemini Pro", text)
        self.assertIn("must not get worse", text)
        self.assertNotIn("that keep living-analysis judgment on Pro", text)

    def test_cfo_weekly_report_survives_llm_failure(self):
        report = {
            "days": 7,
            "totals": {"est_cost_usd": 1.0, "events": 1},
            "events": [],
        }
        with patch(
            "bigas.llm.factory.get_llm_client",
            side_effect=RuntimeError("no key"),
        ):
            msg = build_weekly_cfo_ai_report(report)
        self.assertIn("CFO: AI usage", msg)
        self.assertNotIn("CFO analysis", msg)

    def test_publish_weekly_goes_to_cfo_not_cto(self):
        with patch(
            "bigas.discord_webhook.post_long_to_discord"
        ) as post:
            publish_weekly_cfo_ai_report("hello cfo")
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(kwargs.get("chat_agent_id"), "cfo")
        self.assertEqual(args[1], "hello cfo")


class LlmLogsParseTests(unittest.TestCase):
    def test_parse_json_payload(self):
        entry = {
            "jsonPayload": {
                "event": "llm_usage",
                "feature": "cto_pr_review",
                "prompt_tokens": 10,
            }
        }
        data = _parse_log_payload(entry)
        self.assertEqual(data["feature"], "cto_pr_review")

    def test_parse_text_payload_json(self):
        entry = {
            "textPayload": (
                '{"attempt": "total", "event": "llm_usage", '
                '"feature": "cto_pr_review", "model": "gemini-pro-latest", '
                '"est_cost_usd": 0.18}'
            )
        }
        data = _parse_log_payload(entry)
        self.assertEqual(data["est_cost_usd"], 0.18)

    def test_parse_text_payload_python_logger_prefix(self):
        entry = {
            "textPayload": (
                'INFO:bigas.llm.logging_client:{"app": "bigas", '
                '"event": "llm_usage", "feature": "cto_pr_review", '
                '"model": "gemini-pro-latest", "est_cost_usd": 0.007594}'
            )
        }
        data = _parse_log_payload(entry)
        self.assertEqual(data["app"], "bigas")
        self.assertEqual(data["est_cost_usd"], 0.007594)

    @patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "demo-proj"}, clear=False)
    def test_llm_logs_is_configured(self):
        self.assertTrue(CloudRunLlmUsageProvider.is_configured())

    @patch.dict(
        "os.environ",
        {
            "GOOGLE_CLOUD_PROJECT": "bigas-503008",
            "BIGAS_LLM_USAGE_PROJECTS": "bigas-503008",
        },
        clear=False,
    )
    def test_fetch_raises_when_logging_api_fails(self):
        provider = CloudRunLlmUsageProvider()
        with patch.object(
            provider, "_list_entries", side_effect=RuntimeError("403")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                provider.fetch_usage(
                    start=datetime(2026, 8, 17, tzinfo=timezone.utc),
                    end=datetime(2026, 8, 24, tzinfo=timezone.utc),
                )
        self.assertIn("bigas-503008", str(ctx.exception))

    @patch.dict(
        "os.environ",
        {
            "GOOGLE_CLOUD_PROJECT": "bigas-503008",
            "BIGAS_LLM_USAGE_PROJECTS": "bigas-503008",
        },
        clear=False,
    )
    def test_fetch_reestimates_undercounted_thinking(self):
        provider = CloudRunLlmUsageProvider()
        entry = {
            "timestamp": "2026-08-21T12:00:00Z",
            "insertId": "abc",
            "textPayload": (
                'INFO:bigas.llm.logging_client:{"event": "llm_usage", '
                '"feature": "cto_pr_review", "model": "gemini-pro-latest", '
                '"prompt_tokens": 3131, "candidates_tokens": 286, '
                '"thoughts_tokens": null, "total_tokens": 10737, '
                '"est_cost_usd": 0.009694}'
            ),
        }
        with patch.object(
            provider, "_list_entries", return_value={"entries": [entry]}
        ):
            events = provider.fetch_usage(
                start=datetime(2026, 8, 17, tzinfo=timezone.utc),
                end=datetime(2026, 8, 24, tzinfo=timezone.utc),
            )
        self.assertEqual(len(events), 1)
        billed_out = 10737 - 3131
        expected = round(3131 / 1_000_000.0 * 2.00 + billed_out / 1_000_000.0 * 12.00, 6)
        self.assertEqual(events[0].est_cost_usd, expected)


class CursorProviderTests(unittest.TestCase):
    @patch.dict("os.environ", {"CURSOR_API_KEY": "test-key", "BIGAS_CTO_AUTOFIX_MODEL": "composer-2.5"})
    def test_fetch_filters_bigas_autofix_and_window(self):
        client = MagicMock()
        client.list_agents.return_value = {
            "items": [
                {
                    "id": "bc-new",
                    "name": "Bigas autofix acme/app#12 (1/5)",
                    "createdAt": "2026-08-09T10:00:00.000Z",
                    "url": "https://cursor.com/agents/bc-new",
                    "latestRunId": "run-1",
                },
                {
                    "id": "bc-other",
                    "name": "Manual agent",
                    "createdAt": "2026-08-09T11:00:00.000Z",
                },
                {
                    "id": "bc-old",
                    "name": "Bigas autofix acme/app#1 (1/5)",
                    "createdAt": "2026-07-01T10:00:00.000Z",
                },
            ]
        }
        client.get_usage.return_value = {
            "totalUsage": {
                "inputTokens": 1000,
                "outputTokens": 200,
                "cacheWriteTokens": 0,
                "cacheReadTokens": 500,
                "totalTokens": 1700,
            }
        }

        provider = CursorCloudAgentUsageProvider(api_key="test-key")
        provider._client = client
        start = datetime(2026, 8, 3, tzinfo=timezone.utc)
        end = datetime(2026, 8, 10, tzinfo=timezone.utc)
        events = provider.fetch_usage(start=start, end=end, feature_prefix="cto_")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_id, "bc-new")
        self.assertEqual(events[0].meta.get("repo"), "acme/app")
        self.assertIsNotNone(events[0].est_cost_usd)


if __name__ == "__main__":
    unittest.main()
