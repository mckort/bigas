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
                "events": 3,
                "by_provider": {"cursor": 8.1, "llm_logs": 4.24},
                "by_feature": {"cto_autofix": 8.1, "cto_pr_review": 4.24},
            },
            "events": [
                {"feature": "cto_autofix"},
                {"feature": "cto_autofix"},
                {"feature": "cto_pr_review"},
            ],
            "top_prs": [
                {"pr_url": "https://github.com/o/r/pull/1", "est_cost_usd": 2.1},
            ],
            "errors": [],
        }
        msg = format_weekly_cto_ai_report(report)
        self.assertIn("CTO AI usage", msg)
        self.assertIn("$12.3400", msg)
        self.assertIn("cursor:", msg)
        self.assertIn("2 Cursor autofix", msg)


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
        self.assertEqual(report["totals"]["est_cost_usd"], 1.25)
        self.assertEqual(report["top_prs"][0]["pr_url"], "https://github.com/o/r/pull/9")


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

    @patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "demo-proj"}, clear=False)
    def test_llm_logs_is_configured(self):
        self.assertTrue(CloudRunLlmUsageProvider.is_configured())


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
