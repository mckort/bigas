"""HTML comment markers embedded in Bigas pull-request comments.

This module stays free of autofix and GitHub-client imports. Heuristics and
the GitHub client both need the markers, and importing each other to share
them crashes the process on boot.
"""

from __future__ import annotations

import re

BIGAS_REVIEW_MARKER = "<!-- bigas-ai-review-marker -->"
BIGAS_AUTOFIX_COOLDOWN_MARKER = "<!-- bigas-autofix-cooldown-marker -->"
# One hidden comment per PR listing Cursor agents started after a review.
BIGAS_AUTOFIX_ROUNDS_MARKER = "<!-- bigas-autofix-rounds-marker -->"

_REVIEWED_SHA_RE = re.compile(
    r"<!--\s*bigas-reviewed-sha:\s*([0-9a-f]{7,40})\s*-->",
    re.IGNORECASE,
)


def reviewed_sha_marker(sha: str) -> str:
    """Review marker plus the head SHA this comment was written for."""
    cleaned = (sha or "").strip().lower()
    if not cleaned:
        return BIGAS_REVIEW_MARKER
    return f"{BIGAS_REVIEW_MARKER}\n<!-- bigas-reviewed-sha: {cleaned} -->"


def reviewed_sha_from_comment(body: str | None) -> str | None:
    if not isinstance(body, str) or not body:
        return None
    match = _REVIEWED_SHA_RE.search(body)
    if not match:
        return None
    return match.group(1).lower()


def strip_bigas_review_markers(body: str | None) -> str:
    """Drop review markers so a SHA comment is not treated as a finding."""
    text = (body or "").replace(BIGAS_REVIEW_MARKER, "")
    return _REVIEWED_SHA_RE.sub("", text)
