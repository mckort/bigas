import os

os.environ.setdefault("CHAT_STORAGE_MODE", "memory")

from bigas.resources.product.create_jira_issue.service import CreateJiraIssueService
from bigas.resources.product.create_release_notes.jira_client import JiraError
from bigas.resources.product.update_ticket import (
    UpdateTicketError,
    UpdateTicketService,
)
from bigas.tickets import store as ticket_store_module
from bigas.tickets.constants import (
    CREATABLE_ISSUE_TYPES,
    ISSUE_TYPES,
    is_in_release_cut,
    normalize_issue_type,
    resolve_column_status,
    unknown_column_error,
)
from bigas.tickets.release_store import reset_release_store_for_tests


def setup_function():
    ticket_store_module._store = None
    reset_release_store_for_tests()
    os.environ.pop("USE_INTERNAL_BOARD", None)


def test_release_cut_includes_final_approval():
    assert is_in_release_cut("Done") is True
    assert is_in_release_cut("Final approval (manual)") is True
    assert is_in_release_cut("final approval", project_key="VFA") is True
    assert is_in_release_cut("Final Review", project_key="VFA") is True
    assert is_in_release_cut("To Do") is False
    assert is_in_release_cut("In Progress (AI)") is False


def test_resolve_final_review_alias():
    assert resolve_column_status("Final Review", project_key="VFA") == (
        "Final approval (manual)"
    )
    assert resolve_column_status("final approval", project_key="VFA") == (
        "Final approval (manual)"
    )
    assert resolve_column_status("Final approval (manual)", project_key="VFA") == (
        "Final approval (manual)"
    )


def test_resolve_personal_in_progress():
    assert resolve_column_status("in progress", project_key=None) == "In Progress"
    assert resolve_column_status("in progress", project_key="VFA") == "In Progress (AI)"


def test_resolve_unknown_is_none():
    assert resolve_column_status("Not a column", project_key="VFA") is None
    assert "Final approval (manual)" in unknown_column_error(
        "Not a column", project_key="VFA"
    )


def test_create_and_update_column_via_services():
    created = CreateJiraIssueService().create(
        project_key="VFA",
        summary="Show last seen",
        description="Admin last API activity",
        status="Final Review",
    )
    assert created["ok"] is True
    assert created["status"] == "Final approval (manual)"

    created_todo = CreateJiraIssueService().create(
        project_key="VFA",
        summary="Move me",
        description="Needs Final Review",
    )
    assert created_todo["status"] == "To Do"
    moved = UpdateTicketService().update(
        issue_key=created_todo["key"],
        status="Final Review",
    )
    assert moved["ok"] is True
    assert moved["status"] == "Final approval (manual)"
    assert moved["key"] == created_todo["key"]


def test_update_rejects_unknown_column():
    created = CreateJiraIssueService().create(
        project_key="VFA",
        summary="Bad column",
        description="Stay in To Do",
    )
    try:
        UpdateTicketService().update(
            issue_key=created["key"],
            status="Not a column",
        )
    except UpdateTicketError as exc:
        assert "unknown column" in str(exc).lower()
    else:
        raise AssertionError("expected UpdateTicketError")


def test_update_passes_user_id_to_set_status(monkeypatch):
    captured = {}

    class FakeTicketService:
        def set_status(self, issue_key, status, *, user_id=None):
            captured["issue_key"] = issue_key
            captured["status"] = status
            captured["user_id"] = user_id
            return {
                "key": issue_key,
                "url": f"/board?ticket={issue_key}",
                "title": "Moved",
                "status": "In Progress",
            }

    monkeypatch.setattr(
        "bigas.tickets.service.TicketService",
        FakeTicketService,
    )
    monkeypatch.setattr(
        "bigas.tickets.config.use_internal_board",
        lambda: True,
    )

    result = UpdateTicketService().update(
        issue_key="VFA-99",
        status="In Progress",
        user_id="agent-user-1",
    )
    assert result["ok"] is True
    assert captured == {
        "issue_key": "VFA-99",
        "status": "In Progress",
        "user_id": "agent-user-1",
    }


def test_update_description_via_service():
    created = CreateJiraIssueService().create(
        project_key="VFA",
        summary="Rewrite brief",
        description="Old text",
    )
    assert created["ok"] is True
    updated = UpdateTicketService().update(
        issue_key=created["key"],
        description="## New brief\n\nDo the thing.",
    )
    assert updated["ok"] is True
    assert updated["description"] == "## New brief\n\nDo the thing."
    assert updated["status"] == "To Do"
    assert updated["key"] == created["key"]


