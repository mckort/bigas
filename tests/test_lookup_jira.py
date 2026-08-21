"""Unit tests for lookup_jira MCP tool (no network)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas.resources.product.create_jira_issue.lookup import (
    LookupJiraError,
    LookupJiraService,
)
from bigas.resources.product.create_release_notes.jira_client import compact_jira_issue


def test_compact_jira_issue_extracts_epic_parent():
    compact = compact_jira_issue(
        {
            "key": "GPWW-3",
            "fields": {
                "summary": "Implement tracking",
                "issuetype": {"name": "Task"},
                "status": {"name": "To Do"},
                "project": {"key": "GPWW"},
                "parent": {
                    "key": "GPWW-2",
                    "fields": {
                        "summary": "10 paying customers before the end of 2026",
                        "issuetype": {"name": "Epic"},
                    },
                },
            },
        },
        base_url="https://example.atlassian.net",
    )
    assert compact["key"] == "GPWW-3"
    assert compact["parent_epic_key"] == "GPWW-2"
    assert compact["parent"]["key"] == "GPWW-2"
    assert compact["parent"]["issue_type"] == "Epic"
    assert compact["url"] == "https://example.atlassian.net/browse/GPWW-3"


def test_compact_jira_issue_without_parent():
    compact = compact_jira_issue(
        {
            "key": "GPWW-9",
            "fields": {
                "summary": "Standalone",
                "issuetype": {"name": "Task"},
                "status": {"name": "To Do"},
                "project": {"key": "GPWW"},
            },
        },
        base_url="https://example.atlassian.net",
    )
    assert "parent" not in compact
    assert "parent_epic_key" not in compact


def test_lookup_jira_requires_issue_or_project():
    with pytest.raises(LookupJiraError, match="issue_key or project_key"):
        LookupJiraService().lookup()


def test_lookup_jira_issue_includes_parent_and_project_epics(monkeypatch):
    class FakeClient:
        def __init__(self, config):
            self._config = config

        def get_issue(self, issue_key, *, fields=None, expand=None):
            assert issue_key == "GPWW-3"
            return {
                "key": "GPWW-3",
                "fields": {
                    "summary": "Implement tracking",
                    "issuetype": {"name": "Task"},
                    "status": {"name": "To Do"},
                    "project": {"key": "GPWW"},
                    "parent": {
                        "key": "GPWW-2",
                        "fields": {
                            "summary": "10 paying customers",
                            "issuetype": {"name": "Epic"},
                        },
                    },
                },
            }

        def list_open_epics(self, project_keys=None, *, max_results=20):
            assert project_keys == "GPWW"
            return [
                {
                    "key": "GPWW-2",
                    "summary": "10 paying customers",
                    "issue_type": "Epic",
                    "url": "https://example.atlassian.net/browse/GPWW-2",
                }
            ]

    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.lookup.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.lookup.JiraConfig",
        type(
            "C",
            (),
            {
                "from_env": staticmethod(
                    lambda: type("Cfg", (), {"base_url": "https://example.atlassian.net"})()
                )
            },
        )(),
    )

    result = LookupJiraService().lookup(issue_key="gpww-3")
    assert result["ok"] is True
    assert result["issue"]["key"] == "GPWW-3"
    assert result["parent"]["key"] == "GPWW-2"
    assert result["epics"][0]["key"] == "GPWW-2"
    assert "standalone" in result["parent_guidance"].lower()


def test_lookup_jira_project_lists_epics_only(monkeypatch):
    class FakeClient:
        def __init__(self, config):
            self._config = config

        def list_open_epics(self, project_keys=None, *, max_results=20):
            return [{"key": "GPWW-2", "summary": "10 paying customers", "issue_type": "Epic"}]

    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.lookup.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.lookup.JiraConfig",
        type("C", (), {"from_env": staticmethod(lambda: object())})(),
    )

    result = LookupJiraService().lookup(project_key="GPWW")
    assert result["ok"] is True
    assert "issue" not in result
    assert result["epics"][0]["key"] == "GPWW-2"
