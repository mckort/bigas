"""Tests for fetch_github_activity (no network)."""
from __future__ import annotations

from bigas.resources.product.github_activity import (
    fetch_github_activity,
    resolve_activity_since,
)
from bigas.resources.product.progress_updates.github_commits import GitHubCommitsError
import pytest


def test_resolve_activity_since_iso_date():
    cutoff = resolve_activity_since(since="2026-08-17")
    assert cutoff.year == 2026
    assert cutoff.month == 8
    assert cutoff.day == 17


def test_resolve_activity_since_rejects_bad_date():
    with pytest.raises(GitHubCommitsError, match="Invalid since date"):
        resolve_activity_since(since="not-a-date")


def test_fetch_github_activity_returns_commits_and_prs(monkeypatch):
    class FakeClient:
        _headers = {}

        def list_commits_since(self, *, owner, repo, since, per_page=100, max_pages=3):
            assert owner == "mckort"
            assert repo == "vcfieldassistant"
            return [
                {
                    "sha": "abc123456",
                    "html_url": "https://github.com/mckort/vcfieldassistant/commit/abc123456",
                    "parents": [{"sha": "1"}],
                    "commit": {
                        "message": "Open self-serve Angel Small signup (#165)",
                        "author": {
                            "name": "Marcus",
                            "date": "2026-08-27T14:00:00Z",
                        },
                    },
                }
            ]

    monkeypatch.setattr(
        "bigas.resources.product.github_activity.list_merged_pulls_since",
        lambda *args, **kwargs: [
            {
                "number": 165,
                "title": "Open self-serve Angel Small signup",
                "merged_at": "2026-08-27T14:04:11+00:00",
                "html_url": "https://github.com/mckort/vcfieldassistant/pull/165",
                "user": "mckort",
            }
        ],
    )
    monkeypatch.setattr(
        "bigas.resources.product.github_activity.project_repo_map_from_env",
        lambda: {"VFA": "mckort/vcfieldassistant"},
    )
    result = fetch_github_activity(
        project_key="VFA",
        since="2026-08-17",
        client=FakeClient(),
    )
    assert result["ok"] is True
    assert result["repo"] == "mckort/vcfieldassistant"
    assert result["since"] == "2026-08-17"
    assert result["commits"][0]["subject"].startswith("Open self-serve")
    assert result["pull_requests"][0]["number"] == 165
    assert result["stats"]["commits"] == 1


def test_fetch_github_activity_requires_repo():
    with pytest.raises(GitHubCommitsError, match="required"):
        fetch_github_activity(project_key="ZZZ", since="2026-08-17")
