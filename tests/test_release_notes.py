"""Release notes read the board cut (Done + Final approval) and Feature type."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas.resources.product.create_release_notes.formatter import categorize_issue
from bigas.resources.product.create_release_notes.jira_client import JiraClient
from bigas.resources.product.create_release_notes.service import (
    CreateReleaseNotesService,
    _default_issue_client,
    _resolved_project_keys,
    filter_release_cut_issues,
)
from bigas.tickets.jira_import import _map_issue_type
from bigas.tickets import store as ticket_store_module
from bigas.tickets.jira_adapter import TicketJiraAdapter
from bigas.tickets.release_store import reset_release_store_for_tests
from bigas.tickets.store import get_ticket_store


@pytest.fixture(autouse=True)
def _reset_stores():
    ticket_store_module._store = None
    reset_release_store_for_tests()
    yield
    ticket_store_module._store = None
    reset_release_store_for_tests()


def test_default_issue_client_uses_internal_adapter(monkeypatch):
    monkeypatch.setattr("bigas.tickets.config.use_internal_board", lambda: True)
    assert isinstance(_default_issue_client(), TicketJiraAdapter)


def test_default_issue_client_uses_jira_when_board_off(monkeypatch):
    monkeypatch.setattr("bigas.tickets.config.use_internal_board", lambda: False)
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "dev@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "VFA")
    assert isinstance(_default_issue_client(), JiraClient)


def test_map_issue_type_new_feature_variants():
    assert _map_issue_type("New Feature") == "Feature"
    assert _map_issue_type("new-feature") == "Feature"
    assert _map_issue_type("Feature") == "Feature"


def test_map_issue_type_improvement():
    assert _map_issue_type("Improvement") == "Improvement"
    assert _map_issue_type("improvement") == "Improvement"
    assert _map_issue_type("improvements") == "Improvement"
    assert _map_issue_type("enhancement") == "Improvement"


def test_filter_release_cut_accepts_status_key():
    kept = {
        issue["key"]
        for issue in filter_release_cut_issues(
            [
                {"key": "VFA-1", "status": "Done"},
                {"key": "VFA-2", "_status": "Final approval (manual)"},
            ]
        )
    }
    assert kept == {"VFA-1", "VFA-2"}


def test_resolved_project_keys_merges_internal_store(monkeypatch):
    monkeypatch.setattr("bigas.tickets.config.use_internal_board", lambda: True)
    monkeypatch.setattr("bigas.portfolio.jira_project_keys", lambda: ["VFA"])

    store = get_ticket_store()
    board = store.create_board("dev-user", name="Custom Board", project_key="CUSTOM")
    store.create_ticket(board["board_id"], title="Ship it", user_id="dev-user", key="CUSTOM-1")

    keys = _resolved_project_keys(TicketJiraAdapter(), None)
    assert keys == ["CUSTOM", "VFA"]


def test_filter_release_cut_keeps_done_and_final_approval():
    kept = {
        issue["key"]
        for issue in filter_release_cut_issues(
            [
                {"key": "VFA-1", "_status": "Done"},
                {"key": "VFA-2", "_status": "Final approval (manual)"},
                {"key": "VFA-3", "_status": "In Progress (AI)"},
                {"key": "VFA-4", "_status": "To Do"},
                {"key": "VFA-5", "_status": "final review"},
            ]
        )
    }
    assert kept == {"VFA-1", "VFA-2", "VFA-5"}


def test_feature_type_is_new_feature():
    assert categorize_issue({"issue_type": "Feature"}) == "new_features"
    assert categorize_issue({"issue_type": "Task"}) == "improvements"
    assert categorize_issue({"issue_type": "Improvement"}) == "improvements"
    assert categorize_issue({"issue_type": "Bug"}) == "bug_fixes"


def test_create_includes_board_cut_and_features(monkeypatch):
    monkeypatch.setattr("bigas.tickets.config.use_internal_board", lambda: True)

    class _SilentLLM:
        def complete(self, **kwargs):
            return ""

    monkeypatch.setattr(
        "bigas.resources.product.create_release_notes.service.get_llm_client",
        lambda **kwargs: (_SilentLLM(), "fake"),
    )

    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title='News search also to cover "overlapping competitors"',
        user_id="dev-user",
        key="VFA-72",
        issue_type="Feature",
        status="Final approval (manual)",
        fix_version="0.7.0",
    )
    store.create_ticket(
        board["board_id"],
        title="Meeting recording stops around 60 minutes after Firebase token refresh",
        user_id="dev-user",
        key="VFA-90",
        issue_type="Bug",
        status="Done",
        fix_version="0.7.0",
    )
    store.create_ticket(
        board["board_id"],
        title="Add fund-fit criteria",
        user_id="dev-user",
        key="VFA-65",
        issue_type="Task",
        status="To Do",
        fix_version="0.7.0",
    )

    result = CreateReleaseNotesService().create(
        fix_version="0.7.0",
        project_keys=["VFA"],
    )
    keys = {issue["key"] for issue in result["issues_included"]}
    assert keys == {"VFA-72", "VFA-90"}
    assert any("competitors" in line.lower() for line in result["sections"]["features"])
    assert any("recording" in line.lower() for line in result["sections"]["bug_fixes"])
    assert "Add fund-fit criteria" not in " ".join(result["sections"]["improvements"])
