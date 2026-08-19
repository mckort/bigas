"""Async weekly analytics report returns a job_id without running the full pipeline."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="marketing endpoints use PEP 604 unions (Python 3.10+)",
)


def test_manifest_includes_question_and_async_weekly():
    from bigas.resources.marketing.endpoints import get_manifest

    tools = {t["name"]: t for t in get_manifest()["tools"]}
    ask = tools["ask_analytics_question"]
    assert "question" in ask["parameters"]["required"]
    trends = tools["analyze_trends"]
    assert trends["parameters"]["properties"]["post_to_discord"]["default"] is False
    assert "weekly_analytics_report_async" in tools


class _FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args

    def start(self):
        pass


def test_weekly_async_returns_job_id(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    monkeypatch.setattr("bigas.resources.marketing.endpoints.threading.Thread", _FakeThread)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post("/mcp/tools/weekly_analytics_report_async", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "accepted"
    assert body["job_id"].startswith("job_")


def test_weekly_sync_async_flag_returns_job_id(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    monkeypatch.setattr("bigas.resources.marketing.endpoints.threading.Thread", _FakeThread)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post("/mcp/tools/weekly_analytics_report", json={"async": True})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "accepted"


def test_weekly_async_rejects_non_object_json():
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post("/mcp/tools/weekly_analytics_report_async", json=[])
    assert resp.status_code == 400
    assert "JSON object" in resp.get_json()["error"]


def test_weekly_async_rejects_invalid_timeout():
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post("/mcp/tools/weekly_analytics_report_async", json={"timeout_seconds": "abc"})
    assert resp.status_code == 400
    assert "timeout_seconds" in resp.get_json()["error"]


def test_weekly_async_clamps_zero_timeout_to_minimum(monkeypatch):
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    monkeypatch.setattr("bigas.resources.marketing.endpoints.threading.Thread", _FakeThread)

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    client = app.test_client()
    resp = client.post("/mcp/tools/weekly_analytics_report_async", json={"timeout_seconds": 0})
    assert resp.status_code == 200
    assert resp.get_json()["timeout_seconds"] == 10
