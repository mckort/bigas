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


@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
def test_pr_url_satisfies_repo_and_number(mock_discord):
    client = _app().test_client()
    res = client.post(
        "/mcp/tools/review_and_comment_pr",
        json={"pr_url": "https://github.com/mckort/vcfieldassistant/pull/121"},
    )
    assert res.status_code == 400
    err = (res.get_json() or {}).get("error", "")
    assert "repo is required" not in err
    assert "pr_number" not in err
    assert "GitHub token" in err
    mock_discord.assert_not_called()


@patch("bigas.resources.cto.endpoints.AutofixService")
@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
def test_cursor_agent_url_satisfies_agent_id(mock_discord, mock_service):
    mock_service.return_value.poll_status.return_value = {
        "done": False,
        "ok": True,
        "status": "RUNNING",
        "agent_id": "bc-c71a88db-7821-4e44-9717-9f845ad7406b",
        "run_id": None,
        "agent_url": "https://cursor.com/agents/bc-c71a88db-7821-4e44-9717-9f845ad7406b",
    }
    client = _app().test_client()
    res = client.post(
        "/mcp/tools/autofix_followup",
        json={
            "pr_url": "https://github.com/mckort/vcfieldassistant/pull/121",
            "agent_id": "https://cursor.com/agents/bc-c71a88db-7821-4e44-9717-9f845ad7406b",
        },
    )
    assert res.status_code == 200
    body = res.get_json() or {}
    assert body.get("agent_id") == "bc-c71a88db-7821-4e44-9717-9f845ad7406b"
    mock_discord.assert_not_called()
