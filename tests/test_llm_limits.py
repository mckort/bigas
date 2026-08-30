"""Tests for model-aware LLM output token limits."""
from __future__ import annotations

import unittest

from bigas.llm.limits import cap_output_tokens, model_output_token_limit


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


if __name__ == "__main__":
    unittest.main()
