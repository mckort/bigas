import os

os.environ.setdefault("CHAT_STORAGE_MODE", "memory")

from bigas.resources.product.create_jira_issue.service import CreateJiraIssueService
from bigas.resources.product.update_ticket import (
    UpdateTicketError,
    UpdateTicketService,
)
from bigas.tickets import store as ticket_store_module
from bigas.tickets.constants import resolve_column_status, unknown_column_error
from bigas.tickets.release_store import reset_release_store_for_tests


def setup_function():
    ticket_store_module._store = None
    reset_release_store_for_tests()
    os.environ.pop("USE_INTERNAL_BOARD", None)


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
