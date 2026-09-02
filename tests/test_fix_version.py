"""Tests for fix version assignment (BIG-42)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bigas.resources.product.fix_version import ensure_active_fix_version
from bigas.tickets.jira_adapter import TicketJiraAdapter
from bigas.tickets.release_store import reset_release_store_for_tests


def test_ensure_active_fix_version_jira_client():
    jira = MagicMock()
    jira.ensure_issue_fix_version.return_value = "0.9.0"
    assert (
        ensure_active_fix_version(jira, issue_key="VFA-1", project_key="VFA") == "0.9.0"
    )
    jira.ensure_issue_fix_version.assert_called_once_with("VFA-1", project_key="VFA")


def test_ticket_adapter_assigns_active_fix_version(monkeypatch):
    reset_release_store_for_tests()
    monkeypatch.setenv("BIGAS_PROJECT_ACTIVE_FIX_VERSION", "VFA:0.9.0")
    store = TicketJiraAdapter()._store
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    ticket = store.create_ticket(
        board["board_id"],
        title="Bug fix",
        user_id="dev-user",
        key="VFA-100",
    )
    assert ticket.get("fix_version") in (None, "")

    adapter = TicketJiraAdapter()
    applied = adapter.ensure_issue_fix_version("VFA-100", project_key="VFA")
    assert applied == "0.9.0"
    updated = store.get_ticket_by_key("VFA-100")
    assert updated["fix_version"] == "0.9.0"


def test_ticket_adapter_keeps_existing_fix_version(monkeypatch):
    monkeypatch.setenv("BIGAS_PROJECT_ACTIVE_FIX_VERSION", "VFA:0.9.0")
    store = TicketJiraAdapter()._store
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Already versioned",
        user_id="dev-user",
        key="VFA-101",
        fix_version="1.0.0",
    )
    adapter = TicketJiraAdapter()
    assert adapter.ensure_issue_fix_version("VFA-101", project_key="VFA") == "1.0.0"
    updated = store.get_ticket_by_key("VFA-101")
    assert updated["fix_version"] == "1.0.0"
