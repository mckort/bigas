from bigas.resources.cto.autofix.heuristics import (
    auto_merge_enabled,
    autofix_pushed_new_commit,
    latest_commit_is_autofix,
    review_needs_autofix,
)
from bigas.resources.cto.autofix.service import (
    _build_prompt,
    autofix_looks_like_confirmation_stop,
)


def test_lgtm_skips():
    ok, reason = review_needs_autofix(
        "Looks good to me. Safe to merge as-is.\n\n**Minor suggestion (Non-blocking):** spacing"
    )
    assert ok is False
    assert "clean" in reason or "non-blocking" in reason


def test_blocking_runs():
    ok, reason = review_needs_autofix(
        "## Blocking\nMust fix the broken auth check before merge."
    )
    assert ok is True
    assert "actionable" in reason


def test_important_runs():
    ok, reason = review_needs_autofix(
        "**Important**\n\n"
        "* **`backend/src/foo.ts` (line 10)**: A confirmed metric should likely "
        "supersede any unconfirmed peers in its new slot.\n\n"
        "**Minor / Polish**\n\n"
        "* Consider wrapping both blocks in a fragment.\n\n"
        "The rest of the logic looks solid.\n"
    )
    assert ok is True
    assert "actionable" in reason


def test_structured_important_runs():
    ok, reason = review_needs_autofix(
        "### Blockers\nNone.\n\n"
        "### Important\n"
        "- Validate body.samples before writing to Firestore.\n\n"
        "### Minor\n"
        "- Consider extracting a helper.\n\n"
        "Otherwise solid.\n"
    )
    assert ok is True
    assert "actionable" in reason


def test_structured_minor_only_skips():
    ok, reason = review_needs_autofix(
        "### Blockers\nNone.\n\n"
        "### Important\nNone.\n\n"
        "### Minor\n"
        "- Consider extracting a helper.\n\n"
        "Ready to merge.\n"
    )
    assert ok is False
    assert "nit" in reason or "non-blocking" in reason


def test_soft_consider_only_skips():
    ok, reason = review_needs_autofix(
        "A few optional polish items:\n"
        "- Consider adding an AbortController for polling.\n"
        "- Consider leaving a TODO for the duplicate query.\n"
        "The rest of the implementation looks solid and ready to merge!\n"
    )
    assert ok is False


def test_autofix_commit_marker():
    assert latest_commit_is_autofix("fix: auth [bigas-autofix]")
    assert not latest_commit_is_autofix("fix: auth")


def test_autofix_max_iterations_env(monkeypatch):
    from bigas.resources.cto.autofix.heuristics import autofix_max_iterations

    monkeypatch.delenv("BIGAS_CTO_AUTOFIX_MAX_ITERATIONS", raising=False)
    assert autofix_max_iterations() == 5
    monkeypatch.setenv("BIGAS_CTO_AUTOFIX_MAX_ITERATIONS", "7")
    assert autofix_max_iterations() == 7
    monkeypatch.setenv("BIGAS_CTO_AUTOFIX_MAX_ITERATIONS", "0")
    assert autofix_max_iterations() == 1


def test_format_loop_protection_message_is_clear():
    from bigas.resources.cto.autofix.heuristics import format_loop_protection_message

    msg = format_loop_protection_message(autofix_count=9, max_iterations=5)
    assert "limit of 5" in msg
    assert "found 9" in msg
    assert "manual handling" in msg


def test_autofix_cooldown_seconds_env(monkeypatch):
    from bigas.resources.cto.autofix.heuristics import autofix_cooldown_seconds

    monkeypatch.delenv("BIGAS_CTO_AUTOFIX_COOLDOWN_SECONDS", raising=False)
    assert autofix_cooldown_seconds() == 120
    monkeypatch.setenv("BIGAS_CTO_AUTOFIX_COOLDOWN_SECONDS", "90")
    assert autofix_cooldown_seconds() == 90


def test_age_seconds_since_parses_github_timestamps():
    from datetime import datetime, timedelta, timezone

    from bigas.resources.cto.autofix.service import _age_seconds_since

    past = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat().replace(
        "+00:00", "Z"
    )
    age = _age_seconds_since(past)
    assert age is not None
    assert 40 <= age <= 60
    assert _age_seconds_since(None) is None
    assert _age_seconds_since("not-a-date") is None


def test_autofix_prompt_forbids_confirmation():
    prompt = _build_prompt(
        repo="mckort/bigas",
        pr_number=1,
        pr_url="https://github.com/mckort/bigas/pull/1",
        review_body="## Blocking\nFix auth",
    )
    assert "Do NOT ask for confirmation" in prompt
    assert "apply the fixes and push commits immediately" in prompt
    assert "Also fix Minor items" in prompt
    assert "Fix all Blockers and Important" in prompt
    assert "already resolved" in prompt or "local wrapper" in prompt
    assert "remove that dead code" in prompt
    assert "Do not expand into a repo-wide cleanup" in prompt


