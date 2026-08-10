"""Missing/invalid review inputs must not spam Discord."""

from __future__ import annotations

from unittest.mock import patch

from flask import Flask

from bigas.resources.cto.endpoints import cto_bp


def _app():
    app = Flask(__name__)
    app.register_blueprint(cto_bp)
    app.config["TESTING"] = True
    return app


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
def test_missing_repo_returns_400_without_discord(mock_discord):
    client = _app().test_client()
    res = client.post("/mcp/tools/review_and_comment_pr", json={})
    assert res.status_code == 400
    assert "repo is required" in (res.get_json() or {}).get("error", "")
    mock_discord.assert_not_called()


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
def test_invalid_repo_returns_400_without_discord(mock_discord):
    client = _app().test_client()
    res = client.post(
        "/mcp/tools/review_and_comment_pr",
        json={"repo": "not-a-pair", "pr_number": 1},
    )
    assert res.status_code == 400
    assert "owner/repo" in (res.get_json() or {}).get("error", "")
    mock_discord.assert_not_called()


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
def test_missing_pr_number_returns_400_without_discord(mock_discord):
    client = _app().test_client()
    res = client.post(
        "/mcp/tools/review_and_comment_pr",
        json={"repo": "mckort/vcfieldassistant"},
    )
    assert res.status_code == 400
    assert "pr_number" in (res.get_json() or {}).get("error", "")
    mock_discord.assert_not_called()
