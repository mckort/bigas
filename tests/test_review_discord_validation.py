"""Missing/invalid review inputs must not spam Discord."""

from __future__ import annotations

from unittest.mock import patch

from flask import Flask

from bigas.resources.cto.endpoints import _discord_review_posted_message, cto_bp


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
    assert (res.get_json() or {}).get("summary")
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
    assert "still running" in (body.get("summary") or "").lower()
    mock_discord.assert_not_called()


def test_post_cto_status_skips_chat_thread(monkeypatch):
    from bigas.resources.cto import endpoints as ep

    calls = []

    def fake_post(message, *, mirror_thread=True):
        calls.append({"message": message, "mirror_thread": mirror_thread})

    monkeypatch.setattr(ep, "_post_to_discord_cto", fake_post)
    ep._post_cto_status("**CTO autofix launched** (1/5)\nPR: https://github.com/acme/app/pull/1")
    assert calls == [
        {
            "message": "**CTO autofix launched** (1/5)\nPR: https://github.com/acme/app/pull/1",
            "mirror_thread": False,
        }
    ]


@patch("bigas.resources.cto.endpoints._fetch_pull_request", return_value={})
@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
def test_autofix_loop_protection_skips_chat_thread(mock_discord, _mock_pr):
    from bigas.resources.cto.endpoints import _notify_autofix_loop_protection

    _notify_autofix_loop_protection(
        repo="acme/app",
        pr_number=1,
        pr_url="https://github.com/acme/app/pull/1",
        autofix_count=5,
        max_iterations=5,
        github_token="tok",
    )
    assert mock_discord.call_args.kwargs.get("mirror_thread") is False
    assert "CTO autofix stopped" in mock_discord.call_args.args[0]


@patch("bigas.resources.cto.endpoints._fetch_pull_request", return_value={"title": "Fix"})
@patch("bigas.resources.cto.endpoints.AutofixService")
@patch("bigas.resources.cto.endpoints._post_to_discord_cto")
def test_autofix_launched_skips_chat_thread(mock_discord, mock_service, _mock_pr):
    mock_service.return_value.run.return_value = {
        "launched": True,
        "agent_url": "https://cursor.com/agents/bc-1",
        "agent_id": "bc-1",
        "autofix_round": 1,
        "max_iterations": 5,
    }
    client = _app().test_client()
    res = client.post(
        "/mcp/tools/autofix_pr",
        json={"repo": "acme/app", "pr_number": 1, "cursor_api_key": "ck"},
    )
    assert res.status_code == 200
    assert mock_discord.called
    assert mock_discord.call_args.kwargs.get("mirror_thread") is False
    assert "CTO autofix launched" in mock_discord.call_args.args[0]


def test_post_to_discord_cto_chunks_skips_chat_thread(monkeypatch):
    from bigas.resources.cto import endpoints as ep

    calls = []

    def fake_long(webhook_url, text, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(ep, "_cto_discord_webhook", lambda: "https://discord.example/cto")
    monkeypatch.setattr(ep, "post_long_to_discord", fake_long)
    ep._post_to_discord_cto_chunks("**CTO PR review done**\n### Blockers:\nNone.")
    assert calls
    assert calls[0].get("mirror_thread") is False
    assert calls[0].get("chat_agent_id") == "cto"


def test_discord_review_posted_message_puts_pr_on_first_chunk():
    msg = _discord_review_posted_message(
        done_label="**CTO PR review done**",
        pr_url="https://github.com/Green-Promo-Wear-Global/greenpromowear-website/pull/14",
        comment_url="https://github.com/Green-Promo-Wear-Global/greenpromowear-website/pull/14#issuecomment-1",
        review_body="### Blockers:\nNone.",
        cost_suffix="\nEstimated LLM cost: ~$0.1800 (gemini-pro-latest, 1 attempt)",
    )
    header, _, body = msg.partition("\n\n---\n\n")
    assert header.startswith("**CTO PR review done**")
    assert "PR: https://github.com/Green-Promo-Wear-Global/greenpromowear-website/pull/14" in header
    assert "Comment: https://github.com/Green-Promo-Wear-Global/greenpromowear-website/pull/14#issuecomment-1" in header
    assert "Estimated LLM cost:" in header
    assert body == "### Blockers:\nNone."


def test_discord_review_posted_message_without_comment_url():
    msg = _discord_review_posted_message(
        done_label="**CTO PR re-review after autofix done**",
        pr_url="https://github.com/acme/repo/pull/2",
        comment_url="",
        review_body="### Important:\nMissing CSS",
    )
    assert msg.startswith("**CTO PR re-review after autofix done**\nPR: https://github.com/acme/repo/pull/2\n")
    assert "Comment: (no URL returned from GitHub.)" in msg
    assert "### Important:\nMissing CSS" in msg


def test_discord_review_posted_message_includes_pr_title_as_link():
    msg = _discord_review_posted_message(
        done_label="**CTO PR review done**",
        pr_url="https://github.com/mckort/bigas/pull/11",
        comment_url="https://github.com/mckort/bigas/pull/11#issuecomment-1",
        review_body="Looks good.",
        pr_title="BIG-11: Implement create_jira_issue tool",
    )
    header, _, body = msg.partition("\n\n---\n\n")
    assert (
        "[BIG-11: Implement create_jira_issue tool]"
        "(https://github.com/mckort/bigas/pull/11)"
    ) in header
    assert "PR: https://github.com/mckort/bigas/pull/11" not in header
    assert body == "Looks good."
