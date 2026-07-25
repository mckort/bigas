from bigas.resources.cto.autofix.heuristics import (
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


def test_autofix_looks_like_confirmation_stop():
    assert autofix_looks_like_confirmation_stop(
        "Proposed changes...\n\nShall I proceed with implementing these?"
    )
    assert autofix_looks_like_confirmation_stop("Please confirm before I proceed.")
    assert not autofix_looks_like_confirmation_stop("Pushed [bigas-autofix] commits.")
    assert not autofix_looks_like_confirmation_stop("")
