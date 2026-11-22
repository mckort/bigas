"""Google Ads health check: discover accessible customers without a customer_id."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="marketing endpoints use PEP 604 unions (Python 3.10+)",
)


def test_manifest_includes_google_ads_health_check():
    from bigas.resources.marketing.endpoints import get_manifest

    tools = {t["name"]: t for t in get_manifest()["tools"]}
    assert "google_ads_health_check" in tools
    assert tools["google_ads_health_check"]["method"] == "GET"
    assert tools["google_ads_health_check"]["path"] == "/mcp/tools/google_ads_health_check"


def test_google_ads_health_check_lists_customers(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing import google_ads_service as gas
    from bigas.resources.marketing.endpoints import marketing_bp

    class _FakeSvc:
        def list_accessible_customers(self):
            return ["customers/1112223333", "customers/4445556666"]

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", "111-222-3333")
    monkeypatch.delenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", raising=False)
    monkeypatch.setattr(gas, "GoogleAdsService", lambda *a, **k: _FakeSvc())

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.get("/mcp/tools/google_ads_health_check")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["customer_ids"] == ["1112223333", "4445556666"]
    assert body["configured_customer_id"] == "1112223333"
    assert body["configured_customer_accessible"] is True
    assert body["hint"] is None
    assert body["resource_names"] == ["customers/1112223333", "customers/4445556666"]


def test_google_ads_health_check_mcc_login_counts_as_accessible(monkeypatch):
    """A client id is absent from ListAccessibleCustomers when the MCC is the direct account."""
    from flask import Flask

    from bigas.resources.marketing import google_ads_service as gas
    from bigas.resources.marketing.endpoints import marketing_bp

    class _FakeSvc:
        def list_accessible_customers(self):
            return ["customers/9998887777"]

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", "123-456-7890")
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "999-888-7777")
    monkeypatch.setattr(gas, "GoogleAdsService", lambda *a, **k: _FakeSvc())

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.get("/mcp/tools/google_ads_health_check")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["configured_customer_id"] == "1234567890"
    assert body["configured_login_customer_id"] == "9998887777"
    assert body["customer_ids"] == ["9998887777"]
    assert body["configured_customer_accessible"] is True
    assert "9998887777" in body["hint"]
    assert "is accessible" in body["hint"]
    assert "set GOOGLE_ADS_LOGIN_CUSTOMER_ID" not in body["hint"]


def test_google_ads_health_check_login_not_in_list_stays_inaccessible(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing import google_ads_service as gas
    from bigas.resources.marketing.endpoints import marketing_bp

    class _FakeSvc:
        def list_accessible_customers(self):
            return ["customers/1112223333"]

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", "1234567890")
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "0001112223")
    monkeypatch.setattr(gas, "GoogleAdsService", lambda *a, **k: _FakeSvc())

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.get("/mcp/tools/google_ads_health_check")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["configured_customer_accessible"] is False
    assert "set GOOGLE_ADS_LOGIN_CUSTOMER_ID" in body["hint"]


def test_google_ads_health_check_sanitizes_names_and_dedupes_ids(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing import google_ads_service as gas
    from bigas.resources.marketing.endpoints import marketing_bp

    class _Weird:
        def __str__(self):
            return "customers/7776665555"

    class _FakeSvc:
        def list_accessible_customers(self):
            return ["customers/1112223333", "customers/1112223333", _Weird(), None, ""]

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", "1112223333")
    monkeypatch.delenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", raising=False)
    monkeypatch.setattr(gas, "GoogleAdsService", lambda *a, **k: _FakeSvc())

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.get("/mcp/tools/google_ads_health_check")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["customer_ids"] == ["1112223333"]
    assert body["accessible_customers_count"] == 1
    assert body["resource_names"] == [
        "customers/1112223333",
        "customers/1112223333",
        "customers/7776665555",
    ]
    assert body["configured_customer_accessible"] is True


def test_google_ads_health_check_missing_token(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    monkeypatch.delenv("GOOGLE_ADS_DEVELOPER_TOKEN", raising=False)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.get("/mcp/tools/google_ads_health_check")
    assert resp.status_code == 400
    assert "GOOGLE_ADS_DEVELOPER_TOKEN" in resp.get_json()["error"]


def test_google_ads_health_check_auth_error(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing import google_ads_service as gas
    from bigas.resources.marketing.endpoints import marketing_bp

    class _FakeSvc:
        def list_accessible_customers(self):
            raise gas.GoogleAdsAuthError("ADC failed")

    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "test-token")
    monkeypatch.setattr(gas, "GoogleAdsService", lambda *a, **k: _FakeSvc())

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.get("/mcp/tools/google_ads_health_check")
    assert resp.status_code == 401
    assert "ADC" in resp.get_json()["error"]


def test_google_ads_feature_hides_health_check(monkeypatch):
    from bigas.features import filter_tools, hidden_tool_names

    monkeypatch.setenv("BIGAS_FEATURES", "objectives")
    assert "google_ads_health_check" in hidden_tool_names()
    filtered = filter_tools(
        [{"name": "google_ads_health_check"}, {"name": "create_ticket"}]
    )
    assert [t["name"] for t in filtered] == ["create_ticket"]
