"""Unit tests for progress-update git commit helpers."""
from __future__ import annotations

from bigas.resources.product.progress_updates.github_commits import (
    format_commits_for_prompt,
    normalize_commit,
)
from bigas.resources.product.progress_updates.prompts import (
    build_progress_updates_user_prompt,
)


def test_normalize_skips_merge_commits():
    raw = {
        "sha": "abc123456",
        "html_url": "https://github.com/o/r/commit/abc",
        "parents": [{"sha": "1"}, {"sha": "2"}],
        "commit": {
            "message": "Merge pull request #1",
            "author": {"name": "Bot"},
        },
    }
    assert normalize_commit(raw, project_key="BIG", repo="mckort/bigas") is None


def test_normalize_marks_autofix():
    raw = {
        "sha": "def456789",
        "parents": [{"sha": "1"}],
        "commit": {
            "message": "Fix review findings [bigas-autofix]\n\nbody",
            "author": {"name": "Cursor"},
        },
    }
    n = normalize_commit(raw, project_key="BIG", repo="mckort/bigas")
    assert n is not None
    assert n["is_autofix"] is True
    assert n["subject"].startswith("Fix review findings")


def test_normalize_ignores_autofix_marker_in_squash_body():
    raw = {
        "sha": "aaa111222",
        "parents": [{"sha": "1"}],
        "commit": {
            "message": (
                "Give Angel Standard and VC Team a 30-day Stripe trial (#75)\n\n"
                "Co-authored-by: Cursor\n\n"
                "[bigas-autofix] Fix checkout session metadata"
            ),
            "author": {"name": "Marcus"},
        },
    }
    n = normalize_commit(raw, project_key="VFA", repo="mckort/vcfieldassistant")
    assert n is not None
    assert n["is_autofix"] is False
    assert n["subject"].startswith("Give Angel Standard")


def test_format_commits_for_prompt():
    text = format_commits_for_prompt(
        {
            "BIG": [
                {
                    "subject": "Add usage providers",
                    "is_autofix": False,
                }
            ],
            "WAYW": [],
        },
        stats={
            "BIG": {"repo": "mckort/bigas", "total": 1, "autofix": 0},
            "WAYW": {"repo": "mckort/roadpal", "total": 0, "autofix": 0},
        },
    )
    assert "### BIG (mckort/bigas)" in text
    assert "Add usage providers" in text
    assert "no non-merge commits" in text


def test_prompt_includes_git_and_inactive_line_guidance():
    prompt = build_progress_updates_user_prompt(
        stats={"total": 0, "by_type": {}, "by_project": {"VFA": 0, "BIG": 0}},
        done_issues_text="(none)",
        days=7,
        git_commits_text="### BIG\n- Ship feature",
        git_stats={
            "VFA": {"total": 0, "autofix": 0, "repo": "mckort/vcfieldassistant"},
            "BIG": {"total": 1, "autofix": 0, "repo": "mckort/bigas"},
        },
    )
    assert "git commits" in prompt.lower()
    assert "Projects with activity" in prompt
    assert "BIG" in prompt
    assert "No activity:" in prompt
