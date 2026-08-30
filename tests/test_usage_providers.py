"""Tests for CTO AI usage pricing, Discord formatting, and providers."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bigas.providers.usage.base import UsageEvent, usage_provider_enabled
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
    enrich_with_prior_period,
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
        self.assertIn("Bigas AI + cloud usage", msg)
        self.assertIn("$12.34", msg)
        self.assertIn("Top drivers:", msg)
        self.assertIn("By area:", msg)
        self.assertIn("Engineering (PR + autofix)", msg)
        self.assertIn("cursor:", msg)
        self.assertIn("Top features", msg)
        self.assertIn("cto_autofix:", msg)
        self.assertIn("cto_pr_review:", msg)
        self.assertIn("chat:", msg)
        self.assertIn("/call)", msg)

    def test_format_flags_gcp_billing_blocked(self):
        report = {
            "days": 7,
            "totals": {"est_cost_usd": 1.0, "invoice_cost_usd": 0.0, "events": 1},
            "errors": [
                {
                    "provider": "gcp_billing",
                    "error": "Cloud Billing export table not found in bigas-503008.gcp_billing.",
                }
            ],
        }
        msg = format_weekly_cto_ai_report(report)
        self.assertIn("GCP invoice: unavailable", msg)
        self.assertIn("export table not found", msg)
        self.assertNotIn("Provider errors:", msg)

    def test_format_includes_wow_when_prior_present(self):
        report = {
            "days": 7,
            "totals": {"est_cost_usd": 12.0, "events": 2, "by_feature": {}},
            "prior_period": {"days": 7, "totals": {"est_cost_usd": 10.0}},
        }
        msg = format_weekly_cto_ai_report(report)
        self.assertIn("vs prior 7d:", msg)
        self.assertIn("+20%", msg)

    def test_enrich_with_prior_period(self):
        class FakeProvider:
            name = "cursor"
            display_name = "Fake"

            def fetch_usage(self, *, start, end, feature_prefix=None):
                # Prior window (before 2026-08-03) → cheaper; current window higher.
                cost = 1.0 if end.day <= 3 else 2.0
                return [
                    UsageEvent(
                        provider="cursor",
                        source_id="bc-1",
                        started_at=start.isoformat(),
                        feature="cto_autofix",
                        model="composer-2.5",
                        est_cost_usd=cost,
                    )
                ]

        now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
        current = fetch_ai_usage(
            days=7,
            provider="all",
            providers=[FakeProvider()],
            now=now,
        )
        enriched = enrich_with_prior_period(current, providers=[FakeProvider()])
        self.assertIn("prior_period", enriched)
        self.assertEqual(enriched["totals"]["est_cost_usd"], 2.0)
        self.assertEqual(enriched["prior_period"]["totals"]["est_cost_usd"], 1.0)
        msg = format_weekly_cto_ai_report(enriched)
        self.assertIn("vs prior 7d:", msg)
        self.assertIn("+100%", msg)


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
        self.assertEqual(report["totals"]["invoice_cost_usd"], 0.0)
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
                "by_feature": {"chat": 3.0, "cto_pr_review": 2.0},
                "activity_by_feature": {"chat": 200, "cto_pr_review": 50},
            },
            "events": [{"feature": "chat"}] * 200,
        }
        msg = format_weekly_cto_ai_report(report)
        self.assertIn("Top features", msg)
        self.assertIn("chat:", msg)
        self.assertIn("200", msg)
        self.assertIn("cto_pr_review:", msg)
        self.assertIn("50", msg)

    def test_cfo_weekly_report_appends_analysis(self):
        report = {
            "days": 7,
            "totals": {"est_cost_usd": 3.0, "events": 2},
            "events": [{"model": "gemini-2.5-pro", "feature": "llm.living_analysis"}],
        }

        class FakeLlm:
            def complete(self, messages, **kwargs):
                return "### Drivers\n- Living analysis led spend.\n### Savings\n- Trim cadence.\n### Model\n- Stay on Pro for judgment."

        with patch(
            "bigas.llm.factory.get_llm_client",
            return_value=(FakeLlm(), "gemini-3.1-pro-preview"),
        ):
            msg = build_weekly_cfo_ai_report(report, include_prior_period=False)
        self.assertIn("CFO: AI + cloud usage", msg)
        self.assertIn("CFO analysis", msg)
        self.assertIn("### Drivers", msg)

    def test_cfo_prompt_may_challenge_pro_if_quality_holds(self):
        from bigas.resources.cto.usage.service import CFO_WEEKLY_ANALYSIS_INSTRUCTIONS

        text = CFO_WEEKLY_ANALYSIS_INSTRUCTIONS
        self.assertIn("challenge keeping Gemini Pro", text)
        self.assertIn("must not get worse", text)
        self.assertIn("### Drivers", text)
        self.assertIn("### Savings", text)
        self.assertIn("### Model", text)
        self.assertIn("180 words", text)

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
            msg = build_weekly_cfo_ai_report(report, include_prior_period=False)
        self.assertIn("CFO: AI + cloud usage", msg)
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

    @patch.dict(
        "os.environ",
        {"GOOGLE_CLOUD_PROJECT": "demo-proj", "BIGAS_USAGE_PROVIDERS": "llm_logs"},
        clear=False,
    )
    def test_llm_logs_is_configured(self):
        self.assertTrue(CloudRunLlmUsageProvider.is_configured())

    @patch.dict(
        "os.environ",
        {"GOOGLE_CLOUD_PROJECT": "demo-proj", "BIGAS_USAGE_PROVIDERS": ""},
        clear=False,
    )
    def test_llm_logs_off_unless_listed(self):
        self.assertFalse(CloudRunLlmUsageProvider.is_configured())

    def test_usage_provider_enabled_parses_list(self):
        with patch.dict(
            "os.environ",
            {"BIGAS_USAGE_PROVIDERS": "cursor, llm_logs"},
            clear=False,
        ):
            self.assertTrue(usage_provider_enabled("llm_logs"))
            self.assertTrue(usage_provider_enabled("CURSOR"))
            self.assertFalse(usage_provider_enabled("tavily"))

    @patch.dict(
        "os.environ",
        {
            "GOOGLE_CLOUD_PROJECT": "demo-proj",
            "CURSOR_API_KEY": "k",
            "BIGAS_USAGE_PROVIDERS": "cursor,llm_logs",
        },
        clear=False,
    )
    def test_only_listed_usage_providers_configure(self):
        from bigas.providers.usage.gcp_billing import GcpBillingUsageProvider
        from bigas.providers.usage.tavily import TavilyUsageProvider

        self.assertTrue(CloudRunLlmUsageProvider.is_configured())
        self.assertTrue(CursorCloudAgentUsageProvider.is_configured())
        self.assertFalse(GcpBillingUsageProvider.is_configured())
        self.assertFalse(TavilyUsageProvider.is_configured())

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
        self.assertEqual(events[0].output_tokens, billed_out)

    @patch.dict(
        "os.environ",
        {
            "GOOGLE_CLOUD_PROJECT": "bigas-503008",
            "BIGAS_LLM_USAGE_PROJECTS": "bigas-503008,vcfieldassistant",
        },
        clear=False,
    )
    def test_fetch_raises_when_any_project_fails(self):
        provider = CloudRunLlmUsageProvider()
        good_entry = {
            "timestamp": "2026-08-21T12:00:00Z",
            "insertId": "abc",
            "jsonPayload": {
                "event": "llm_usage",
                "feature": "chat",
                "model": "gemini-2.5-flash",
                "prompt_tokens": 10,
                "candidates_tokens": 5,
                "total_tokens": 15,
            },
        }

        def list_entries(body):
            project = body["resourceNames"][0]
            if project == "projects/vcfieldassistant":
                raise RuntimeError("403 forbidden")
            return {"entries": [good_entry]}

        with patch.object(provider, "_list_entries", side_effect=list_entries):
            with self.assertRaises(RuntimeError) as ctx:
                provider.fetch_usage(
                    start=datetime(2026, 8, 17, tzinfo=timezone.utc),
                    end=datetime(2026, 8, 24, tzinfo=timezone.utc),
                )
        self.assertIn("vcfieldassistant", str(ctx.exception))
        self.assertIn("403 forbidden", str(ctx.exception))


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


class GcpBillingTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {"GOOGLE_CLOUD_PROJECT": "demo-proj", "BIGAS_USAGE_PROVIDERS": "gcp_billing"},
        clear=False,
    )
    def test_is_configured_when_listed(self):
        from bigas.providers.usage.gcp_billing import GcpBillingUsageProvider

        self.assertTrue(GcpBillingUsageProvider.is_configured())

    def test_service_feature_mapping(self):
        from bigas.providers.usage.gcp_billing import service_feature

        self.assertEqual(service_feature("Cloud Firestore"), "gcp.firestore")
        self.assertEqual(service_feature("Cloud Run"), "gcp.cloud_run")
        self.assertEqual(service_feature("Vertex AI"), "gcp.gemini_invoice")
        self.assertEqual(service_feature("Generative Language API"), "gcp.gemini_invoice")

    def test_rows_to_events(self):
        from bigas.providers.usage.gcp_billing import _rows_to_events

        events = _rows_to_events(
            [
                {
                    "project_id": "bigas-503008",
                    "service": "Cloud Firestore",
                    "net_cost": "1.25",
                },
                {
                    "project_id": "vcfieldassistant",
                    "service": "Cloud Run",
                    "net_cost": "0.4",
                },
            ],
            started_at="2026-08-17T00:00:00+00:00",
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].feature, "gcp.firestore")
        self.assertFalse(events[0].cost_estimate)
        self.assertEqual(events[0].meta["app"], "bigas")
        self.assertEqual(events[1].meta["app"], "vcfieldassistant")


class TavilyUsageTests(unittest.TestCase):
    def test_merge_skips_gemini_and_keeps_tavily_features(self):
        from bigas.providers.usage.tavily import events_for_day, merge_tavily_shards

        shards = [
            {
                "byProvider": {"gemini": 6.32, "tavily": 6.9},
                "byFeature": {
                    "llm.living_analysis": 6.32,
                    "tavily.company_news_weekly": 6.9,
                },
            }
        ]
        by_feature, leftover = merge_tavily_shards(shards)
        self.assertEqual(by_feature, {"tavily.company_news_weekly": 6.9})
        self.assertEqual(leftover, 0.0)
        events = events_for_day("2026-08-19", shards, project="vcfieldassistant")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].provider, "tavily")
        self.assertEqual(events[0].est_cost_usd, 6.9)

    def test_leftover_provider_total_becomes_tavily_search(self):
        from bigas.providers.usage.tavily import events_for_day

        shards = [{"byProvider": {"tavily": 0.04}, "byFeature": {}}]
        events = events_for_day("2026-08-18", shards, project="vcfieldassistant")
        self.assertEqual(events[0].feature, "tavily.search")
        self.assertEqual(events[0].est_cost_usd, 0.04)


class InvoiceVsListPriceTests(unittest.TestCase):
    def test_gcp_invoice_is_not_in_list_price_total(self):
        class FakeLlm:
            name = "llm_logs"

            def fetch_usage(self, *, start, end, feature_prefix=None):
                return [
                    UsageEvent(
                        provider="llm_logs",
                        source_id="a",
                        started_at=start.isoformat(),
                        feature="cto_pr_review",
                        est_cost_usd=10.0,
                        cost_estimate=True,
                    )
                ]

        class FakeGcp:
            name = "gcp_billing"

            def fetch_usage(self, *, start, end, feature_prefix=None):
                return [
                    UsageEvent(
                        provider="gcp_billing",
                        source_id="b",
                        started_at=start.isoformat(),
                        feature="gcp.firestore",
                        est_cost_usd=2.0,
                        cost_estimate=False,
                        meta={"cost_kind": "invoice", "app": "bigas"},
                    )
                ]

        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        report = fetch_ai_usage(
            days=7,
            provider="all",
            providers=[FakeLlm(), FakeGcp()],
            now=now,
        )
        self.assertEqual(report["totals"]["est_cost_usd"], 10.0)
        self.assertEqual(report["totals"]["invoice_cost_usd"], 2.0)
        msg = format_weekly_cto_ai_report(report)
        self.assertIn("GCP invoice", msg)
        self.assertIn("gcp.firestore", msg)


if __name__ == "__main__":
    unittest.main()
