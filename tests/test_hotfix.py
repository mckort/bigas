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


@patch("bigas.resources.product.hotfix.cherry_pick._request")
def test_find_merged_pr_paginates(mock_request):
    page1 = [
        {
            "number": i,
            "merged_at": "2026-01-01T00:00:00Z",
            "title": f"Unrelated PR {i}",
            "body": "",
            "head": {"ref": f"feature/unrelated-{i}"},
        }
        for i in range(100)
    ]
    page2 = [
        {
            "number": 105,
            "merged_at": "2026-01-02T00:00:00Z",
            "merge_commit_sha": "def456",
            "title": "VFA-42: Fix export",
            "html_url": "https://github.com/acme/app/pull/105",
            "body": "",
            "head": {"ref": "feature/vfa-42"},
        }
    ]
    mock_request.side_effect = [page1, page2]
    pr = find_merged_pr_for_issue(
        token="tok",
        owner="acme",
        repo="app",
        issue_key="VFA-42",
        base_branch="staging",
    )
    assert pr["number"] == 105
    assert mock_request.call_count == 2


@patch("bigas.resources.product.hotfix.service.find_merged_pr_for_issue")
@patch("bigas.resources.product.hotfix.service._github_token", return_value="tok")
def test_hotfix_service_dispatches_workflow(_token, mock_find, monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "VFA:staging,DEFAULT:main")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    mock_find.return_value = {
        "number": 5,
        "merge_commit_sha": "deadbeef",
        "html_url": "https://github.com/mckort/vcfieldassistant/pull/5",
    }

    service = HotfixService()
    with patch.object(
        service,
        "_try_dispatch_workflow",
        return_value={"workflow": "cherry_pick.yml", "workflow_ref": "main"},
    ):
        result = service.cherry_pick_to_main(issue_key="VFA-1")

    assert result["ok"] is True
    assert result["mode"] == "workflow_dispatch"
    assert result["workflow"] == "cherry_pick.yml"


@patch("bigas.resources.product.hotfix.service.find_merged_pr_for_issue")
@patch("bigas.resources.product.hotfix.service._github_token", return_value="tok")
def test_hotfix_service_requires_workflow(_token, mock_find, monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "VFA:staging,DEFAULT:main")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    mock_find.return_value = {
        "number": 5,
        "merge_commit_sha": "deadbeef",
        "html_url": "https://github.com/mckort/vcfieldassistant/pull/5",
    }

    service = HotfixService()
    with pytest.raises(HotfixError, match="cherry_pick.yml"):
        service.cherry_pick_to_main(issue_key="VFA-1", use_workflow=False)


@patch("bigas.resources.product.hotfix.service.find_merged_pr_for_issue")
@patch("bigas.resources.product.hotfix.service._github_token", return_value="tok")
def test_hotfix_service_workflow_dispatch_failure(_token, mock_find, monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "VFA:staging,DEFAULT:main")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    mock_find.return_value = {
        "number": 5,
        "merge_commit_sha": "deadbeef",
        "html_url": "https://github.com/mckort/vcfieldassistant/pull/5",
    }

    service = HotfixService()
    with patch.object(service, "_try_dispatch_workflow", return_value=None):
        with pytest.raises(HotfixError, match="Could not dispatch"):
            service.cherry_pick_to_main(issue_key="VFA-1")


def test_hotfix_same_branch_rejected(monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "BIG:main,DEFAULT:main")
    service = HotfixService()
    with pytest.raises(HotfixError, match="does not use a staging branch"):
        service.cherry_pick_to_main(issue_key="BIG-1", use_workflow=False)