def test_pr_review_prompts_respect_project_helpers():
    from bigas.resources.cto.pr_review.prompts import (
        PR_REVIEW_INITIAL_SYSTEM_PROMPT,
        PR_REVIEW_POST_AUTOFIX_SYSTEM_PROMPT,
        PR_REVIEW_SYSTEM_PROMPT,
    )

    for text in (PR_REVIEW_INITIAL_SYSTEM_PROMPT, PR_REVIEW_POST_AUTOFIX_SYSTEM_PROMPT):
        assert "deleteField()" in text
        assert "Project helpers" in text

    assert "mobile/responsive" in PR_REVIEW_INITIAL_SYSTEM_PROMPT
    assert "small mobile screens" in PR_REVIEW_SYSTEM_PROMPT


def test_pr_review_prompts_classify_dead_code_as_important():
    from bigas.resources.cto.pr_review.prompts import (
        PR_REVIEW_INITIAL_SYSTEM_PROMPT,
        PR_REVIEW_POST_AUTOFIX_SYSTEM_PROMPT,
        PR_REVIEW_SYSTEM_PROMPT,
    )

    for text in (
        PR_REVIEW_INITIAL_SYSTEM_PROMPT,
        PR_REVIEW_POST_AUTOFIX_SYSTEM_PROMPT,
        PR_REVIEW_SYSTEM_PROMPT,
    ):
        assert "Dead / unused code (classify as Important, not Minor)" in text
        assert "this PR introduced or made unused" in text
        assert "Do NOT hunt the rest of the repository" in text

    assert "Classify as Important" in PR_REVIEW_INITIAL_SYSTEM_PROMPT
    assert "leftover dead/unused code" in PR_REVIEW_POST_AUTOFIX_SYSTEM_PROMPT
    assert "unused code this PR introduced or made unused" in PR_REVIEW_SYSTEM_PROMPT


def test_pr_review_prompts_forbid_ready_to_merge_with_findings():
    from bigas.resources.cto.pr_review.prompts import (
        PR_REVIEW_INITIAL_SYSTEM_PROMPT,
        PR_REVIEW_POST_AUTOFIX_SYSTEM_PROMPT,
    )

    for text in (PR_REVIEW_INITIAL_SYSTEM_PROMPT, PR_REVIEW_POST_AUTOFIX_SYSTEM_PROMPT):
        assert 'do NOT write "ready to merge"' in text
        assert "delete if unused elsewhere" in text

def test_autofix_looks_like_confirmation_stop():
    assert autofix_looks_like_confirmation_stop(
        "Proposed changes...\n\nShall I proceed with implementing these?"
    )
    assert autofix_looks_like_confirmation_stop("Please confirm before I proceed.")
    assert not autofix_looks_like_confirmation_stop("Pushed [bigas-autofix] commits.")
    assert not autofix_looks_like_confirmation_stop("")


def test_autofix_pushed_new_commit_requires_sha_change():
    msg = "fix stuff [bigas-autofix]"
    # Same SHA as launch → agent did not push.
    assert (
        autofix_pushed_new_commit(
            head_sha="abc123",
            head_message=msg,
            baseline_head_sha="abc123",
        )
        is False
    )
    # New autofix commit after launch.
    assert (
        autofix_pushed_new_commit(
            head_sha="def456",
            head_message=msg,
            baseline_head_sha="abc123",
        )
        is True
    )
    # Non-autofix head never counts.
    assert (
        autofix_pushed_new_commit(
            head_sha="def456",
            head_message="regular commit",
            baseline_head_sha="abc123",
        )
        is False
    )
    # Legacy callers without baseline: autofix head still counts.
    assert (
        autofix_pushed_new_commit(
            head_sha="abc123",
            head_message=msg,
            baseline_head_sha=None,
        )
        is True
    )


def test_auto_merge_enabled_defaults_false(monkeypatch):
    monkeypatch.delenv("BIGAS_CTO_AUTO_MERGE", raising=False)
    assert auto_merge_enabled() is False


def test_auto_merge_enabled_true_values(monkeypatch):
    for value in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", value)
        assert auto_merge_enabled() is True, value


def test_auto_merge_enabled_false_values(monkeypatch):
    for value in ("false", "0", "no", "off", ""):
        monkeypatch.setenv("BIGAS_CTO_AUTO_MERGE", value)
        assert auto_merge_enabled() is False, value


def test_autofix_skips_already_merged_pr(monkeypatch):
    from bigas.resources.cto.autofix.service import AutofixService

    class FakeGH:
        def get_pull_request(self, *args, **kwargs):
            return {"merged": True}

        def get_pr_head_commit_meta(self, *args, **kwargs):
            raise AssertionError("should skip before fetching head commit")

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.GitHubPRCommentClient",
        lambda token: FakeGH(),
    )
    result = AutofixService(cursor_api_key="c", github_token="t").run(
        repo="owner/repo", pr_number=9
    )
    assert result["skipped"] is True
    assert result["reason"] == "pr_already_merged"
