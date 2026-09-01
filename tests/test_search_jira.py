"""Unit tests for search_jira MCP tool (no network)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas.resources.product.search_jira.service import (
    SearchJiraError,
    SearchJiraService,
    extract_jql_project_keys,
    scope_jql_to_portfolio,
)


def test_parse_internal_filters_stops_at_and():
    from bigas.resources.product.search_jira.internal import parse_internal_filters

    filters = parse_internal_filters(
        'project = VFA AND statusCategory = Done AND text ~ "news"'
    )
    assert filters["project_keys"] == ["VFA"]
    assert filters["status_category"].lower() == "done"
    assert filters["text"] == "news"


def test_extract_jql_project_keys():
    assert extract_jql_project_keys('project = VFA AND type = Bug') == ["VFA"]
    assert extract_jql_project_keys('project in (VFA, WAYW)') == ["VFA", "WAYW"]
    assert extract_jql_project_keys("project in ('BIG', \"GPWW\")") == ["BIG", "GPWW"]
    assert extract_jql_project_keys("type = Bug") == []


def test_scope_jql_wraps_without_project():
    jql = scope_jql_to_portfolio(
        'type = Bug AND text ~ "Stripe"',
        allowed=["VFA", "WAYW"],
    )
    assert jql == '(type = Bug AND text ~ "Stripe") AND project in (VFA, WAYW)'


def test_scope_jql_preserves_order_by():
    jql = scope_jql_to_portfolio("type = Bug ORDER BY created DESC", allowed=["VFA"])
    assert jql == "(type = Bug) AND project in (VFA) ORDER BY created DESC"


def test_scope_jql_keeps_allowed_project():
    assert (
        scope_jql_to_portfolio("project = VFA AND type = Bug", allowed=["VFA", "WAYW"])
        == "(project = VFA AND type = Bug) AND project in (VFA, WAYW)"
    )


def test_scope_jql_rejects_unknown_project():
    with pytest.raises(SearchJiraError, match="outside the portfolio"):
        scope_jql_to_portfolio("project = SECRET AND type = Bug", allowed=["VFA"])


def test_scope_jql_requires_query():
    with pytest.raises(SearchJiraError, match="jql is required"):
        scope_jql_to_portfolio("  ", allowed=["VFA"])


def test_search_jira_internal_board(monkeypatch):
    from datetime import datetime, timezone

    from bigas.tickets.store import MemoryTicketStore

    store = MemoryTicketStore()
    board = store.create_board("u1", name="VFA Board", project_key="VFA")
    done = store.create_ticket(board["board_id"], title="News tab", description="weekly company news")
    store.update_ticket(done["ticket_id"], status="Done")
    open_ticket = store.create_ticket(board["board_id"], title="Pending rejection column")
    monkeypatch.setattr("bigas.tickets.config.use_internal_board", lambda: True)
    monkeypatch.setattr(
        "bigas.resources.product.search_jira.internal.get_ticket_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "bigas.resources.product.search_jira.service.allowed_project_keys",
        lambda: ["VFA", "WAYW"],
    )
    result = SearchJiraService().search(
        jql='project = VFA AND statusCategory = Done AND text ~ "news"',
        max_results=10,
    )
    assert result["ok"] is True
    assert result["source"] == "internal_board"
    assert result["count"] == 1
    assert result["issues"][0]["key"] == done["key"]
    assert result["issues"][0]["summary"] == "News tab"
    skipped = SearchJiraService().search(jql="project = VFA AND status = \"To Do\"")
    assert skipped["issues"][0]["key"] == open_ticket["key"]
    dated = SearchJiraService().search(
        jql=f'project = VFA AND updated >= {datetime.now(timezone.utc).strftime("%Y-%m-%d")}'
    )
    assert dated["count"] >= 1


def test_search_jira_returns_compact_issues(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, config):
            self._config = config

        def search_jql(self, *, jql, fields=None, max_results_per_page=25, max_pages=1):
            captured["jql"] = jql
            captured["max_results"] = max_results_per_page
            return [
                {
                    "key": "VFA-1",
                    "fields": {
                        "summary": "Stripe webhook",
                        "issuetype": {"name": "Bug"},
                        "status": {"name": "To Do"},
                        "project": {"key": "VFA"},
                    },
                }
            ]

    monkeypatch.setattr("bigas.tickets.config.use_internal_board", lambda: False)
    monkeypatch.setattr(
        "bigas.resources.product.search_jira.service.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.search_jira.service.JiraConfig",
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
    monkeypatch.setattr(
        "bigas.resources.product.search_jira.service.allowed_project_keys",
        lambda: ["VFA", "WAYW"],
    )

    result = SearchJiraService().search(
        jql='type = Bug AND text ~ "Stripe"',
        max_results=10,
    )
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["issues"][0]["key"] == "VFA-1"
    assert result["issues"][0]["summary"] == "Stripe webhook"
    assert "project in (VFA, WAYW)" in captured["jql"]
    assert captured["max_results"] == 10


def test_search_jira_clamps_max_results(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, config):
            self._config = config

        def search_jql(self, *, jql, fields=None, max_results_per_page=25, max_pages=1):
            captured["max_results"] = max_results_per_page
            return []

    monkeypatch.setattr("bigas.tickets.config.use_internal_board", lambda: False)
    monkeypatch.setattr(
        "bigas.resources.product.search_jira.service.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.search_jira.service.JiraConfig",
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
    monkeypatch.setattr(
        "bigas.resources.product.search_jira.service.allowed_project_keys",
        lambda: ["VFA"],
    )
    result = SearchJiraService().search(jql="type = Bug", max_results=999)
    assert result["ok"] is True
    assert result["issues"] == []
    assert captured["max_results"] == 50
