"""Tests for model-aware LLM output token limits."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bigas.llm.limits import (
    cap_output_tokens,
    model_output_token_limit,
    supports_temperature,
    uses_max_completion_tokens,
)
from bigas.llm.openai_client import OpenAILLMClient


class ModelOutputTokenLimitTests(unittest.TestCase):
    def test_gemini_models_are_not_capped(self):
        self.assertIsNone(model_output_token_limit("gemini-3.1-pro-preview"))
        self.assertEqual(
            cap_output_tokens("gemini-3.1-pro-preview", 8192),
            8192,
        )

    def test_legacy_gpt4_capped_at_4096(self):
        self.assertEqual(model_output_token_limit("gpt-4"), 4096)
        self.assertEqual(cap_output_tokens("gpt-4", 8192), 4096)
        self.assertEqual(cap_output_tokens("gpt-4-turbo", 8192), 4096)

    def test_newer_gpt4o_variants_allow_8192(self):
        self.assertEqual(model_output_token_limit("gpt-4o-2024-08-06"), 16_384)
        self.assertEqual(cap_output_tokens("gpt-4o-2024-08-06", 8192), 8192)
        self.assertEqual(model_output_token_limit("gpt-4o-mini"), 16_384)

    def test_bare_gpt4o_stays_conservative(self):
        self.assertEqual(model_output_token_limit("gpt-4o"), 4096)
        self.assertEqual(cap_output_tokens("gpt-4o", 8192), 4096)

    def test_newer_openai_models_use_max_completion_tokens(self):
        self.assertTrue(uses_max_completion_tokens("gpt-6-astra"))
        self.assertTrue(uses_max_completion_tokens("gpt-5.6-sol"))
        self.assertTrue(uses_max_completion_tokens("o3-mini"))
        self.assertFalse(uses_max_completion_tokens("gpt-4o"))
        self.assertFalse(uses_max_completion_tokens("gemini-3.1-pro-preview"))

    def test_reasoning_models_reject_custom_temperature(self):
        self.assertFalse(supports_temperature("o1"))
        self.assertFalse(supports_temperature("o3-mini"))
        self.assertTrue(supports_temperature("gpt-4o"))

    def _complete_captured(self, model_id: str) -> dict:
        captured: dict = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="ok", tool_calls=None),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                )

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        with patch("bigas.llm.openai_client.openai.OpenAI", FakeOpenAI):
            client = OpenAILLMClient(api_key="test-key", model=model_id)
            client.complete_detailed(
                [{"role": "user", "content": "hi"}],
                max_tokens=800,
                temperature=0.2,
            )
        return captured

    def test_gpt4o_request_keeps_max_tokens(self):
        captured = self._complete_captured("gpt-4o")
        self.assertEqual(captured["max_tokens"], 800)
        self.assertNotIn("max_completion_tokens", captured)

    def test_gpt6_astra_request_uses_max_completion_tokens(self):
        captured = self._complete_captured("gpt-6-astra")
        self.assertEqual(captured.get("extra_body"), {"max_completion_tokens": 800})
        self.assertNotIn("max_tokens", captured)
        self.assertNotIn("max_completion_tokens", captured)

    def test_o3_request_omits_temperature(self):
        captured = self._complete_captured("o3-mini")
        self.assertEqual(captured.get("extra_body"), {"max_completion_tokens": 800})
        self.assertNotIn("temperature", captured)


if __name__ == "__main__":
    unittest.main()
