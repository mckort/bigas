"""Tests for BIGAS_CTO_AUTO_MERGE squash-merge helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bigas.resources.cto.endpoints import _maybe_auto_merge_pr
from bigas.resources.cto.pr_review.github_client import (
    GitHubMergeNotReadyError,
    GitHubPRCommentClient,
    GitHubPRCommentError,
)


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
def test_maybe_auto_merge_skipped_when_disabled(mock_discord, monkeypatch):
    monkeypatch.delenv("BIGAS_CTO_AUTO_MERGE", raising=False)
    result = _maybe_auto_merge_pr(
        repo="acme/app",
        pr_number=12,
        pr_url="https://github.com/acme/app/pull/12",
        github_token="tok",
    )
    assert result.get("skipped") is True
    mock_discord.assert_not_called()


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
@patch("bigas.resources.cto.endpoints.GitHubPRCommentClient")
def test_maybe_auto_merge_skips_quietly_when_already_merged(
    mock_client_cls, mock_discord, monkeypatch
):
    monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", "true")
    client = MagicMock()
    client.get_pull_request.return_value = {"merged": True, "node_id": "PR_x"}
    mock_client_cls.return_value = client

    result = _maybe_auto_merge_pr(
        repo="acme/app",
        pr_number=12,
        pr_url="https://github.com/acme/app/pull/12",
        github_token="tok",
    )

    assert result.get("skipped") is True
    assert result.get("reason") == "pr_already_merged"
    assert result.get("merged") is True
    client.merge_pull_request.assert_not_called()
    mock_discord.assert_not_called()


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
@patch("bigas.resources.cto.endpoints.GitHubPRCommentClient")
def test_maybe_auto_merge_skips_quietly_on_405_when_merged(
    mock_client_cls, mock_discord, monkeypatch
):
    monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", "true")
    client = MagicMock()
    # First check (pre-merge) says open; merge 405; re-check says merged.
    client.get_pull_request.side_effect = [
        {"merged": False, "node_id": "PR_x"},
        {"merged": True, "node_id": "PR_x"},
    ]
    client.merge_pull_request.side_effect = GitHubMergeNotReadyError(
        "Pull Request is not mergeable"
    )
    mock_client_cls.return_value = client

    result = _maybe_auto_merge_pr(
        repo="acme/app",
        pr_number=12,
        pr_url="https://github.com/acme/app/pull/12",
        github_token="tok",
    )

    assert result.get("skipped") is True
    assert result.get("reason") == "pr_already_merged"
    client.enable_pull_request_auto_merge.assert_not_called()
    mock_discord.assert_not_called()


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
@patch("bigas.resources.cto.endpoints.GitHubPRCommentClient")
def test_maybe_auto_merge_success_posts_discord(
    mock_client_cls, mock_discord, monkeypatch
):
    monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", "true")
    client = MagicMock()
    client.get_pull_request.return_value = {"merged": False, "node_id": "PR_x"}
    client.merge_pull_request.return_value = {
        "merged": True,
        "sha": "abc123def",
        "message": "Pull Request successfully merged",
    }
    mock_client_cls.return_value = client

    result = _maybe_auto_merge_pr(
        repo="acme/app",
        pr_number=12,
        pr_url="https://github.com/acme/app/pull/12",
        github_token="tok",
    )

    assert result.get("ok") is True
    assert result.get("merged") is True
    assert result.get("merge_method") == "squash"
    assert result.get("sha") == "abc123def"
    client.merge_pull_request.assert_called_once_with(
        owner="acme",
        repo="app",
        pr_number=12,
        merge_method="squash",
    )
    client.enable_pull_request_auto_merge.assert_not_called()
    client.mark_pull_request_ready_for_review.assert_not_called()
    posted = mock_discord.call_args[0][0]
    assert "PR auto-merged" in posted
    assert "squash" in posted
    assert "https://github.com/acme/app/pull/12" in posted
    assert "draft" not in posted.lower()


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
@patch("bigas.resources.cto.endpoints.GitHubPRCommentClient")
def test_maybe_auto_merge_enables_native_when_checks_block(
    mock_client_cls, mock_discord, monkeypatch
):
    monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", "true")
    client = MagicMock()
    client.get_pull_request.return_value = {"merged": False, "node_id": "PR_x"}
    client.merge_pull_request.side_effect = GitHubMergeNotReadyError(
        "Required status check pending"
    )
    client.enable_pull_request_auto_merge.return_value = {
        "enabled": True,
        "merge_method": "squash",
        "enabled_at": "2026-08-11T07:00:00Z",
    }
    mock_client_cls.return_value = client

    result = _maybe_auto_merge_pr(
        repo="acme/app",
        pr_number=12,
        pr_url="https://github.com/acme/app/pull/12",
        github_token="tok",
    )

    assert result.get("ok") is True
    assert result.get("merged") is False
    assert result.get("auto_merge_enabled") is True
    client.enable_pull_request_auto_merge.assert_called_once_with(
        owner="acme",
        repo="app",
        pr_number=12,
        merge_method="squash",
    )
    posted = mock_discord.call_args[0][0]
    assert "PR auto-merge enabled" in posted
    assert "checks" in posted.lower()


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
@patch("bigas.resources.cto.endpoints.GitHubPRCommentClient")
def test_maybe_auto_merge_failure_posts_discord(
    mock_client_cls, mock_discord, monkeypatch
):
    monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", "true")
    client = MagicMock()
    client.get_pull_request.return_value = {"merged": False, "node_id": "PR_x"}
    client.merge_pull_request.side_effect = GitHubPRCommentError("Merge conflict")
    mock_client_cls.return_value = client

    result = _maybe_auto_merge_pr(
        repo="acme/app",
        pr_number=12,
        pr_url="https://github.com/acme/app/pull/12",
        github_token="tok",
    )

    assert result.get("ok") is False
    assert result.get("merged") is False
    client.enable_pull_request_auto_merge.assert_not_called()
    posted = mock_discord.call_args[0][0]
    assert "PR auto-merge failed" in posted


@patch("bigas.resources.cto.pr_review.github_client.requests.put")
def test_merge_pull_request_squash_payload(mock_put):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"merged": true, "sha": "deadbeef"}'
    mock_resp.json.return_value = {"merged": True, "sha": "deadbeef"}
    mock_put.return_value = mock_resp

    client = GitHubPRCommentClient(token="tok")
    data = client.merge_pull_request(owner="acme", repo="app", pr_number=3)

    assert data["sha"] == "deadbeef"
    mock_put.assert_called_once()
    _, kwargs = mock_put.call_args
    assert kwargs["json"]["merge_method"] == "squash"


@patch("bigas.resources.cto.pr_review.github_client.requests.put")
def test_merge_pull_request_405_is_not_ready(mock_put):
    mock_resp = MagicMock()
    mock_resp.status_code = 405
    mock_resp.text = '{"message": "Required status check pending"}'
    mock_resp.json.return_value = {"message": "Required status check pending"}
    mock_put.return_value = mock_resp

    client = GitHubPRCommentClient(token="tok")
    with pytest.raises(GitHubMergeNotReadyError, match="status check"):
        client.merge_pull_request(owner="acme", repo="app", pr_number=3)


@patch("bigas.resources.cto.pr_review.github_client.requests.put")
def test_merge_pull_request_conflict_raises(mock_put):
    mock_resp = MagicMock()
    mock_resp.status_code = 409
    mock_resp.text = '{"message": "Merge conflict"}'
    mock_resp.json.return_value = {"message": "Merge conflict"}
    mock_put.return_value = mock_resp

    client = GitHubPRCommentClient(token="tok")
    with pytest.raises(GitHubPRCommentError, match="Merge conflict"):
        client.merge_pull_request(owner="acme", repo="app", pr_number=3)


@patch("bigas.resources.cto.pr_review.github_client.requests.put")
def test_merge_pull_request_403_includes_github_message(mock_put):
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = '{"message": "Resource not accessible by personal access token"}'
    mock_resp.json.return_value = {
        "message": "Resource not accessible by personal access token"
    }
    mock_put.return_value = mock_resp

    client = GitHubPRCommentClient(token="tok")
    with pytest.raises(
        GitHubPRCommentError,
        match="Resource not accessible by personal access token",
    ):
        client.merge_pull_request(owner="acme", repo="app", pr_number=3)


@patch("bigas.resources.cto.pr_review.github_client.requests.post")
@patch.object(GitHubPRCommentClient, "get_pull_request")
def test_enable_pull_request_auto_merge_graphql(mock_get_pr, mock_post):
    mock_get_pr.return_value = {"node_id": "PR_kwDOTest"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"data":{}}'
    mock_resp.json.return_value = {
        "data": {
            "enablePullRequestAutoMerge": {
                "pullRequest": {
                    "id": "PR_kwDOTest",
                    "number": 3,
                    "autoMergeRequest": {
                        "enabledAt": "2026-08-11T07:00:00Z",
                        "mergeMethod": "SQUASH",
                    },
                }
            }
        }
    }
    mock_post.return_value = mock_resp

    client = GitHubPRCommentClient(token="tok")
    data = client.enable_pull_request_auto_merge(
        owner="acme", repo="app", pr_number=3
    )
    assert data["enabled"] is True
    assert data["merge_method"] == "squash"
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["variables"]["mergeMethod"] == "SQUASH"


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
@patch("bigas.resources.cto.endpoints.GitHubPRCommentClient")
def test_maybe_auto_merge_marks_draft_ready_then_merges(
    mock_client_cls, mock_discord, monkeypatch
):
    monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", "true")
    client = MagicMock()
    client.get_pull_request.return_value = {
        "merged": False,
        "draft": True,
        "node_id": "PR_x",
    }
    client.merge_pull_request.return_value = {
        "merged": True,
        "sha": "abc123def",
        "message": "Pull Request successfully merged",
    }
    mock_client_cls.return_value = client

    result = _maybe_auto_merge_pr(
        repo="acme/app",
        pr_number=12,
        pr_url="https://github.com/acme/app/pull/12",
        github_token="tok",
    )

    assert result.get("ok") is True
    assert result.get("merged") is True
    assert result.get("draft_converted") is True
    client.mark_pull_request_ready_for_review.assert_called_once_with(
        owner="acme",
        repo="app",
        pr_number=12,
    )
    client.merge_pull_request.assert_called_once()
    posted = mock_discord.call_args[0][0]
    assert "PR auto-merged" in posted
    assert "Marked draft as ready for review" in posted


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
@patch("bigas.resources.cto.endpoints.GitHubPRCommentClient")
def test_maybe_auto_merge_draft_convert_failure_skips_merge(
    mock_client_cls, mock_discord, monkeypatch
):
    monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", "true")
    client = MagicMock()
    client.get_pull_request.return_value = {
        "merged": False,
        "draft": True,
        "node_id": "PR_x",
    }
    client.mark_pull_request_ready_for_review.side_effect = GitHubPRCommentError(
        "GitHub returned 403 marking PR ready for review."
    )
    mock_client_cls.return_value = client

    result = _maybe_auto_merge_pr(
        repo="acme/app",
        pr_number=12,
        pr_url="https://github.com/acme/app/pull/12",
        github_token="tok",
    )

    assert result.get("ok") is False
    assert result.get("merged") is False
    assert result.get("draft") is True
    assert result.get("draft_converted") is False
    client.merge_pull_request.assert_not_called()
    posted = mock_discord.call_args[0][0]
    assert "PR auto-merge failed" in posted
    assert "draft" in posted.lower()


@patch("bigas.resources.cto.pr_review.github_client.requests.post")
def test_mark_pull_request_ready_for_review_posts(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"draft": false, "html_url": "https://github.com/acme/app/pull/3"}'
    mock_resp.json.return_value = {
        "draft": False,
        "html_url": "https://github.com/acme/app/pull/3",
        "node_id": "PR_kwDOTest",
    }
    mock_post.return_value = mock_resp

    client = GitHubPRCommentClient(token="tok")
    data = client.mark_pull_request_ready_for_review(
        owner="acme", repo="app", pr_number=3
    )

    assert data["ok"] is True
    assert data["already_ready"] is False
    assert data["draft"] is False
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0].endswith("/repos/acme/app/pulls/3/ready_for_review")


@patch("bigas.resources.cto.pr_review.github_client.requests.post")
@patch.object(GitHubPRCommentClient, "get_pull_request")
def test_mark_pull_request_ready_for_review_422_already_ready(
    mock_get_pr, mock_post
):
    mock_resp = MagicMock()
    mock_resp.status_code = 422
    mock_resp.text = '{"message": "Validation Failed"}'
    mock_resp.json.return_value = {"message": "Validation Failed"}
    mock_post.return_value = mock_resp
    mock_get_pr.return_value = {"draft": False, "node_id": "PR_x"}

    client = GitHubPRCommentClient(token="tok")
    data = client.mark_pull_request_ready_for_review(
        owner="acme", repo="app", pr_number=3
    )

    assert data["ok"] is True
    assert data["already_ready"] is True


@patch("bigas.resources.cto.pr_review.github_client.requests.post")
@patch.object(GitHubPRCommentClient, "get_pull_request")
def test_mark_pull_request_ready_for_review_422_still_draft_raises(
    mock_get_pr, mock_post
):
    mock_resp = MagicMock()
    mock_resp.status_code = 422
    mock_resp.text = '{"message": "Pull request is in draft"}'
    mock_resp.json.return_value = {"message": "Pull request is in draft"}
    mock_post.return_value = mock_resp
    mock_get_pr.return_value = {"draft": True, "node_id": "PR_x"}

    client = GitHubPRCommentClient(token="tok")
    with pytest.raises(GitHubPRCommentError, match="ready for review"):
        client.mark_pull_request_ready_for_review(
            owner="acme", repo="app", pr_number=3
        )
