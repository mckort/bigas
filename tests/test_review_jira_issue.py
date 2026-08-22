"""Unit tests for review_jira_issue MCP tool (no network)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas.resources.product.review_jira_issue.prompts import PM_REVIEW_MARKER
from bigas.resources.product.review_jira_issue.service import (
    ReviewJiraIssueError,
    ReviewJiraIssueService,
    _recommend_advance,
)


def test_recommend_advance_parses_header():
    assert _recommend_advance("### Recommendation\nDo not advance yet — privacy") is False
    assert _recommend_advance("### Recommendation\nAdvance — Brief is clear") is True
    assert _recommend_advance("No header here") is None


def test_review_jira_issue_requires_issue_key():
    with pytest.raises(ReviewJiraIssueError, match="issue_key"):
        ReviewJiraIssueService().review(issue_key="")


def test_review_jira_issue_success(monkeypatch):
    class FakeLookup:
        def lookup(self, *, issue_key=None, project_key=None):
            assert issue_key == "VFA-17"
            return {
                "ok": True,
                "issue": {
                    "key": "VFA-17",
                    "summary": "Founder section in term sheet",
                    "status": "Description approval (Manual)",
                    "issue_type": "Task",
                    "url": "https://example.atlassian.net/browse/VFA-17",
                    "description": "## Brief\nFounder section in the deal memo",
                    "human_comments": "- [2026-08-22 08:55] Marcus:\n  Abstract the test",
                },
            }

    class FakeLLM:
        def complete(self, messages, **kwargs):
            assert any("Product Manager" in m["content"] for m in messages if m["role"] == "system")
            user = next(m["content"] for m in messages if m["role"] == "user")
            assert "Abstract the test" in user
            assert "deal memo" in user
            return (
                "### Recommendation\nDo not advance yet — keep assessments restricted.\n\n"
                "### Product view\nShow traits, not the raw test.\n\n"
                "### Scope\nv1 from transcripts only.\n\n"
                "### Risks\nDerived traits are still sensitive.\n\n"
                "### Open questions\nNone."
            )

    posted = {}

    class FakeJira:
        def __init__(self, config):
            self._config = config

        def add_or_update_marked_comment(self, issue_key, body_text, *, marker):
            posted["key"] = issue_key
            posted["body"] = body_text
            posted["marker"] = marker
            return {"id": "10999", "updated": False}

    monkeypatch.setattr(
        "bigas.resources.product.review_jira_issue.service.LookupJiraService",
        FakeLookup,
    )
    monkeypatch.setattr(
        "bigas.resources.product.review_jira_issue.service.get_llm_client",
        lambda **kwargs: (FakeLLM(), "test-model"),
    )
    monkeypatch.setattr(
        "bigas.resources.product.review_jira_issue.service.JiraClient",
        FakeJira,
    )
    monkeypatch.setattr(
        "bigas.resources.product.review_jira_issue.service.JiraConfig",
        type("C", (), {"from_env": staticmethod(lambda: object())})(),
    )

    result = ReviewJiraIssueService().review(
        issue_key="https://scaleupadvisor.atlassian.net/browse/VFA-17",
        instructions="See my comment",
    )
    assert result["ok"] is True
    assert result["key"] == "VFA-17"
    assert result["recommend_advance"] is False
    assert result["comment_posted"] is True
    assert "Do not advance yet" in result["review"]
    assert "Founder section in term sheet" in result["review"]
    assert "bigas://action/jira_transition?issue=VFA-17" in result["review"]
    assert posted["key"] == "VFA-17"
    assert posted["marker"] == PM_REVIEW_MARKER
    assert "Do not advance yet" in posted["body"]
    assert "bigas://" not in posted["body"]


def test_review_jira_issue_can_skip_comment(monkeypatch):
    class FakeLookup:
        def lookup(self, *, issue_key=None, project_key=None):
            return {
                "ok": True,
                "issue": {
                    "key": "BIG-1",
                    "summary": "Title",
                    "url": "https://example.atlassian.net/browse/BIG-1",
                    "description": "Body",
                    "human_comments": "(none)",
                },
            }

    class FakeLLM:
        def complete(self, messages, **kwargs):
            return "### Recommendation\nAdvance — ready."

    monkeypatch.setattr(
        "bigas.resources.product.review_jira_issue.service.LookupJiraService",
        FakeLookup,
    )
    monkeypatch.setattr(
        "bigas.resources.product.review_jira_issue.service.get_llm_client",
        lambda **kwargs: (FakeLLM(), "test-model"),
    )

    result = ReviewJiraIssueService().review(issue_key="BIG-1", post_comment=False)
    assert result["comment_posted"] is False
    assert result["recommend_advance"] is True


def test_manifest_includes_review_jira_issue():
    from bigas.resources.product.endpoints import get_manifest

    tools = {t["name"]: t for t in get_manifest()["tools"]}
    tool = tools["review_jira_issue"]
    assert tool["path"] == "/mcp/tools/review_jira_issue"
    assert tool["parameters"]["required"] == ["issue_key"]
    assert "product" in tool["description"].lower()