_JIRA_RESULT_KEYS = {
    "ok",
    "key",
    "url",
    "summary",
    "status",
    "description",
    "source",
}


class _FakeJiraConfig:
    base_url = "https://example.atlassian.net"

    @staticmethod
    def from_env():
        return _FakeJiraConfig()


class _FakeJiraClient:
    def __init__(self, config):
        self.config = config
        self.transitions = []
        self.descriptions = []
        self.fail_read = False

    def transition_issue(self, issue_key, *, to_status_name, comment=None):
        self.transitions.append((issue_key, to_status_name))

    def update_description(self, issue_key, description_markdown):
        self.descriptions.append((issue_key, description_markdown))

    def get_issue(self, issue_key, *, fields=None, expand=None):
        if self.fail_read:
            raise JiraError("read failed")
        status_name = self.transitions[-1][1] if self.transitions else "To Do"
        return {
            "key": issue_key,
            "fields": {
                "summary": "Existing title",
                "status": {"name": status_name},
                "description": "Stored body",
            },
        }


def _use_fake_jira(monkeypatch, *, fail_read=False):
    created = {}

    class Client(_FakeJiraClient):
        def __init__(self, config):
            super().__init__(config)
            self.fail_read = fail_read
            created["client"] = self

    monkeypatch.setattr("bigas.tickets.config.use_internal_board", lambda: False)
    monkeypatch.setattr(
        "bigas.resources.product.update_ticket.JiraClient",
        Client,
    )
    monkeypatch.setattr(
        "bigas.resources.product.update_ticket.JiraConfig",
        _FakeJiraConfig,
    )
    return created


def test_jira_update_description_returns_current_status(monkeypatch):
    created = _use_fake_jira(monkeypatch)
    result = UpdateTicketService().update(
        issue_key="VFA-48",
        description="## New brief\n\nDo the thing.",
    )
    assert set(result) == _JIRA_RESULT_KEYS
    assert result["ok"] is True
    assert result["source"] == "jira"
    assert result["key"] == "VFA-48"
    assert result["status"] == "To Do"
    assert result["description"] == "## New brief\n\nDo the thing."
    assert result["summary"] == "Existing title"
    assert result["url"] == "https://example.atlassian.net/browse/VFA-48"
    client = created["client"]
    assert client.transitions == []
    assert client.descriptions == [("VFA-48", "## New brief\n\nDo the thing.")]


def test_jira_update_status_returns_current_description(monkeypatch):
    created = _use_fake_jira(monkeypatch)
    result = UpdateTicketService().update(
        issue_key="VFA-48",
        status="Final Review",
    )
    assert set(result) == _JIRA_RESULT_KEYS
    assert result["status"] == "Final approval (manual)"
    assert result["description"] == "Stored body"
    assert result["summary"] == "Existing title"
    assert created["client"].transitions == [("VFA-48", "Final approval (manual)")]
    assert created["client"].descriptions == []


def test_jira_update_keeps_written_fields_when_reread_fails(monkeypatch):
    _use_fake_jira(monkeypatch, fail_read=True)
    result = UpdateTicketService().update(
        issue_key="VFA-48",
        description="Replacement body",
    )
    assert set(result) == _JIRA_RESULT_KEYS
    assert result["ok"] is True
    assert result["source"] == "jira"
    assert result["description"] == "Replacement body"
    assert result["status"] is None
    assert result["summary"] == "VFA-48"
    assert result["url"] == "https://example.atlassian.net/browse/VFA-48"


def test_update_rejects_missing_status_and_description():
    try:
        UpdateTicketService().update(issue_key="VFA-1")
    except UpdateTicketError as exc:
        assert "status or description" in str(exc).lower()
    else:
        raise AssertionError("expected UpdateTicketError")


def test_normalize_issue_type_accepts_improvement():
    assert "Improvement" in ISSUE_TYPES
    assert "Improvement" in CREATABLE_ISSUE_TYPES
    assert normalize_issue_type("Improvement") == "Improvement"
    assert normalize_issue_type("improvements") == "Improvement"
    assert normalize_issue_type("enhancement") == "Improvement"
    assert normalize_issue_type("feature") == "Feature"
    assert normalize_issue_type("") == "Task"
