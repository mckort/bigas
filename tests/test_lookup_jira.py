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
    parse_issue_keys,
)
from bigas.resources.product.create_release_notes.jira_client import compact_jira_issue


@pytest.fixture(autouse=True)
def _force_external_jira(monkeypatch):
    monkeypatch.setenv("USE_INTERNAL_BOARD", "false")
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "dev@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "GPWW")


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


def test_parse_issue_keys_expands_range_and_lists():
    assert parse_issue_keys("BIG-15 to BIG-18") == ["BIG-15", "BIG-16", "BIG-17", "BIG-18"]
    assert parse_issue_keys("BIG-15 - BIG-18") == ["BIG-15", "BIG-16", "BIG-17", "BIG-18"]
    assert parse_issue_keys("BIG-15-18") == ["BIG-15", "BIG-16", "BIG-17", "BIG-18"]
    assert parse_issue_keys("which of the BIG-15 to 18 have been done?") == [
        "BIG-15",
        "BIG-16",
        "BIG-17",
        "BIG-18",
    ]
    assert parse_issue_keys("GPWW-3, GPWW-4") == ["GPWW-3", "GPWW-4"]
    assert parse_issue_keys(["vfa-17", "https://x.atlassian.net/browse/VFA-18"]) == [
        "VFA-17",
        "VFA-18",
    ]
    assert parse_issue_keys("no tickets here") == []


def test_lookup_jira_requires_issue_or_project():
    with pytest.raises(LookupJiraError, match="issue_key or project_key"):
        LookupJiraService().lookup()


def test_lookup_jira_issue_includes_parent_and_project_epics(monkeypatch):
    class FakeClient:
        def __init__(self, config):
            self._config = config

        def search_issues_by_keys(self, issue_keys, *, fields=None, max_results_per_page=50, max_pages=10):
            assert list(issue_keys) == ["GPWW-3"]
            return [
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
                                "summary": "10 paying customers",
                                "issuetype": {"name": "Epic"},
                            },
                        },
                    },
                }
            ]

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


def test_lookup_jira_range_returns_statuses_without_epics(monkeypatch):
    class FakeClient:
        def __init__(self, config):
            self._config = config

        def search_issues_by_keys(self, issue_keys, *, fields=None, max_results_per_page=50, max_pages=10):
            statuses = {
                "BIG-15": "To Do",
                "BIG-16": "Done",
                "BIG-17": "In Progress",
                "BIG-18": "Done",
            }
            issues = []
            for issue_key in issue_keys:
                if issue_key not in statuses:
                    raise AssertionError(f"unexpected key {issue_key}")
                issues.append(
                    {
                        "key": issue_key,
                        "fields": {
                            "summary": f"Task {issue_key}",
                            "issuetype": {"name": "Task"},
                            "status": {"name": statuses[issue_key]},
                            "project": {"key": "BIG"},
                        },
                    }
                )
            return issues

        def list_open_epics(self, project_keys=None, *, max_results=20):
            raise AssertionError("multi-issue lookup should not list open Epics")

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

    result = LookupJiraService().lookup(issue_key="BIG-15 to BIG-18")
    assert [issue["key"] for issue in result["issues"]] == ["BIG-15", "BIG-16", "BIG-17", "BIG-18"]
    assert result["issues"][1]["status"] == "Done"
    assert "epics" not in result
