"""Unit tests for LLM token usage parsing, cost estimates, and usage logging."""
from __future__ import annotations

import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bigas.llm.client import LLMClient
from bigas.llm.completion import LLMCompletion
from bigas.llm.factory import get_llm_client
from bigas.llm.logging_client import LoggingLLMClient
from bigas.llm.usage import (
    TokenUsage,
    estimate_cost_usd,
    resolve_model_price_usd_per_mtok,
    usage_from_mapping,
    usage_log_payload,
)


class TokenUsageTests(unittest.TestCase):
    def test_usage_from_gemini_snake_case(self):
        usage = usage_from_mapping(
            {
                "prompt_token_count": 1000,
                "candidates_token_count": 200,
                "thoughts_token_count": 50,
                "total_token_count": 1250,
            }
        )
        self.assertEqual(usage.prompt_tokens, 1000)
        self.assertEqual(usage.candidates_tokens, 200)
        self.assertEqual(usage.thoughts_tokens, 50)
        self.assertEqual(usage.total_tokens, 1250)

    def test_usage_from_openai(self):
        usage = usage_from_mapping(
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )
        self.assertEqual(usage.prompt_tokens, 10)
        self.assertEqual(usage.candidates_tokens, 5)
        self.assertEqual(usage.total_tokens, 15)

    def test_merge_sums(self):
        a = TokenUsage(prompt_tokens=10, candidates_tokens=2, thoughts_tokens=1, total_tokens=13)
        b = TokenUsage(prompt_tokens=5, candidates_tokens=3, thoughts_tokens=None, total_tokens=8)
        m = a.merge(b)
        self.assertEqual(m.prompt_tokens, 15)
        self.assertEqual(m.candidates_tokens, 5)
        self.assertEqual(m.thoughts_tokens, 1)
        self.assertEqual(m.total_tokens, 21)

    def test_pro_latest_price(self):
        self.assertEqual(resolve_model_price_usd_per_mtok("gemini-pro-latest"), (2.00, 12.00))

    def test_flash_estimate_does_not_double_count_thoughts(self):
        # Realistic Gemini shape: candidates already includes thoughts.
        usage = TokenUsage(
            prompt_tokens=1_000_000,
            candidates_tokens=1_000_000,
            thoughts_tokens=1_000_000,
            total_tokens=2_000_000,
        )
        # gemini-2.5-flash: $0.30 in + $2.50 out on 1M billed output tokens
        cost = estimate_cost_usd("gemini-2.5-flash", usage)
        self.assertEqual(cost, 0.30 + 2.50)

    def test_thoughts_only_fallback_when_candidates_missing(self):
        usage = TokenUsage(
            prompt_tokens=1_000_000,
            candidates_tokens=None,
            thoughts_tokens=1_000_000,
            total_tokens=None,
        )
        cost = estimate_cost_usd("gemini-2.5-flash", usage)
        self.assertEqual(cost, 0.30 + 2.50)

    def test_usage_log_payload(self):
        usage = TokenUsage(prompt_tokens=1000, candidates_tokens=100, total_tokens=1100)
        payload = usage_log_payload(
            feature="cto_pr_review",
            model="gemini-pro-latest",
            usage=usage,
            extra={"phase": "initial", "attempt": 0},
        )
        self.assertEqual(payload["event"], "llm_usage")
        self.assertEqual(payload["feature"], "cto_pr_review")
        self.assertEqual(payload["phase"], "initial")
        self.assertTrue(payload["cost_estimate"])
        self.assertIn("est_cost_usd", payload)


class LoggingLLMClientTests(unittest.TestCase):
    def _inner(self, usage: TokenUsage) -> MagicMock:
        inner = MagicMock()
        inner.complete_detailed.return_value = LLMCompletion(
            text="hello",
            finish_reason="STOP",
            usage=usage,
        )
        inner.model_name = "gemini-2.5-flash"
        return inner

    def test_complete_logs_once(self):
        usage = TokenUsage(prompt_tokens=1000, candidates_tokens=100, total_tokens=1100)
        client = LoggingLLMClient(self._inner(usage), feature="chat", model="gemini-2.5-flash")
        with self.assertLogs("bigas.llm.logging_client", level=logging.INFO) as captured:
            text = client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(text, "hello")
        payloads = [json.loads(r.getMessage()) for r in captured.records]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["event"], "llm_usage")
        self.assertEqual(payloads[0]["feature"], "chat")
        self.assertIn("est_cost_usd", payloads[0])
        self.assertNotIn("attempt", payloads[0])

    def test_skips_empty_usage(self):
        client = LoggingLLMClient(self._inner(TokenUsage()), feature="chat", model="gemini-2.5-flash")
        with patch("bigas.llm.logging_client.logger.info") as info:
            client.complete_detailed([{"role": "user", "content": "hi"}])
        info.assert_not_called()

    def test_record_openai_response(self):
        inner = MagicMock()
        client = LoggingLLMClient(inner, feature="chat", model="gpt-4.1-mini")
        resp = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=5, total_tokens=25)
        )
        with self.assertLogs("bigas.llm.logging_client", level=logging.INFO) as captured:
            client.record_openai_response(resp)
        payload = json.loads(captured.records[0].getMessage())
        self.assertEqual(payload["feature"], "chat")
        self.assertEqual(payload["prompt_tokens"], 20)
        self.assertEqual(payload["candidates_tokens"], 5)

    def test_log_failure_does_not_raise(self):
        usage = TokenUsage(prompt_tokens=10, candidates_tokens=1, total_tokens=11)
        client = LoggingLLMClient(self._inner(usage), feature="chat", model="gemini-2.5-flash")
        with patch("bigas.llm.logging_client.usage_log_payload", side_effect=RuntimeError("boom")):
            text = client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(text, "hello")

    def test_proxies_unknown_attributes(self):
        inner = MagicMock()
        inner.complete_detailed.return_value = LLMCompletion(text="ok", finish_reason="STOP")
        inner.stream = MagicMock(return_value="streamed")
        client = LoggingLLMClient(inner, feature="chat", model="gemini-2.5-flash")
        self.assertIs(client.stream, inner.stream)
        self.assertEqual(client.stream(), "streamed")

    def test_is_llm_client_instance(self):
        inner = MagicMock()
        inner.complete_detailed.return_value = LLMCompletion(text="ok", finish_reason="STOP")
        client = LoggingLLMClient(inner, feature="chat", model="gemini-2.5-flash")
        self.assertIsInstance(client, LLMClient)

    @patch("bigas.llm.factory.GeminiLLMClient")
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False)
    def test_factory_wraps_client(self, mock_cls):
        mock_cls.return_value = MagicMock()
        client, model = get_llm_client(feature="chat", explicit_model="gemini-2.5-flash")
        self.assertIsInstance(client, LoggingLLMClient)
        self.assertIsInstance(client, LLMClient)
        self.assertEqual(client._feature, "chat")
        self.assertEqual(model, "gemini-2.5-flash")


if __name__ == "__main__":
    unittest.main()
