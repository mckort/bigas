from bigas.resources.cto.autofix.heuristics import (
    auto_merge_enabled,
    autofix_pushed_new_commit,
    leftover_nits_are_acceptable,
    latest_commit_is_autofix,
    review_is_nits_only,
    review_is_ready_to_merge,
    review_needs_autofix,
)
from bigas.resources.cto.autofix.service import (
    _build_prompt,
    _issue_key_from_pr,
    autofix_looks_like_confirmation_stop,
)


def test_lgtm_with_leftover_nits_runs_autofix():
    ok, reason = review_needs_autofix(
        "Looks good to me. Safe to merge as-is.\n\n**Minor suggestion (Non-blocking):** spacing"
    )
    assert ok is True
    assert "minor" in reason or "nit" in reason


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


def test_structured_minor_only_runs_autofix_until_budget():
    body = (
        "### Blockers\nNone.\n\n"
        "### Important\nNone.\n\n"
        "### Minor\n"
        "- Consider extracting a helper.\n\n"
        "Ready to merge.\n"
    )
    ok, reason = review_needs_autofix(body)
    assert ok is True
    assert "minor" in reason
    ok_after, reason_after = review_needs_autofix(body, minor_autofix_count=2)
    assert ok_after is False
    assert "nit" in reason_after or "non-blocking" in reason_after
    ok_at_cap, _ = review_needs_autofix(body, autofix_count=5)
    assert ok_at_cap is False


_CLEAN_STRUCTURED = (
    "### Blockers\nNone.\n\n### Important\nNone.\n\n### Minor\nNone.\n\nReady to merge.\n"
)
_MINOR_LEFTOVER = (
    "### Blockers\nNone.\n\n### Important\nNone.\n\n"
    "### Minor\n- Deduplicate query tokens.\n\n"
    "This pull request is clean, well-tested, and ready to merge.\n"
)


def test_ready_to_merge_requires_empty_minor_until_nits_budget():
    assert review_is_ready_to_merge(_CLEAN_STRUCTURED) is True
    assert review_is_ready_to_merge(_MINOR_LEFTOVER) is False
    assert review_is_nits_only(_MINOR_LEFTOVER) is True
    assert review_is_ready_to_merge(_MINOR_LEFTOVER, minor_autofix_count=2) is True
    assert review_is_ready_to_merge(_MINOR_LEFTOVER, autofix_count=5) is True
    assert review_is_ready_to_merge(_MINOR_LEFTOVER, minor_autofix_count=1) is False


def test_ready_to_merge_ignores_ready_line_when_minor_has_findings():
    assert "ready to merge" in _MINOR_LEFTOVER.lower()
    assert review_is_ready_to_merge(_MINOR_LEFTOVER) is False


_CLEAN_WITH_IMPORTANT_CLOSER = (
    "### Blockers\nNone.\n\n### Important\nNone.\n\n### Minor\nNone.\n\n"
    "All previous findings are resolved, no new blocker or important issues "
    "were found, and the PR is ready to merge.\n"
)


def test_none_plus_important_word_closer_is_still_clean():
    """LGTM closer with 'important issues' must not count as leftover Minor."""
    ok, reason = review_needs_autofix(_CLEAN_WITH_IMPORTANT_CLOSER)
    assert ok is False
    assert "clean" in reason
    assert review_is_nits_only(_CLEAN_WITH_IMPORTANT_CLOSER) is False
    assert review_is_ready_to_merge(_CLEAN_WITH_IMPORTANT_CLOSER) is True


def test_none_plus_important_word_closer_ignores_review_marker():
    body = (
        _CLEAN_WITH_IMPORTANT_CLOSER.rstrip()
        + "\n\n<!-- bigas-ai-review-marker -->\n"
    )
    ok, reason = review_needs_autofix(body)
    assert ok is False
    assert "clean" in reason
    assert review_is_ready_to_merge(body) is True


def test_minor_bullet_plus_important_word_closer_still_counts():
    body = (
        "### Blockers\nNone.\n\n### Important\nNone.\n\n"
        "### Minor\n- Deduplicate query tokens.\n\n"
        "All previous findings are resolved, no new blocker or important issues "
        "were found, and the PR is ready to merge.\n"
    )
    ok, reason = review_needs_autofix(body)
    assert ok is True
    assert "minor" in reason
    assert review_is_nits_only(body) is True
    assert review_is_ready_to_merge(body) is False


def test_leftover_nits_are_acceptable_at_caps():
    assert leftover_nits_are_acceptable() is False
    assert leftover_nits_are_acceptable(minor_autofix_count=2) is True
    assert leftover_nits_are_acceptable(autofix_count=5) is True
    assert leftover_nits_are_acceptable(minor_autofix_count=1, autofix_count=4) is False


def test_soft_consider_only_runs_autofix():
    ok, reason = review_needs_autofix(
        "A few optional polish items:\n"
        "- Consider adding an AbortController for polling.\n"
        "- Consider leaving a TODO for the duplicate query.\n"
        "The rest of the implementation looks solid and ready to merge!\n"
    )
    assert ok is True
    assert "minor" in reason


def test_autofix_commit_marker():
    from bigas.resources.cto.autofix.heuristics import count_autofix_rounds

    assert latest_commit_is_autofix("fix: auth [bigas-autofix]")
    assert latest_commit_is_autofix("BIG-89: [bigas-autofix] [nits-only] polish")
    assert not latest_commit_is_autofix("fix: auth")
    assert count_autofix_rounds(
        [
            "feat: start",
            "BIG-1: [bigas-autofix] fix auth",
            "BIG-1: [bigas-autofix] [nits-only] polish",
        ]
    ) == (2, 1)


