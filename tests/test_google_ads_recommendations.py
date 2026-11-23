"""Google Ads recommendations: GAQL build, normalize, and MCP endpoint."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="marketing endpoints use PEP 604 unions (Python 3.10+)",
)


def test_manifest_includes_fetch_google_ads_recommendations():
    from bigas.resources.marketing.endpoints import get_manifest

    tools = {t["name"]: t for t in get_manifest()["tools"]}
    assert "fetch_google_ads_recommendations" in tools
    tool = tools["fetch_google_ads_recommendations"]
    assert tool["method"] == "POST"
    assert tool["path"] == "/mcp/tools/fetch_google_ads_recommendations"
    assert "apply" not in tool["description"].lower() or "does not apply" in tool["description"].lower()


def test_build_recommendations_query_filters():
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    q = GoogleAdsService.build_recommendations_query()
    assert "FROM recommendation" in q
    assert "recommendation.dismissed = FALSE" in q
    assert "recommendation.impact" in q

    q2 = GoogleAdsService.build_recommendations_query(
        include_dismissed=True,
        recommendation_types=["keyword", "CAMPAIGN_BUDGET"],
    )
    assert "recommendation.dismissed = FALSE" not in q2
    assert "recommendation.type IN (KEYWORD, CAMPAIGN_BUDGET)" in q2


def test_build_recommendations_query_rejects_non_identifiers():
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    with pytest.raises(ValueError, match="recommendation_types"):
        GoogleAdsService.build_recommendations_query(
            recommendation_types=["KEYWORD) OR (1=1"]
        )


def test_normalize_recommendation_row_impact_and_details():
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    row = {
        "recommendation": {
            "resourceName": "customers/1/recommendations/9",
            "type": "KEYWORD",
            "dismissed": False,
            "campaign": "customers/1/campaigns/42",
            "impact": {
                "baseMetrics": {"clicks": "10", "impressions": "100", "costMicros": "5000000", "conversions": "1"},
                "potentialMetrics": {
                    "clicks": "15",
                    "impressions": "120",
                    "costMicros": "6000000",
                    "conversions": "2",
                },
            },
            "keywordRecommendation": {
                "keyword": {"text": "running shoes", "matchType": "BROAD"},
                "recommendedCpcBidMicros": "1000000",
            },
        },
        "campaign": {"id": "42", "name": "Search Brand"},
        "customer": {"descriptiveName": "Demo Ads"},
    }
    flat = GoogleAdsService.normalize_recommendation_row(row)
    assert flat["type"] == "KEYWORD"
    assert flat["campaign_id"] == "42"
    assert flat["campaign_name"] == "Search Brand"
    assert flat["impact"]["delta"]["clicks"] == 5
    assert flat["impact"]["delta"]["conversions"] == 1.0
    assert flat["impact"]["base_metrics"]["cost"] == 5.0
    assert flat["details"]["kind"] == "keyword"
    assert flat["details"]["payload"]["keyword"]["text"] == "running shoes"


def test_normalize_recommendation_rows_groups_and_sorts():
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    rows = [
        {
            "recommendation": {
                "resourceName": "customers/1/recommendations/1",
                "type": "CALLOUT_ASSET",
                "dismissed": False,
                "impact": {
                    "baseMetrics": {"clicks": "1", "conversions": "0"},
                    "potentialMetrics": {"clicks": "2", "conversions": "0"},
                },
            },
            "campaign": {"id": "1", "name": "A"},
        },
        {
            "recommendation": {
                "resourceName": "customers/1/recommendations/2",
                "type": "KEYWORD",
                "dismissed": False,
                "impact": {
                    "baseMetrics": {"clicks": "10", "conversions": "1"},
                    "potentialMetrics": {"clicks": "20", "conversions": "3"},
                },
            },
            "campaign": {"id": "2", "name": "B"},
        },
    ]
    out = GoogleAdsService.normalize_recommendation_rows(rows)
    assert out["count"] == 2
    assert out["by_type"] == {"CALLOUT_ASSET": 1, "KEYWORD": 1}
    assert out["recommendations"][0]["type"] == "KEYWORD"


def test_fetch_google_ads_recommendations_endpoint(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing import google_ads_service as gas
    from bigas.resources.marketing.endpoints import marketing_bp

    class _FakeSvc:
        def __init__(self, *a, **k):
            self.kwargs = k

        def fetch_recommendations(self, customer_id, **kwargs):
            return {
                "customer_id": customer_id,
                "include_dismissed": kwargs.get("include_dismissed", False),
                "recommendation_types": kwargs.get("recommendation_types") or [],
                "optimization": {"score": 0.72, "account_name": "Demo", "uplift_by_type": []},
                "recommendations": [
                    {
                        "resource_name": "customers/111/recommendations/1",
                        "type": "CAMPAIGN_BUDGET",
                        "dismissed": False,
                        "campaign_id": "9",
                        "campaign_name": "Brand",
                        "impact": None,
                        "details": None,
                    }
                ],
                "count": 1,
                "by_type": {"CAMPAIGN_BUDGET": 1},
            }

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.delenv("GOOGLE_ADS_CUSTOMER_ID", raising=False)
    monkeypatch.setattr(gas, "GoogleAdsService", _FakeSvc)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post(
        "/mcp/tools/fetch_google_ads_recommendations",
        json={
            "customer_id": "111-222-3333",
            "recommendation_types": ["CAMPAIGN_BUDGET"],
            "include_optimization_score": True,
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["customer_id"] == "1112223333"
    assert body["count"] == 1
    assert body["optimization"]["score"] == 0.72
    assert body["recommendations"][0]["type"] == "CAMPAIGN_BUDGET"


def test_fetch_google_ads_recommendations_requires_customer(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.delenv("GOOGLE_ADS_CUSTOMER_ID", raising=False)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post("/mcp/tools/fetch_google_ads_recommendations", json={})
    assert resp.status_code == 400
    assert "customer_id" in resp.get_json()["error"]


def test_fetch_google_ads_recommendations_accepts_integer_ids(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing import google_ads_service as gas
    from bigas.resources.marketing.endpoints import marketing_bp

    seen = {}

    class _FakeSvc:
        def __init__(self, *a, **k):
            seen["login_customer_id"] = k.get("login_customer_id")

        def fetch_recommendations(self, customer_id, **kwargs):
            seen["customer_id"] = customer_id
            seen["recommendation_types"] = kwargs.get("recommendation_types")
            return {
                "customer_id": customer_id,
                "include_dismissed": False,
                "recommendation_types": kwargs.get("recommendation_types") or [],
                "optimization": None,
                "recommendations": [],
                "count": 0,
                "by_type": {},
            }

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.delenv("GOOGLE_ADS_CUSTOMER_ID", raising=False)
    monkeypatch.setattr(gas, "GoogleAdsService", _FakeSvc)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post(
        "/mcp/tools/fetch_google_ads_recommendations",
        json={
            "customer_id": 1234567890,
            "login_customer_id": 9988776655,
            "recommendation_types": ["keyword"],
        },
    )
    assert resp.status_code == 200
    assert seen["customer_id"] == "1234567890"
    assert seen["login_customer_id"] == "9988776655"
    assert seen["recommendation_types"] == ["KEYWORD"]


def test_fetch_google_ads_recommendations_rejects_non_digit_customer(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    monkeypatch.delenv("GOOGLE_ADS_CUSTOMER_ID", raising=False)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post(
        "/mcp/tools/fetch_google_ads_recommendations",
        json={"customer_id": "abc-123"},
    )
    assert resp.status_code == 400
    assert "digits" in resp.get_json()["error"]


def test_fetch_google_ads_recommendations_rejects_injected_types(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    monkeypatch.delenv("GOOGLE_ADS_CUSTOMER_ID", raising=False)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post(
        "/mcp/tools/fetch_google_ads_recommendations",
        json={
            "customer_id": "1234567890",
            "recommendation_types": ["KEYWORD) OR (1=1"],
        },
    )
    assert resp.status_code == 400
    assert "recommendation_types" in resp.get_json()["error"]


def test_fetch_google_ads_recommendations_uses_env_customer(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing import google_ads_service as gas
    from bigas.resources.marketing.endpoints import marketing_bp

    seen = {}

    class _FakeSvc:
        def __init__(self, *a, **k):
            pass

        def fetch_recommendations(self, customer_id, **kwargs):
            seen["customer_id"] = customer_id
            return {
                "customer_id": customer_id,
                "include_dismissed": False,
                "recommendation_types": [],
                "optimization": None,
                "recommendations": [],
                "count": 0,
                "by_type": {},
            }

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", "9998887777")
    monkeypatch.setattr(gas, "GoogleAdsService", _FakeSvc)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post("/mcp/tools/fetch_google_ads_recommendations", json={})
    assert resp.status_code == 200
    assert seen["customer_id"] == "9998887777"


def test_fetch_recommendations_service_calls_search_stream(monkeypatch):
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    calls = []

    def _fake_search_stream(self, customer_id, query):
        calls.append(query)
        if "FROM recommendation" in query:
            return (
                [
                    {
                        "recommendation": {
                            "resourceName": "customers/1/recommendations/1",
                            "type": "KEYWORD",
                            "dismissed": False,
                        },
                        "campaign": {"id": "7", "name": "Search"},
                    }
                ],
                [],
            )
        if "customer.optimization_score" in query:
            return (
                [{"customer": {"id": "1", "descriptiveName": "Acme", "optimizationScore": 0.81}}],
                [],
            )
        if "optimization_score_uplift" in query:
            return (
                [
                    {
                        "segments": {"recommendationType": "KEYWORD"},
                        "metrics": {
                            "optimizationScoreUplift": 0.05,
                            "optimizationScoreUrl": "https://ads.google.com/example",
                        },
                    }
                ],
                [],
            )
        return [], []

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.setattr(GoogleAdsService, "search_stream", _fake_search_stream)
    svc = GoogleAdsService()
    out = svc.fetch_recommendations("1234567890")
    assert out["count"] == 1
    assert out["recommendations"][0]["type"] == "KEYWORD"
    assert out["optimization"]["score"] == 0.81
    assert out["optimization"]["uplift_by_type"][0]["recommendation_type"] == "KEYWORD"
    assert any("FROM recommendation" in q for q in calls)


def test_feature_hides_fetch_google_ads_recommendations(monkeypatch):
    from bigas.features import filter_tools, hidden_tool_names

    monkeypatch.setenv("BIGAS_FEATURES", "objectives")
    assert "fetch_google_ads_recommendations" in hidden_tool_names()
    filtered = filter_tools(
        [{"name": "fetch_google_ads_recommendations"}, {"name": "create_ticket"}]
    )
    assert [t["name"] for t in filtered] == ["create_ticket"]


def test_tool_tier_is_look():
    from bigas.gateway.tiers import LOOK, tool_tier

    assert tool_tier("fetch_google_ads_recommendations") == LOOK
