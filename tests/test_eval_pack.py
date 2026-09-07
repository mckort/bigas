"""Tests for eval pack loading, heading slice, templates, and web research."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bigas.eval.base import reject_customer_identifiers
from bigas.eval.pack import (
    extract_heading_prompts,
    flatten_step_output,
    heading_matches,
    interpolate,
    load_pack_file,
    pack_from_mapping,
    render_step_prompt,
)
from bigas.eval.research import html_to_text, run_web_research


SAMPLE_PROMPTS = """
# Analys-promptar

### 1.1 Company overview & classification

```
Write the overview.
```

### 1.10 Extrahera / filtrera konkurrentnamn

```
Do not pick this for 1.1.
```

### 1.4 Competitive analysis

```
Write three buckets.
```

### 3.7 Skriv landscape-tabellerna

```
Output ONLY JSON landscape tables.
```

### 4.1 Klassificera bolaget

```
Classify this portfolio company for targeted investor web research.
```
"""


class PackSchemaTests(unittest.TestCase):
    def test_rejects_customer_ids_in_pack_fixture(self):
        with self.assertRaises(ValueError) as ctx:
            pack_from_mapping(
                {
                    "id": "demo",
                    "fixture": {"companyId": "cust-1", "url": "https://example.com"},
                    "steps": [{"id": "one", "prompt": "Hi"}],
                }
            )
        self.assertIn("companyId", str(ctx.exception))

    def test_requires_prompt_or_prompt_from(self):
        with self.assertRaises(ValueError):
            pack_from_mapping({"id": "demo", "steps": [{"id": "one"}]})

    def test_load_pack_file_inline_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.pack.yaml"
            path.write_text(
                "id: demo\n"
                "fixture:\n  company: Acme\n  url: https://example.com\n"
                "steps:\n  - id: one\n    prompt: Hello {{fixture.company}}\n",
                encoding="utf-8",
            )
            pack = load_pack_file(path)
            self.assertEqual(pack.id, "demo")
            self.assertEqual(pack.steps[0].resolved_prompt, "Hello {{fixture.company}}")


class HeadingSliceTests(unittest.TestCase):
    def test_classify_alias_extracts_fenced_block(self):
        text = extract_heading_prompts(SAMPLE_PROMPTS, "classify")
        self.assertIn("Classify this portfolio company", text)
        self.assertNotIn("Do not pick this", text)

    def test_numbered_1_1_does_not_match_1_10(self):
        self.assertTrue(heading_matches("1.1 Company overview & classification", "1.1"))
        self.assertFalse(heading_matches("1.10 Extrahera / filtrera konkurrentnamn", "1.1"))
        text = extract_heading_prompts(SAMPLE_PROMPTS, "1.1")
        self.assertIn("Write the overview.", text)
        self.assertNotIn("Do not pick this", text)

    def test_competitive_landscape_concatenates_aliases(self):
        text = extract_heading_prompts(SAMPLE_PROMPTS, "competitive-landscape")
        self.assertIn("Write three buckets.", text)
        self.assertIn("Output ONLY JSON landscape tables.", text)


class TemplateTests(unittest.TestCase):
    def test_interpolate_and_flatten_json_fields(self):
        step = flatten_step_output('{"category":"investor workspace","customerSegment":"VCs"}')
        rendered = interpolate(
            "{{steps.classify.category}} / {{steps.classify.customer_segment}}",
            {"steps": {"classify": step}},
        )
        self.assertEqual(rendered, "investor workspace / VCs")

    def test_render_step_appends_input(self):
        pack = pack_from_mapping(
            {
                "id": "demo",
                "steps": [
                    {
                        "id": "one",
                        "prompt": "Task",
                        "input": "PAGE:\n{{fixture.page}}",
                    }
                ],
            }
        )
        text = render_step_prompt(
            pack.steps[0],
            {"fixture": {"page": "Hello site"}},
        )
        self.assertIn("Task", text)
        self.assertIn("Hello site", text)


class ResearchTests(unittest.TestCase):
    def test_html_to_text_strips_tags(self):
        text = html_to_text("<html><script>x</script><p>Hello <b>world</b></p></html>")
        self.assertIn("Hello", text)
        self.assertIn("world", text)
        self.assertNotIn("script", text.lower())

    def test_web_research_without_key_is_empty(self):
        pack = pack_from_mapping(
            {
                "id": "demo",
                "steps": [
                    {
                        "id": "one",
                        "prompt": "Go",
                        "research": {
                            "provider": "web",
                            "queries": ["{{steps.classify.category}} alternatives"],
                        },
                    }
                ],
            }
        )
        with patch.dict("os.environ", {"TAVILY_API_KEY": "", "EVAL_TAVILY_API_KEY": ""}, clear=False):
            snippets = run_web_research(
                pack.steps[0].research,
                {"steps": {"classify": {"category": "investor workspace"}}},
            )
        self.assertEqual(snippets, "")

    @patch("bigas.eval.research.fetch_page_text", return_value="Competitor homepage.")
    @patch("bigas.eval.research.web_search")
    def test_web_research_injects_snippets_and_pages(self, mock_search, _mock_fetch):
        mock_search.return_value = [
            {
                "title": "Alt",
                "url": "https://alt.example",
                "content": "An alternative product.",
            }
        ]
        pack = pack_from_mapping(
            {
                "id": "demo",
                "steps": [
                    {
                        "id": "one",
                        "prompt": "Go",
                        "research": {
                            "provider": "web",
                            "queries": ["investor workspace alternatives"],
                            "fetch_urls_from": "snippets",
                            "max_pages": 1,
                        },
                    }
                ],
            }
        )
        snippets = run_web_research(pack.steps[0].research, {})
        self.assertIn("An alternative product.", snippets)
        self.assertIn("PAGE: https://alt.example", snippets)
        self.assertIn("Competitor homepage.", snippets)


class IsolationTests(unittest.TestCase):
    def test_reject_still_blocks_workspace(self):
        with self.assertRaises(ValueError):
            reject_customer_identifiers({"workspaceId": "ws-1"})


if __name__ == "__main__":
    unittest.main()
