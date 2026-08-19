"""Marketing NL query parsing and MCP-facing flags."""
from __future__ import annotations

from bigas.resources.marketing.marketing_llm_service import MarketingLLMService
from bigas.resources.marketing.utils import extract_json_object, request_flag


def test_extract_json_object_from_markdown_fence():
    raw = """Here you go:
```json
{"metrics": ["sessions"], "dimensions": ["country"]}
```
"""
    parsed = extract_json_object(raw)
    assert parsed["metrics"] == ["sessions"]
    assert parsed["dimensions"] == ["country"]


def test_extract_json_object_from_surrounding_text():
    raw = 'Sure. {"metrics": ["activeUsers"], "dimensions": ["date"]} thanks.'
    parsed = extract_json_object(raw)
    assert parsed["metrics"] == ["activeUsers"]


def test_extract_json_object_empty():
    assert extract_json_object("") == {}
    assert extract_json_object("no json here") == {}


def test_request_flag_defaults_and_strings():
    assert request_flag({}, "post_to_discord", False) is False
    assert request_flag({"post_to_discord": True}, "post_to_discord", False) is True
    assert request_flag({"post_to_discord": "false"}, "post_to_discord", True) is False
    assert request_flag({"post_to_discord": "true"}, "post_to_discord", False) is True


class _FakeLLM:
    def __init__(self, text):
        self.text = text

    def complete(self, **kwargs):
        return self.text


def test_parse_query_accepts_gemini_markdown_json(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.marketing.marketing_llm_service.get_llm_client",
        lambda **k: (_FakeLLM('```json\n{"metrics": ["sessions"], "date_range": {"start_date": "30daysAgo", "end_date": "today"}}\n```'), "gemini-test"),
    )
    svc = MarketingLLMService("sk-test")
    out = svc.parse_query("Where does traffic come from?")
    assert out["metrics"] == ["sessions"]
    assert out["dimensions"] == ["date"]


def test_parse_query_raises_when_unparseable(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.marketing.marketing_llm_service.get_llm_client",
        lambda **k: (_FakeLLM("I cannot produce JSON."), "gemini-test"),
    )
    svc = MarketingLLMService("sk-test")
    try:
        svc.parse_query("How are we doing?")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "valid JSON" in str(exc)
