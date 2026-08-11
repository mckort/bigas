"""Tests for BIGAS_CTO_AUTO_MERGE squash-merge helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bigas.resources.cto.endpoints import _maybe_auto_merge_pr
from bigas.resources.cto.pr_review.github_client import (
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
def test_maybe_auto_merge_success_posts_discord(
    mock_client_cls, mock_discord, monkeypatch
):
    monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", "true")
    client = MagicMock()
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
    posted = mock_discord.call_args[0][0]
    assert "PR auto-merged" in posted
    assert "squash" in posted
    assert "https://github.com/acme/app/pull/12" in posted


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
@patch("bigas.resources.cto.endpoints.GitHubPRCommentClient")
def test_maybe_auto_merge_failure_posts_discord(
    mock_client_cls, mock_discord, monkeypatch
):
    monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", "true")
    client = MagicMock()
    client.merge_pull_request.side_effect = GitHubPRCommentError(
        "PR acme/app#12 is not mergeable"
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
def test_merge_pull_request_conflict_raises(mock_put):
    mock_resp = MagicMock()
    mock_resp.status_code = 409
    mock_resp.text = '{"message": "Merge conflict"}'
    mock_resp.json.return_value = {"message": "Merge conflict"}
    mock_put.return_value = mock_resp

    client = GitHubPRCommentClient(token="tok")
    with pytest.raises(GitHubPRCommentError, match="Merge conflict"):
        client.merge_pull_request(owner="acme", repo="app", pr_number=3)
