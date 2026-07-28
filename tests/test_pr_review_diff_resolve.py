"""Tests for CTO PR review diff resolution helpers."""

from __future__ import annotations

from bigas.resources.cto.endpoints import _resolve_pr_diff_text


class _FakeGH:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def get_pr_diff(self, *, owner: str, repo: str, pr_number: int) -> str:
        self.calls += 1
        assert owner == "mckort"
        assert repo == "bigas"
        assert pr_number == 62
        return self.text


def test_resolve_pr_diff_prefers_non_empty_caller_diff(monkeypatch):
    fake = _FakeGH("from-api")
    monkeypatch.setattr(
        "bigas.resources.cto.endpoints.GitHubPRCommentClient",
        lambda token: fake,
    )
    out = _resolve_pr_diff_text(
        diff="diff --git a/x b/x\n",
        owner="mckort",
        repo_name="bigas",
        pr_number=62,
        github_token="t",
    )
    assert out.startswith("diff --git")
    assert fake.calls == 0


def test_resolve_pr_diff_fetches_when_empty(monkeypatch):
    fake = _FakeGH("api-diff-body")
    monkeypatch.setattr(
        "bigas.resources.cto.endpoints.GitHubPRCommentClient",
        lambda token: fake,
    )
    out = _resolve_pr_diff_text(
        diff="   ",
        owner="mckort",
        repo_name="bigas",
        pr_number=62,
        github_token="t",
    )
    assert out == "api-diff-body"
    assert fake.calls == 1


def test_resolve_pr_diff_fetches_when_missing(monkeypatch):
    fake = _FakeGH("api-diff-missing")
    monkeypatch.setattr(
        "bigas.resources.cto.endpoints.GitHubPRCommentClient",
        lambda token: fake,
    )
    out = _resolve_pr_diff_text(
        diff=None,
        owner="mckort",
        repo_name="bigas",
        pr_number=62,
        github_token="t",
    )
    assert out == "api-diff-missing"
    assert fake.calls == 1
