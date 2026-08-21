"""Tests for Jira transition (BIG-13) and formatting helpers."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")
os.environ.setdefault("JIRA_BASE_URL", "https://example.atlassian.net")
os.environ.setdefault("JIRA_EMAIL", "test@example.com")
os.environ.setdefault("JIRA_API_TOKEN", "test-token")
os.environ.setdefault("JIRA_PROJECT_KEY", "BIG")

from app import create_app
from bigas.agents.chief_of_staff import humanize_tool_result
from bigas.chat.jira_formatting import (
    JIRA_FORMATTING_RULES,
    format_jira_issue_markdown,
    humanize_jira_tool_result,
    jira_transition_action_markdown,
)
from bigas.resources.product.create_release_notes.jira_client import JiraClient, JiraConfig


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-dev-token", "Content-Type": "application/json"}


def test_jira_transition_action_markdown():
    md = jira_transition_action_markdown("BIG-13")
    assert md == "[Move to next column](bigas://action/jira_transition?issue=BIG-13)"


def test_format_jira_issue_markdown_includes_button():
    text = format_jira_issue_markdown(
        key="BIG-13",
        url="https://example.atlassian.net/browse/BIG-13",
        summary="Update system prompt",
    )
    assert "[Update system prompt](https://example.atlassian.net/browse/BIG-13)" in text
    assert "bigas://action/jira_transition?issue=BIG-13" in text


def test_humanize_jira_tool_result():
    result = humanize_jira_tool_result(
        {
            "ok": True,
            "key": "BIG-42",
            "url": "https://example.atlassian.net/browse/BIG-42",
            "summary": "New feature",
        }
    )
    assert result is not None
    assert "[New feature]" in result
    assert "bigas://action/jira_transition?issue=BIG-42" in result


def test_humanize_lookup_jira_includes_parent_and_epics():
    result = humanize_jira_tool_result(
        {
            "ok": True,
            "issue": {
                "key": "GPWW-3",
                "summary": "Implement tracking",
                "url": "https://example.atlassian.net/browse/GPWW-3",
                "parent": {
                    "key": "GPWW-2",
                    "summary": "10 paying customers",
                    "issue_type": "Epic",
                    "url": "https://example.atlassian.net/browse/GPWW-2",
                },
            },
            "epics": [
                {
                    "key": "GPWW-2",
                    "summary": "10 paying customers",
                    "url": "https://example.atlassian.net/browse/GPWW-2",
                }
            ],
        }
    )
    assert result is not None
    assert "[Implement tracking](https://example.atlassian.net/browse/GPWW-3)" in result
    assert "Parent (Epic): [10 paying customers](https://example.atlassian.net/browse/GPWW-2)" in result
    assert "Open Epics:" in result
    assert "[10 paying customers](https://example.atlassian.net/browse/GPWW-2)" in result


def test_humanize_tool_result_prefers_jira_markdown():
    text = humanize_tool_result(
        {
            "ok": True,
            "key": "BIG-99",
            "url": "https://example.atlassian.net/browse/BIG-99",
        }
    )
    assert text is not None
    assert "[BIG-99]" in text
    assert "bigas://" in text


def test_jira_formatting_rules_require_english():
    from bigas.agents.chief_of_staff import _agent_system_prompt
    from bigas.chat.jira_formatting import JIRA_AWARE_AGENT_IDS

    assert "Always respond in English" in JIRA_FORMATTING_RULES
    assert "Never output raw JSON or HTML" in JIRA_FORMATTING_RULES
    assert "create_jira_issue" in JIRA_FORMATTING_RULES
    assert "Never tell the user to create the issue in Jira" in JIRA_FORMATTING_RULES
    assert JIRA_AWARE_AGENT_IDS == frozenset(
        {"chief", "marketing", "product", "cto", "devops"}
    )
    prompt = _agent_system_prompt(
        {"agent_id": "marketing", "system_prompt_goals": "GA4 analyst."}
    )
    assert "create_jira_issue" in prompt
    assert "Never tell the user to create the issue in Jira" in prompt
    assert "lookup_jira" in prompt
    assert "does not mean the new work belongs under the same Epic" in prompt


def test_transition_issue_to_next_skips_backward(monkeypatch):
    config = JiraConfig.from_env()
    jira = JiraClient(config)

    monkeypatch.setattr(
        jira,
        "get_issue",
        lambda key, **kwargs: {
            "fields": {"status": {"name": "In Progress"}, "summary": "Test issue"},
        },
    )
    monkeypatch.setattr(
        jira,
        "list_transitions",
        lambda key: [
            {"id": "1", "name": "Reopen", "to": {"name": "To Do"}},
            {"id": "2", "name": "Start review", "to": {"name": "In Review"}},
        ],
    )
    posted = {}

    def fake_post(method, url, *, json=None, expect_json=True):
        posted["payload"] = json
        return {}

    monkeypatch.setattr(jira, "_request_with_retry_429", fake_post)

    result = jira.transition_issue_to_next("BIG-1")
    assert result["new_status"] == "In Review"
    assert result["previous_status"] == "In Progress"
    assert posted["payload"]["transition"]["id"] == "2"


def test_jira_transition_endpoint_success(client, monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.product.jira_transition.service.transition_issue_to_next_column",
        lambda issue_key: {
            "success": True,
            "message": "Moved BIG-1 to In Review",
            "new_status": "In Review",
            "issue_key": issue_key,
            "previous_status": "In Progress",
        },
    )

    resp = client.post(
        "/api/jira/transition",
        headers=_auth_headers(),
        data=json.dumps({"issue_key": "BIG-1"}),
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["new_status"] == "In Review"


def test_jira_transition_endpoint_logs_activity(client, monkeypatch):
    from bigas.chat.activity import mirror_to_activity_feed
    from bigas.chat.jira_formatting import format_jira_issue_markdown

    def mock_transition(issue_key):
        link = format_jira_issue_markdown(
            key=issue_key,
            url=f"https://example.atlassian.net/browse/{issue_key}",
            summary="Test",
            include_transition_button=False,
        )
        mirror_to_activity_feed(
            f"Moved {link} to In Review",
            type_="jira",
            source="product",
        )
        return {
            "success": True,
            "message": "Moved BIG-2 to In Review",
            "new_status": "In Review",
            "issue_key": issue_key,
        }

    monkeypatch.setattr(
        "bigas.resources.product.jira_transition.service.transition_issue_to_next_column",
        mock_transition,
    )

    resp = client.post(
        "/api/jira/transition",
        headers=_auth_headers(),
        data=json.dumps({"issue_key": "BIG-2"}),
    )
    assert resp.status_code == 200

    feed = client.get("/api/feed", headers=_auth_headers())
    events = feed.get_json()["events"]
    assert any("Moved" in e["content"] and "In Review" in e["content"] for e in events)
    assert any(e.get("type") == "jira" for e in events)


def test_jira_transition_endpoint_requires_issue_key(client):
    resp = client.post(
        "/api/jira/transition",
        headers=_auth_headers(),
        data=json.dumps({}),
    )
    assert resp.status_code == 400
