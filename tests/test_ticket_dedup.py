"""Unit tests for create_ticket title dedup (BIG-84)."""
from __future__ import annotations

from bigas.tickets.dedup import (
    find_open_duplicate_ticket,
    normalize_ticket_title,
    ticket_titles_collide,
)


def test_normalize_collapses_case_and_whitespace():
    assert normalize_ticket_title("  Meeting   Recording  ") == "meeting recording"


def test_titles_collide_on_same_wording():
    title = "Meeting recording stops around 60 minutes after Firebase token refresh"
    assert ticket_titles_collide(title, title)
    assert ticket_titles_collide(title, f"  {title.upper()}  ")
    assert ticket_titles_collide(title, title + ".")


def test_titles_do_not_collide_on_unrelated_work():
    assert not ticket_titles_collide("Fix Stripe webhook", "Add company labels")
    assert not ticket_titles_collide("Fix login", "Fix login on mobile Safari")


def test_find_open_duplicate_skips_done_and_other_types():
    tickets = [
        {"key": "VFA-1", "title": "Same bug", "status": "Done", "issue_type": "Bug"},
        {"key": "VFA-2", "title": "Same bug", "status": "To Do", "issue_type": "Task"},
        {"key": "VFA-3", "title": "Same bug", "status": "In Progress (AI)", "issue_type": "Bug"},
    ]
    found = find_open_duplicate_ticket(tickets, title="same bug", issue_type="Bug")
    assert found is not None
    assert found["key"] == "VFA-3"
