"""Decide whether a Bigas PR review warrants launching Cursor autofix."""
from __future__ import annotations

import re
from typing import Tuple

AUTOFIX_COMMIT_MARKER = "[bigas-autofix]"
AUTOFIX_NITS_ONLY_MARKER = "[nits-only]"
DEFAULT_AUTOFIX_MAX_ITERATIONS = 5
DEFAULT_AUTOFIX_NITS_ONLY_MAX_ROUNDS = 2
# Short window is enough to avoid overlapping launches; Actions also skips cooldown
# when a newer Bigas review already exists after the autofix head commit.
DEFAULT_AUTOFIX_COOLDOWN_SECONDS = 120


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


def autofix_nits_only_max_rounds() -> int:
    """Max dedicated nits-only autofix rounds when only ### Minor has findings."""
    import os

    raw = (os.environ.get("BIGAS_CTO_AUTOFIX_NITS_ONLY_MAX_ROUNDS") or "").strip()
    if not raw:
        return DEFAULT_AUTOFIX_NITS_ONLY_MAX_ROUNDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_AUTOFIX_NITS_ONLY_MAX_ROUNDS


def autofix_cooldown_seconds() -> int:
    """
    Skip launching a new autofix if the latest head commit is already an autofix
    and younger than this many seconds (env BIGAS_CTO_AUTOFIX_COOLDOWN_SECONDS).
    """
    import os

    raw = (os.environ.get("BIGAS_CTO_AUTOFIX_COOLDOWN_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_AUTOFIX_COOLDOWN_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_AUTOFIX_COOLDOWN_SECONDS


def auto_merge_enabled() -> bool:
    """
    When true, squash-merge the PR after a clean review (no Blockers/Important).

    Env: BIGAS_CTO_AUTO_MERGE=true|false (default false — Ready to merge only).
    """
    import os

    raw = (os.environ.get("BIGAS_CTO_AUTO_MERGE") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def format_loop_protection_message(*, autofix_count: int, max_iterations: int) -> str:
    """Human-readable loop-protection copy for Discord / Jira / API reason."""
    return (
        f"Exceeded autofix limit of {max_iterations} "
        f"(found {autofix_count} `[bigas-autofix]` commits on this PR). "
        f"Remaining review comments need manual handling."
    )

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
    text = _strip_section_closer(body)
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


def review_has_minor_only_findings(review_body: str) -> bool:
    """Structured review: Blockers/Important empty, ### Minor has items."""
    sections = _section_bodies(review_body or "")
    if not sections:
        return False
    if _section_has_findings(sections.get("blockers", "")):
        return False
    if _section_has_findings(sections.get("important", "")):
        return False
    return _section_has_findings(sections.get("minor", ""))


def review_has_blocking_findings(review_body: str) -> bool:
    """True when Blockers or Important sections (or unstructured actionable text) need fixes."""
    body = (review_body or "").strip()
    sections = _section_bodies(body)
    if sections:
        return (
            _section_has_findings(sections.get("blockers", ""))
            or _section_has_findings(sections.get("important", ""))
        )
    has_actionable = bool(_ACTIONABLE.search(body))
    if not has_actionable:
        return False
    return True


def _strip_section_closer(body: str) -> str:
    """Drop a trailing LGTM / ready-to-merge closer so it is not a finding."""
    lines = (body or "").splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and _CLEAN.search(lines[-1]) and not _ACTIONABLE.search(lines[-1]):
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines).strip()


def review_needs_autofix(review_body: str) -> Tuple[bool, str]:
    """
    Return (should_run, reason).

    Autofix runs when the review has clear actionable/blocking language,
    or structured ### Minor-only findings (nits-only autofix rounds).
    Clean LGTM reviews and unstructured nit-only reviews are skipped.
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
        if _section_has_findings(sections.get("minor", "")):
            return True, "minor-only findings (nits-only autofix)"
        return False, "review looks clean (LGTM)"

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


def autofix_pushed_new_commit(
    *,
    head_sha: str,
    head_message: str,
    baseline_head_sha: str | None,
) -> bool:
    """
    True only when PR head is a new `[bigas-autofix]` commit since launch.

    Important: HEAD already being an autofix commit is not enough — that is the
    common case when a later agent finishes without pushing, and must not be
    treated as a successful fix round (which would re-review the same SHA).
    """
    if not latest_commit_is_autofix(head_message):
        return False
    baseline = (baseline_head_sha or "").strip()
    current = (head_sha or "").strip()
    if baseline and current and baseline == current:
        return False
    # No baseline (legacy callers): keep prior behavior — autofix head counts.
    return bool(current)


def review_is_ready_to_merge(
    review_body: str,
    *,
    autofix_count: int | None = None,
    nits_only_autofix_count: int | None = None,
) -> bool:
    """
    True when the review has no leftover findings, or leftover Minor-only findings
    are acceptable for automation (exhausted nits-only rounds or at the autofix cap).

    Pass autofix_count (and optional nits_only_autofix_count) from the Actions autofix
    loop; omit them for strict checks (prepare deploy, chat review).
    """
    body = (review_body or "").strip()
    if not body:
        return False

    if review_has_minor_only_findings(body):
        if autofix_count is None:
            return False
        max_iters = autofix_max_iterations()
        nits_max = autofix_nits_only_max_rounds()
        nits_count = int(nits_only_autofix_count or 0)
        if autofix_count >= max_iters or nits_count >= nits_max:
            return True
        return False

    should_fix, _reason = review_needs_autofix(body)
    if should_fix:
        return False
    sections = _section_bodies(body)
    if sections:
        # A trailing "ready to merge" line must not override leftover nits.
        return not (
            _section_has_findings(sections.get("blockers", ""))
            or _section_has_findings(sections.get("important", ""))
            or _section_has_findings(sections.get("minor", ""))
        )
    if _NIT_ONLY.search(body):
        return False
    if _CLEAN.search(body):
        return True
    return False
