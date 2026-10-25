from bigas.resources.cto.autofix.heuristics import review_is_ready_to_merge
from bigas.resources.cto.pr_review.markers import (
    BIGAS_REVIEW_MARKER,
    reviewed_sha_from_comment,
    reviewed_sha_marker,
)


_CLEAN = (
    "### Blockers\nNone.\n\n### Important\nNone.\n\n### Minor\nNone.\n\nReady to merge.\n"
)


def test_reviewed_sha_round_trip():
    sha = "abc123def456"
    marker = reviewed_sha_marker(sha)
    assert BIGAS_REVIEW_MARKER in marker
    assert reviewed_sha_from_comment(f"{_CLEAN}\n\n{marker}") == sha
    assert reviewed_sha_from_comment(_CLEAN) is None
    assert reviewed_sha_marker("") == BIGAS_REVIEW_MARKER


def test_sha_marker_does_not_count_as_a_finding():
    body = f"{_CLEAN}\n\n{reviewed_sha_marker('abc123def456')}"
    assert review_is_ready_to_merge(body) is True
