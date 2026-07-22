from bigas.resources.cto.autofix.heuristics import (
    latest_commit_is_autofix,
    review_needs_autofix,
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


def test_autofix_commit_marker():
    assert latest_commit_is_autofix("fix: auth [bigas-autofix]")
    assert not latest_commit_is_autofix("fix: auth")
