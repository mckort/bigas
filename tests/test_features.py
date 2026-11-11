"""BIGAS_FEATURES switches optional areas off without breaking the core loop."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas import features


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, {"marketing", "objectives", "outreach"}),
        ("", {"marketing", "objectives", "outreach"}),
        ("all", {"marketing", "objectives", "outreach"}),
        ("none", set()),
        ("objectives", {"objectives"}),
        (" Objectives , marketing,unknown", {"objectives", "marketing"}),
    ],
)
def test_enabled_features(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("BIGAS_FEATURES", raising=False)
    else:
        monkeypatch.setenv("BIGAS_FEATURES", raw)
    assert set(features.enabled_features()) == expected


def test_core_areas_cannot_be_switched_off(monkeypatch):
    monkeypatch.setenv("BIGAS_FEATURES", "none")
    assert features.feature_enabled("board")
    assert features.feature_enabled("devops")
    assert not features.feature_enabled("marketing")


def test_filters_hide_marketing_tools_and_agent(monkeypatch):
    monkeypatch.setenv("BIGAS_FEATURES", "objectives")
    tools = [{"name": "weekly_analytics_report"}, {"name": "trigger_deployment"}, {"name": "weekly_okr_pulse"}]
    assert [t["name"] for t in features.filter_tools(tools)] == ["trigger_deployment", "weekly_okr_pulse"]
    agents = [{"agent_id": "marketing"}, {"agent_id": "devops"}]
    assert [a["agent_id"] for a in features.filter_agents(agents)] == ["devops"]


def test_chief_drops_disabled_specialist(monkeypatch):
    from bigas.agents import chief_of_staff as chief

    monkeypatch.setenv("BIGAS_FEATURES", "none")
    tool = chief._consult_specialist_tool()
    enum = tool["function"]["parameters"]["properties"]["specialist"]["enum"]
    assert "marketing" not in enum and "devops" in enum
    assert "- marketing:" not in tool["function"]["description"]
    assert "- marketing:" not in chief._specialist_capabilities()
    assert chief._resolve_delegate_target("marketing") is None
    assert chief._resolve_delegate_target("devops") == "devops"
    # The shared constant is untouched.
    assert "marketing" in chief.CONSULT_SPECIALIST_TOOL["function"]["parameters"]["properties"]["specialist"]["enum"]

    monkeypatch.delenv("BIGAS_FEATURES")
    assert chief._consult_specialist_tool() is chief.CONSULT_SPECIALIST_TOOL


def test_api_reports_features_and_hides_marketing_agent(monkeypatch):
    from app import create_app

    monkeypatch.setenv("BIGAS_FEATURES", "none")
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        config = client.get("/api/auth/config").get_json()
        assert config["features"] == {"marketing": False, "objectives": False, "outreach": False}
        agents = client.get(
            "/api/agents", headers={"Authorization": "Bearer test-dev-token"}
        ).get_json()["agents"]
        ids = {a["agent_id"] for a in agents}
        assert "marketing" not in ids
        assert "devops" in ids
