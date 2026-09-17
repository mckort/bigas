"""Unit tests for create_ticket title dedup (BIG-84)."""
from __future__ import annotations

from bigas.tickets.dedup import (
    find_open_duplicate_ticket,
    normalize_ticket_title,
    ticket_titles_collide,
)


def test_normalize_collapses_case_and_whitespace():
    assert normalize_ticket_title("  Meeting   Recording  ") == "meeting recording"
    assert normalize_ticket_title("Fix login failure.") == "fix login failure"


def test_titles_collide_on_same_wording():
    title = "Meeting recording stops around 60 minutes after Firebase token refresh"
    assert ticket_titles_collide(title, title)
    assert ticket_titles_collide(title, f"  {title.upper()}  ")
    assert ticket_titles_collide(title, title + ".")


def test_titles_do_not_collide_on_unrelated_work():
    assert not ticket_titles_collide("Fix Stripe webhook", "Add company labels")
    assert not ticket_titles_collide("Fix login", "Fix login on mobile Safari")


def test_titles_do_not_collide_on_version_suffix():
    base = "Deploy notification service"
    assert not ticket_titles_collide(base, f"{base} v2")
    assert not ticket_titles_collide(f"{base} v2", base)


def test_short_titles_match_with_trailing_punctuation():
    assert ticket_titles_collide("Fix login failure", "Fix login failure.")


def test_find_open_duplicate_skips_done_and_other_types():
    tickets = [
        {"key": "VFA-1", "title": "Same bug", "status": "Done", "issue_type": "Bug"},
        {"key": "VFA-2", "title": "Same bug", "status": "To Do", "issue_type": "Task"},
        {"key": "VFA-3", "title": "Same bug", "status": "In Progress (AI)", "issue_type": "Bug"},
    ]
    found = find_open_duplicate_ticket(tickets, title="same bug", issue_type="Bug")
    assert found is not None
    assert found["key"] == "VFA-3"


def test_find_open_duplicate_skips_terminal_statuses_case_insensitive():
    tickets = [
        {"key": "VFA-1", "title": "Same bug", "status": "Closed", "issue_type": "Bug"},
        {"key": "VFA-2", "title": "Same bug", "status": "resolved", "issue_type": "Bug"},
    ]
    assert find_open_duplicate_ticket(tickets, title="same bug", issue_type="Bug") is None


def test_find_open_duplicate_handles_none_ticket_list():
    assert find_open_duplicate_ticket(None, title="anything") is None
