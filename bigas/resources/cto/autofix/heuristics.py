"""Decide whether a Bigas PR review warrants launching Cursor autofix."""
from __future__ import annotations

import re
from typing import Tuple

AUTOFIX_COMMIT_MARKER = "[bigas-autofix]"

_CLEAN = re.compile(
    r"(?i)\b(looks good( to me)?|lgtm|safe to merge|no (blocking )?issues|"
    r"nothing to fix|approved as[- ]is)\b"
)
# Note: (?<!non-) avoids matching the "blocking" inside "non-blocking".
_ACTIONABLE = re.compile(
    r"(?i)\b(must[- ]fix|(?<!non-)blocking|critical|security|vulnerability|bug\b|"
    r"broken|incorrect|regression|failing test|high severity|do not merge)\b"
)
_NIT_ONLY = re.compile(
    r"(?i)\b(non[- ]blocking|nit\b|minor suggestion|optional|style only)\b"
)


def review_needs_autofix(review_body: str) -> Tuple[bool, str]:
    """
    Return (should_run, reason).

    Autofix runs when the review has clear actionable/blocking language.
    Clean LGTM reviews and nit-only reviews are skipped.
    """
    body = (review_body or "").strip()
    if not body:
        return False, "empty review body"
    if "<!-- bigas-autofix-skip -->" in body:
        return False, "review contains autofix-skip marker"

    has_actionable = bool(_ACTIONABLE.search(body))
    looks_clean = bool(_CLEAN.search(body))
    nit_only = bool(_NIT_ONLY.search(body)) and not has_actionable

    if has_actionable:
        return True, "actionable findings in review"
    if looks_clean:
        return False, "review looks clean (LGTM)"
    if nit_only:
        return False, "only non-blocking / nit suggestions"

    # Ambiguous middle ground: skip by default to avoid noisy agent runs.
    return False, "no clear actionable findings"


def latest_commit_is_autofix(message: str) -> bool:
    return AUTOFIX_COMMIT_MARKER in (message or "")
