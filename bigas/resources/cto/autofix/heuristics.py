"""Decide whether a Bigas PR review warrants launching Cursor autofix."""
from __future__ import annotations

import re
from typing import Tuple

AUTOFIX_COMMIT_MARKER = "[bigas-autofix]"
DEFAULT_AUTOFIX_MAX_ITERATIONS = 5


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
    r"(?i)\b(looks good( to me)?|lgtm|safe to merge|ready to merge|"
    r"no (blocking )?issues|nothing to fix|approved as[- ]is)\b"
)
# Explicit severity headers from the structured review format.
_SECTION_HEADER = re.compile(
    r"(?im)^\s{0,3}#{1,6}\s*(Blockers|Important|Minor)\s*$"
)
# Note: (?<!non-) avoids matching the "blocking" inside "non-blocking".
_ACTIONABLE = re.compile(
    r"(?i)\b(must[- ]fix|(?<!non-)blocking|critical|important|security|vulnerability|bug\b|"
    r"broken|incorrect|regression|failing test|high severity|do not merge)\b"
)
_NIT_ONLY = re.compile(
    r"(?i)\b(non[- ]blocking|nit\b|minor suggestion|optional|style only|###\s*Minor)\b"
)
_SOFT_ONLY = re.compile(
    r"(?i)\b(consider|optional|todo\b|nice to have|future cleanup|non[- ]blocking|"
    r"nit\b|minor suggestion|style only)\b"
)


def _section_bodies(review_body: str) -> dict[str, str]:
    """Parse ### Blockers / ### Important / ### Minor section bodies if present."""
    matches = list(_SECTION_HEADER.finditer(review_body or ""))
    if not matches:
        return {}
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        name = match.group(1).lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(review_body)
        sections[name] = (review_body[start:end] or "").strip()
    return sections


def _section_has_findings(body: str) -> bool:
    text = (body or "").strip()
    if not text:
        return False
    # Common empty markers from the structured prompt.
    if re.fullmatch(r"(?is)none\.?", text):
        return False
    if re.fullmatch(r"(?is)n/?a\.?", text):
        return False
    if re.fullmatch(r"(?is)no (issues|findings|blockers|important issues)\.?", text):
        return False
    return True


def review_needs_autofix(review_body: str) -> Tuple[bool, str]:
    """
    Return (should_run, reason).

    Autofix runs when the review has clear actionable/blocking language.
    Clean LGTM reviews and nit-only reviews are skipped.
    Soft "consider/TODO/minor" language alone does not trigger autofix.
    """
    body = (review_body or "").strip()
    if not body:
        return False, "empty review body"
    if "<!-- bigas-autofix-skip -->" in body:
        return False, "review contains autofix-skip marker"

    sections = _section_bodies(body)
    if sections:
        if _section_has_findings(sections.get("blockers", "")):
            return True, "actionable findings in review"
        if _section_has_findings(sections.get("important", "")):
            return True, "actionable findings in review"
        # Structured review with empty Blockers/Important → never autofix on Minor alone.
        return False, "only non-blocking / nit suggestions"

    has_actionable = bool(_ACTIONABLE.search(body))
    looks_clean = bool(_CLEAN.search(body))
    nit_only = bool(_NIT_ONLY.search(body)) and not has_actionable
    soft_only = bool(_SOFT_ONLY.search(body)) and not has_actionable

    if has_actionable:
        return True, "actionable findings in review"
    if looks_clean:
        return False, "review looks clean (LGTM)"
    if nit_only or soft_only:
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
    sections = _section_bodies(body)
    if sections and not _section_has_findings(sections.get("blockers", "")) and not _section_has_findings(
        sections.get("important", "")
    ):
        return True
    if _NIT_ONLY.search(body) and not _ACTIONABLE.search(body):
        return True
    return False
