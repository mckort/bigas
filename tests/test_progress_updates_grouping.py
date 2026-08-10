"""Unit tests for progress-update project grouping helpers."""
from __future__ import annotations

from bigas.resources.product.progress_updates.service import (
    _aggregate_stats,
    _format_done_issues_for_prompt,
    _normalize_done_issue,
    _project_key_from_issue_key,
)


def test_project_key_from_issue_key():
    assert _project_key_from_issue_key("VFA-12") == "VFA"
    assert _project_key_from_issue_key("wayw-3") == "WAYW"


def test_normalize_includes_project_key():
    issue = {
        "key": "BIG-9",
        "fields": {
            "summary": "Ship usage",
            "issuetype": {"name": "Story"},
            "assignee": {"displayName": "Ada"},
        },
    }
    norm = _normalize_done_issue(issue)
    assert norm["project_key"] == "BIG"


def test_aggregate_stats_includes_empty_projects():
    normalized = [
        {
            "key": "VFA-1",
            "project_key": "VFA",
            "summary": "A",
            "issue_type": "Task",
            "assignee": "Ada",
        }
    ]
    stats = _aggregate_stats(normalized, project_keys=["VFA", "WAYW", "BIG"])
    assert stats["total"] == 1
    assert stats["by_project"]["VFA"] == 1
    assert stats["by_project"]["WAYW"] == 0
    assert stats["by_project"]["BIG"] == 0


def test_format_groups_by_project():
    text = _format_done_issues_for_prompt(
        [
            {
                "key": "WAYW-2",
                "project_key": "WAYW",
                "summary": "B",
                "issue_type": "Bug",
                "assignee": "Ada",
            },
            {
                "key": "VFA-1",
                "project_key": "VFA",
                "summary": "A",
                "issue_type": "Task",
                "assignee": "Ada",
            },
        ]
    )
    assert "### VFA" in text
    assert "### WAYW" in text
    assert text.index("### VFA") < text.index("### WAYW")