def test_autofix_max_iterations_env(monkeypatch):
    from bigas.resources.cto.autofix.heuristics import autofix_max_iterations

    monkeypatch.delenv("BIGAS_CTO_AUTOFIX_MAX_ITERATIONS", raising=False)
    assert autofix_max_iterations() == 5
    monkeypatch.setenv("BIGAS_CTO_AUTOFIX_MAX_ITERATIONS", "7")
    assert autofix_max_iterations() == 7
    monkeypatch.setenv("BIGAS_CTO_AUTOFIX_MAX_ITERATIONS", "0")
    assert autofix_max_iterations() == 1


def test_minor_autofix_max_iterations_env(monkeypatch):
    from bigas.resources.cto.autofix.heuristics import minor_autofix_max_iterations

    monkeypatch.delenv("BIGAS_CTO_AUTOFIX_MINOR_ITERATIONS", raising=False)
    assert minor_autofix_max_iterations() == 2
    monkeypatch.setenv("BIGAS_CTO_AUTOFIX_MINOR_ITERATIONS", "3")
    assert minor_autofix_max_iterations() == 3
    monkeypatch.setenv("BIGAS_CTO_AUTOFIX_MINOR_ITERATIONS", "0")
    assert minor_autofix_max_iterations() == 1


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
    assert "[bigas-autofix]" in prompt
    assert "[nits-only]" not in prompt


def test_autofix_prompt_nits_only_uses_minor_marker():
    prompt = _build_prompt(
        repo="mckort/bigas",
        pr_number=1,
        pr_url="https://github.com/mckort/bigas/pull/1",
        review_body=_MINOR_LEFTOVER,
        issue_key="BIG-89",
        nits_only=True,
    )
    assert "BIG-89: [bigas-autofix] [nits-only]" in prompt
    assert "only Minor / non-blocking" in prompt


def test_autofix_prompt_requires_ticket_key_in_commit():
    prompt = _build_prompt(
        repo="mckort/vcfieldassistant",
        pr_number=191,
        pr_url="https://github.com/mckort/vcfieldassistant/pull/191",
        review_body="## Blocking\nFix contrast",
        issue_key="VFA-53",
    )
    assert "VFA-53: [bigas-autofix]" in prompt
    assert prompt.index("VFA-53: [bigas-autofix]") < prompt.index("Do not merge")


def test_issue_key_from_pr_title():
    assert (
        _issue_key_from_pr(
            {
                "title": "VFA-53: Fix meeting notes email headline contrast",
                "body": "",
                "head": {"ref": "fix/ios-headline"},
            }
        )
        == "VFA-53"
    )
    assert _issue_key_from_pr({"title": "Add cherry-pick workflow"}) == ""


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


class _NitsFakeGH:
    def __init__(self, messages, body=_MINOR_LEFTOVER):
        self.messages = messages
        self.body = body

    def get_pull_request(self, *args, **kwargs):
        return {"merged": False, "title": "BIG-89: Lock staging", "body": "", "head": {"ref": "feat/x"}}

    def get_pr_head_commit_meta(self, *args, **kwargs):
        return "abc123", "feat: start", "2026-09-17T17:00:00Z"

    def list_pr_commit_messages(self, *args, **kwargs):
        return list(self.messages)

    def get_marked_comment(self, **kwargs):
        return {"body": self.body, "updated_at": "2026-09-17T17:10:00Z"}


def test_autofix_launches_for_leftover_nits(monkeypatch):
    from bigas.resources.cto.autofix.service import AutofixService

    launched = {}

    class FakeCursor:
        def __init__(self, api_key):
            pass

        def launch_pr_autofix(self, **kwargs):
            launched.update(kwargs)
            return {"agent_id": "bc-1", "agent_url": "https://cursor.com/agents/bc-1", "run_id": "run-1"}

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.GitHubPRCommentClient",
        lambda token: _NitsFakeGH([]),
    )
    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.CursorCloudAgentClient",
        FakeCursor,
    )
    result = AutofixService(cursor_api_key="c", github_token="t").run(
        repo="owner/repo", pr_number=9
    )
    assert result.get("launched") is True
    assert launched["prompt_text"].count("[bigas-autofix] [nits-only]") >= 1


def test_autofix_accepts_leftover_nits_after_two_minor_rounds(monkeypatch):
    from bigas.resources.cto.autofix.service import AutofixService

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.GitHubPRCommentClient",
        lambda token: _NitsFakeGH(
            [
                "BIG-89: [bigas-autofix] [nits-only] tweak button",
                "BIG-89: [bigas-autofix] [nits-only] tweak again",
            ]
        ),
    )
    result = AutofixService(cursor_api_key="c", github_token="t").run(
        repo="owner/repo", pr_number=9
    )
    assert result["skipped"] is True
    assert result.get("nits_accepted") is True
    assert result.get("review_clean") is True
    assert result["minor_autofix_count"] == 2


def test_autofix_accepts_leftover_nits_at_max_iterations(monkeypatch):
    from bigas.resources.cto.autofix.service import AutofixService

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.GitHubPRCommentClient",
        lambda token: _NitsFakeGH(
            [f"BIG-89: [bigas-autofix] fix {i}" for i in range(5)]
        ),
    )
    result = AutofixService(cursor_api_key="c", github_token="t").run(
        repo="owner/repo", pr_number=9
    )
    assert result["skipped"] is True
    assert result.get("nits_accepted") is True
    assert result.get("loop_protection") is not True
