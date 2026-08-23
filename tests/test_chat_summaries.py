from bigas.resources.cto.chat_summaries import (
    summarize_autofix_result,
    summarize_followup_result,
    summarize_pr_merged_result,
    summarize_review_result,
    with_summary,
)


def test_review_summary_ready_to_merge():
    text = summarize_review_result(
        {
            "success": True,
            "review_posted": True,
            "ready_to_merge": True,
            "comment_url": "https://github.com/mckort/vcfieldassistant/pull/123#issuecomment-1",
            "auto_merge": {"skipped": True},
        }
    )
    assert "ready to merge" in text.lower()
    assert "issuecomment-1" in text
    assert "autofix" not in text.lower()


def test_review_summary_findings_offers_autofix():
    text = summarize_review_result(
        {
            "success": True,
            "review_posted": True,
            "ready_to_merge": False,
            "comment_url": "https://github.com/mckort/vcfieldassistant/pull/123#issuecomment-1",
        }
    )
    assert "findings" in text.lower()
    assert "run autofix" in text.lower()


def test_pr_merged_summary_moved_ticket():
    text = summarize_pr_merged_result(
        {
            "success": True,
            "pr_url": "https://github.com/mckort/vcfieldassistant/pull/123",
            "jira_final_approval": {
                "ok": True,
                "issue_key": "BIG-15",
                "moved_to": "Final approval (manual)",
            },
        }
    )
    assert "BIG-15" in text
    assert "Final approval" in text


def test_review_summary_already_merged_beats_ready_flag():
    text = summarize_review_result(
        {
            "success": True,
            "skipped": True,
            "reason": "pr_already_merged",
            "ready_to_merge": True,
            "pr_url": "https://github.com/mckort/vcfieldassistant/pull/123",
        }
    )
    assert "already merged" in text.lower()


def test_autofix_summary_launched_includes_agent_link():
    text = summarize_autofix_result(
        {
            "success": True,
            "launched": True,
            "autofix_round": 2,
            "max_iterations": 5,
            "agent_url": "https://cursor.com/agents/bc-123",
            "pr_url": "https://github.com/mckort/vcfieldassistant/pull/123",
        }
    )
    assert "running" in text.lower()
    assert "2/5" in text
    assert "https://cursor.com/agents/bc-123" in text


def test_autofix_summary_clean_review_skips():
    text = summarize_autofix_result(
        {
            "success": True,
            "skipped": True,
            "review_clean": True,
            "reason": "review looks clean",
            "pr_url": "https://github.com/mckort/vcfieldassistant/pull/123",
        }
    )
    assert "no autofix needed" in text.lower()


def test_autofix_summary_error_offers_retry():
    text = summarize_autofix_result({"error": "CURSOR_API_KEY is required"})
    assert text.startswith("Autofix failed:")
    assert "trigger autofix again" in text.lower()


def test_followup_summary_still_running():
    text = summarize_followup_result(
        {
            "success": True,
            "done": False,
            "status": "RUNNING",
            "agent_url": "https://cursor.com/agents/bc-123",
            "finalized": False,
        }
    )
    assert "still running" in text.lower()
    assert "https://cursor.com/agents/bc-123" in text


def test_followup_summary_finished_without_commits_offers_retry():
    text = summarize_followup_result(
        {
            "success": True,
            "done": True,
            "ok": True,
            "finalized": True,
            "fixes_pushed": False,
            "pr_url": "https://github.com/mckort/vcfieldassistant/pull/123",
        }
    )
    assert "without pushing" in text.lower()
    assert "trigger autofix again" in text.lower()


def test_with_summary_attaches_field():
    out = with_summary({"launched": True, "autofix_round": 1, "max_iterations": 5}, summarize_autofix_result)
    assert out["summary"]
    assert "running" in out["summary"].lower()
