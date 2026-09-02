"""Tests for hotfix cherry-pick helpers (BIG-42)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bigas.resources.product.hotfix.cherry_pick import CherryPickError, find_merged_pr_for_issue
from bigas.resources.product.hotfix.service import HotfixError, HotfixService


@patch("bigas.resources.product.hotfix.cherry_pick._request")
def test_find_merged_pr_for_issue(mock_request):
    mock_request.return_value = [
        {
            "number": 9,
            "merged_at": "2026-01-01T00:00:00Z",
            "merge_commit_sha": "abc123",
            "title": "VFA-42: Fix export",
            "html_url": "https://github.com/acme/app/pull/9",
            "body": "",
            "head": {"ref": "feature/vfa-42"},
        }
    ]
    pr = find_merged_pr_for_issue(
        token="tok",
        owner="acme",
        repo="app",
        issue_key="VFA-42",
        base_branch="staging",
    )
    assert pr["number"] == 9
    assert pr["merge_commit_sha"] == "abc123"


@patch("bigas.resources.product.hotfix.cherry_pick._request")
def test_find_merged_pr_missing_raises(mock_request):
    mock_request.return_value = []
    with pytest.raises(CherryPickError, match="No merged PR"):
        find_merged_pr_for_issue(
            token="tok",
            owner="acme",
            repo="app",
            issue_key="VFA-99",
            base_branch="staging",
        )


@patch("bigas.resources.product.hotfix.service.open_pull_request")
@patch("bigas.resources.product.hotfix.service.cherry_pick_commit_to_branch")
@patch("bigas.resources.product.hotfix.service.find_merged_pr_for_issue")
@patch("bigas.resources.product.hotfix.service._github_token", return_value="tok")
def test_hotfix_service_api_fallback(
    _token,
    mock_find,
    mock_cherry,
    mock_open_pr,
    monkeypatch,
):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "VFA:staging,DEFAULT:main")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    mock_find.return_value = {
        "number": 5,
        "merge_commit_sha": "deadbeef",
        "html_url": "https://github.com/mckort/vcfieldassistant/pull/5",
    }
    mock_cherry.return_value = ("hotfix/vfa-1", "cafebabe")
    mock_open_pr.return_value = "https://github.com/mckort/vcfieldassistant/pull/6"

    service = HotfixService()
    with patch.object(service, "_try_dispatch_workflow", return_value=None):
        result = service.cherry_pick_to_main(issue_key="VFA-1", use_workflow=False)

    assert result["ok"] is True
    assert result["mode"] == "github_api"
    assert result["pr_url"].endswith("/pull/6")
    mock_cherry.assert_called_once()


def test_hotfix_same_branch_rejected(monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "BIG:main,DEFAULT:main")
    service = HotfixService()
    with pytest.raises(HotfixError, match="does not use a staging branch"):
        service.cherry_pick_to_main(issue_key="BIG-1", use_workflow=False)
