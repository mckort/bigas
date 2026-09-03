"""Marketing NL query parsing and MCP-facing flags."""
from __future__ import annotations

import sys

import pytest

from bigas.resources.marketing.marketing_llm_service import MarketingLLMService
from bigas.resources.marketing.utils import (
    STRATEGY_ANALYTICS_REJECT,
    extract_json_object,
    is_strategy_analytics_question,
    request_flag,
)


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
    assert extract_json_object("no json here") is None


def test_extract_json_object_valid_empty_object():
    assert extract_json_object("{}") == {}


def test_extract_json_object_fallback_searches_original_text():
    raw = """Here:
```python
print("not json")
```
Use this: {"metrics": ["sessions"], "dimensions": ["country"]}"""
    parsed = extract_json_object(raw)
    assert parsed == {"metrics": ["sessions"], "dimensions": ["country"]}


def test_is_strategy_analytics_question_rejects_briefs_not_metrics():
    brief = (
        "Review GPWW-17 (10 paying customers before the end of the year) and "
        "provide a concrete organic growth, SEO, and content strategy to increase "
        "website sessions and /store pageviews without using paid ads. "
        "Look at GA4 data if needed to see current baseline."
    )
    assert is_strategy_analytics_question(brief) is True
    assert is_strategy_analytics_question("Sessions last 28 days") is False
    assert is_strategy_analytics_question(
        "Sessions last 90 days by sessionDefaultChannelGroup"
    ) is False
    assert is_strategy_analytics_question("SEO traffic last 30 days") is False
    assert is_strategy_analytics_question("How is inbound traffic going recently?") is False


@pytest.mark.skipif(sys.version_info < (3, 10), reason="marketing endpoints need Python 3.10+")
def test_ask_analytics_endpoint_rejects_strategy_brief():
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post(
        "/mcp/tools/ask_analytics_question",
        json={
            "question": (
                "Provide a concrete organic growth, SEO, and content strategy "
                "to increase website sessions without using paid ads."
            ),
            "project_key": "GPWW",
        },
    )
    assert resp.status_code == 400
    error = (resp.get_json() or {}).get("error") or ""
    assert "factual GA4" in error
    assert STRATEGY_ANALYTICS_REJECT[:40] in error


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


def test_parse_query_accepts_empty_json_object(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.marketing.marketing_llm_service.get_llm_client",
        lambda **k: (_FakeLLM("{}"), "gemini-test"),
    )
    svc = MarketingLLMService("sk-test")
    out = svc.parse_query("How are we doing?")
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


def test_format_empty_ga4_finding_includes_filters():
    from bigas.resources.marketing.utils import format_empty_ga4_finding

    text = format_empty_ga4_finding(
        "Did we receive any 'outbound_store_click' events in the last 7 days?",
        {
            "applied_filters": [
                {"field": "eventName", "operator": "equals", "value": "outbound_store_click"}
            ]
        },
    )
    assert "no matching rows" in text.lower()
    assert "outbound_store_click" in text
    assert "valid finding" in text.lower()
    assert "GTM Preview" in text


def test_format_response_obj_empty_rows_is_finding(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.marketing.marketing_llm_service.get_llm_client",
        lambda **k: (_FakeLLM(""), "gemini-test"),
    )
    svc = MarketingLLMService("sk-test")
    question = "Did we receive any 'outbound_store_click' events in the last 7 days?"
    text = svc.format_response_obj(
        {
            "rows": [],
            "applied_filters": [
                {"field": "eventName", "operator": "equals", "value": "outbound_store_click"}
            ],
        },
        question,
    )
    assert "Failed to process" not in text
    assert "Cannot provide analysis" not in text
    assert "outbound_store_click" in text


class _FakeHeader:
    def __init__(self, name):
        self.name = name


class _FakeGa4Response:
    dimension_headers = [_FakeHeader("eventName")]
    metric_headers = [_FakeHeader("eventCount")]
    rows = []


def test_format_response_empty_rows_is_finding(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.marketing.marketing_llm_service.get_llm_client",
        lambda **k: (_FakeLLM(""), "gemini-test"),
    )
    svc = MarketingLLMService("sk-test")
    text = svc.format_response(_FakeGa4Response(), "Any key events last 7 days?")
    assert "no matching rows" in text.lower()
    assert "valid finding" in text.lower()


def test_answer_question_filtered_empty_is_finding(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.marketing.marketing_llm_service.get_llm_client",
        lambda **k: (_FakeLLM(""), "gemini-test"),
    )
    from bigas.resources.marketing.service import MarketingAnalyticsService

    svc = MarketingAnalyticsService.__new__(MarketingAnalyticsService)
    svc.marketing_llm_service = MarketingLLMService("sk-test")
    svc.ga4_service = type(
        "GA4",
        (),
        {
            "build_report_request": lambda *a, **k: object(),
            "run_report": lambda *a, **k: _FakeGa4Response(),
        },
    )()
    svc.marketing_llm_service.parse_query = lambda q: {
        "metrics": ["eventCount"],
        "dimensions": ["eventName"],
        "filters": [{"field": "eventName", "operator": "equals", "value": "outbound_store_click"}],
        "date_range": {"start_date": "7daysAgo", "end_date": "today"},
    }

    answer = svc.answer_question(
        "123",
        "Did we receive any 'outbound_store_click' events in the last 7 days?",
    )
    assert "Failed to process analytics question" not in answer
    assert "outbound_store_click" in answer
    assert "valid finding" in answer.lower()
