"""Tests for scripts/setup.py env generation."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from setup import MVP_DEFAULTS, build_env_lines, write_env_file  # noqa: E402


class TestSetupScript(unittest.TestCase):
    def test_mvp_defaults_in_output(self):
        lines = build_env_lines({"llm_provider": "gemini", "gemini_api_key": "test-key"})
        text = "\n".join(lines)
        for key, value in MVP_DEFAULTS.items():
            self.assertIn(f"{key}={value}", text)
        self.assertIn("GEMINI_API_KEY=test-key", text)
        self.assertIn("CHAT_STORAGE_MODE=memory", text)

    def test_openai_provider(self):
        lines = build_env_lines({"llm_provider": "openai", "openai_api_key": "sk-test"})
        text = "\n".join(lines)
        self.assertIn("OPENAI_API_KEY=sk-test", text)
        self.assertNotIn("GEMINI_API_KEY=", text)

    def test_optional_github_section(self):
        lines = build_env_lines(
            {
                "llm_provider": "gemini",
                "gemini_api_key": "k",
                "enable_github": True,
                "github_token": "ghp_test",
            }
        )
        text = "\n".join(lines)
        self.assertIn("GITHUB_TOKEN=ghp_test", text)
        self.assertIn("GitHub (CTO agent", text)

    def test_write_env_file_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            write_env_file(path, ["A=1"], overwrite=True)
            with self.assertRaises(FileExistsError):
                write_env_file(path, ["A=2"], overwrite=False)


if __name__ == "__main__":
    unittest.main()
