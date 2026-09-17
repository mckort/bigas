"""Release notes with internal board (BIG-90)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas.resources.product.create_release_notes.formatter import categorize_issue, group_issues
from bigas.resources.product.create_release_notes.service import (
    CreateReleaseNotesService,
    _default_issue_client,
)
from bigas.tickets import store as ticket_store_module
from bigas.tickets.jira_adapter import TicketJiraAdapter
from bigas.tickets.store import get_ticket_store


@pytest.fixture(autouse=True)
def _reset_stores():
    ticket_store_module._store = None
    yield
    ticket_store_module._store = None


def test_default_issue_client_uses_adapter_when_internal_board(monkeypatch):
    monkeypatch.setenv("USE_INTERNAL_BOARD", "true")
    client = _default_issue_client()
    assert isinstance(client, TicketJiraAdapter)


def test_default_issue_client_uses_jira_when_external(monkeypatch):
    monkeypatch.setenv("USE_INTERNAL_BOARD", "false")
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "VFA")
    from bigas.resources.product.create_release_notes.jira_client import JiraClient

    client = _default_issue_client()
    assert isinstance(client, JiraClient)


def test_feature_issue_type_maps_to_new_features():
    assert categorize_issue({"issue_type": "Feature", "labels": []}) == "new_features"
    grouped = group_issues(
        [
            {"key": "VFA-1", "summary": "New checkout", "issue_type": "Feature", "labels": []},
            {"key": "VFA-2", "summary": "Fix crash", "issue_type": "Bug", "labels": []},
        ]
    )
    assert grouped["new_features"][0]["key"] == "VFA-1"
    assert grouped["bug_fixes"][0]["key"] == "VFA-2"


def test_create_release_notes_service_fetches_cut_tickets(monkeypatch):
    monkeypatch.setenv("USE_INTERNAL_BOARD", "true")
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Shipped feature",
        user_id="dev-user",
        key="VFA-50",
        fix_version="1.0.0",
        status="Done",
        issue_type="Feature",
    )
    store.create_ticket(
        board["board_id"],
        title="Not ready",
        user_id="dev-user",
        key="VFA-51",
        fix_version="1.0.0",
        status="Design approval (manual)",
        issue_type="Task",
    )

    captured: dict = {}

    class StubJira:
        def search_issues_by_fix_version(self, **kwargs):
            captured.update(kwargs)
            return TicketJiraAdapter().search_issues_by_fix_version(**kwargs)

        def mark_fix_version_released(self, **kwargs):
            return {"ok": True}

    class StubLLM:
        def complete(self, messages, **kwargs):
            return '{"sections":{"new_features":["Shipped feature"],"improvements":[],"bug_fixes":[]},"social":{},"blog_markdown":""}'

    stub_llm = StubLLM()
    monkeypatch.setattr(
        "bigas.resources.product.create_release_notes.service.get_llm_client",
        lambda **kw: (stub_llm, "test-model"),
    )

    service = CreateReleaseNotesService(jira_client=StubJira())

    result = service.create(fix_version="1.0.0", project_keys=["VFA"])

    keys = [i["key"] for i in result["issues_included"]]
    assert keys == ["VFA-50"]
    assert result["issues_included"][0]["issue_type"] == "Feature"
    assert captured.get("project_keys") == ["VFA"]


def test_board_stores_feature_issue_type():
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    ticket = store.create_ticket(
        board["board_id"],
        title="New capability",
        user_id="dev-user",
        issue_type="Feature",
    )
    assert ticket["issue_type"] == "Feature"
