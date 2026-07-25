"""Decide whether a Bigas PR review warrants launching Cursor autofix."""
from __future__ import annotations

import re
from typing import Tuple

AUTOFIX_COMMIT_MARKER = "[bigas-autofix]"
DEFAULT_AUTOFIX_MAX_ITERATIONS = 3


def autofix_max_iterations() -> int:
    """Max automatic autofix rounds per PR (env BIGAS_CTO_AUTOFIX_MAX_ITERATIONS)."""
    import os

    raw = (os.environ.get("BIGAS_CTO_AUTOFIX_MAX_ITERATIONS") or "").strip()
    if not raw:
        return DEFAULT_AUTOFIX_MAX_ITERATIONS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_AUTOFIX_MAX_ITERATIONS


_CLEAN = re.compile(
    r"(?i)\b(looks good( to me)?|lgtm|safe to merge|no (blocking )?issues|"
    r"nothing to fix|approved as[- ]is)\b"
)
# Note: (?<!non-) avoids matching the "blocking" inside "non-blocking".
_ACTIONABLE = re.compile(
    r"(?i)\b(must[- ]fix|(?<!non-)blocking|critical|important|security|vulnerability|bug\b|"
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


def review_is_ready_to_merge(review_body: str) -> bool:
    """True when the review reads as clean enough to merge (no actionable findings)."""
    should_fix, _reason = review_needs_autofix(review_body)
    if should_fix:
        return False
    body = (review_body or "").strip()
    if not body:
        return False
    # Explicit clean signal, or only nits / ambiguous-but-not-actionable after a review ran.
    if _CLEAN.search(body):
        return True
    if _NIT_ONLY.search(body) and not _ACTIONABLE.search(body):
        return True
    return False
