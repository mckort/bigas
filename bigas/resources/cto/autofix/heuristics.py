"""Decide whether a Bigas PR review warrants launching Cursor autofix."""
from __future__ import annotations

import re
from typing import Tuple

AUTOFIX_COMMIT_MARKER = "[bigas-autofix]"
# Must contain AUTOFIX_COMMIT_MARKER so loop protection and the Actions
# skip-on-autofix-head gate still treat nits-only commits as autofix.
AUTOFIX_MINOR_COMMIT_MARKER = "[bigas-autofix] [nits-only]"
DEFAULT_AUTOFIX_MAX_ITERATIONS = 5
DEFAULT_MINOR_AUTOFIX_ITERATIONS = 2
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


def minor_autofix_max_iterations() -> int:
    """Max nits-only autofix rounds (env BIGAS_CTO_AUTOFIX_MINOR_ITERATIONS)."""
    import os

    raw = (os.environ.get("BIGAS_CTO_AUTOFIX_MINOR_ITERATIONS") or "").strip()
    if not raw:
        return DEFAULT_MINOR_AUTOFIX_ITERATIONS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MINOR_AUTOFIX_ITERATIONS


def leftover_nits_are_acceptable(
    *,
    autofix_count: int = 0,
    minor_autofix_count: int = 0,
    max_iterations: int | None = None,
    minor_max_iterations: int | None = None,
) -> bool:
    """True when leftover Minor findings may auto-merge."""
    max_iters = autofix_max_iterations() if max_iterations is None else max_iterations
    minor_max = (
        minor_autofix_max_iterations()
        if minor_max_iterations is None
        else minor_max_iterations
    )
    return autofix_count >= max_iters or minor_autofix_count >= minor_max


def count_autofix_rounds(messages: list[str]) -> tuple[int, int]:
    """Return (all autofix commits, nits-only autofix commits)."""
    autofix = 0
    minor = 0
    for raw in messages:
        msg = raw or ""
        if AUTOFIX_COMMIT_MARKER in msg:
            autofix += 1
        if AUTOFIX_MINOR_COMMIT_MARKER in msg:
            minor += 1
    return autofix, minor


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
_HTML_COMMENT = re.compile(r"^\s*<!--.*?-->\s*$")
_LIST_ITEM = re.compile(r"(?m)^\s*(?:[-*]|\d+\.)\s+\S")
_EMPTY_SECTION_PREFIX = re.compile(
    r"(?is)^(none\.?|n/?a\.?|no (issues|findings|blockers|important issues)\.?)\s*"
)
# "no new blocker or important issues" in an LGTM closer is not a finding.
_NEGATED_ACTIONABLE = re.compile(
    r"(?i)\bno(?:\s+\w+){0,6}\s+"
    r"(blocking|critical|important|security|vulnerability|bug|broken|"
    r"incorrect|regression)\b"
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


def _is_list_item(line: str) -> bool:
    return bool(re.match(r"\s*(?:[-*]|\d+\.)\s+\S", line or ""))


def _closer_line_is_safe_to_strip(line: str) -> bool:
    """True for a trailing LGTM sentence, including 'no … important issues'."""
    if not line or not _CLEAN.search(line) or _is_list_item(line):
        return False
    if not _ACTIONABLE.search(line):
        return True
    return bool(_NEGATED_ACTIONABLE.search(line))


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
    empty_prefix = _EMPTY_SECTION_PREFIX.match(text)
    if empty_prefix:
        rest = text[empty_prefix.end() :].strip()
        if not rest:
            return False
        # "None." plus a verdict sentence is still empty — not leftover nits.
        if not _LIST_ITEM.search(rest) and _CLEAN.search(rest):
            return False
    return True


def _strip_section_closer(body: str) -> str:
    """Drop a trailing LGTM / ready-to-merge closer so it is not a finding."""
    lines = (body or "").splitlines()

    def _pop_blank_and_comments() -> None:
        while lines and (
            not lines[-1].strip() or _HTML_COMMENT.match(lines[-1])
        ):
            lines.pop()

    _pop_blank_and_comments()
    while lines and _closer_line_is_safe_to_strip(lines[-1]):
        lines.pop()
        _pop_blank_and_comments()
    return "\n".join(lines).strip()


def review_is_nits_only(review_body: str) -> bool:
    """True when leftover findings are Minor / nits only (no Blockers or Important)."""
    body = (review_body or "").strip()
    if not body or "<!-- bigas-autofix-skip -->" in body:
        return False
    sections = _section_bodies(body)
    if sections:
        if _section_has_findings(sections.get("blockers", "")):
            return False
        if _section_has_findings(sections.get("important", "")):
            return False
        return _section_has_findings(sections.get("minor", ""))

    has_actionable = bool(_ACTIONABLE.search(body))
    if has_actionable:
        return False
    nit_only = bool(_NIT_ONLY.search(body))
    soft_only = bool(_SOFT_ONLY.search(body))
    return nit_only or soft_only


def review_needs_autofix(
    review_body: str,
    *,
    autofix_count: int = 0,
    minor_autofix_count: int = 0,
    max_iterations: int | None = None,
    minor_max_iterations: int | None = None,
) -> Tuple[bool, str]:
    """
    Return (should_run, reason).

    Autofix runs for Blockers/Important, and for leftover Minor / nits until
    two nits-only rounds or the overall autofix cap.
    """
    body = (review_body or "").strip()
    if not body:
        return False, "empty review body"
    if "<!-- bigas-autofix-skip -->" in body:
        return False, "review contains autofix-skip marker"

    nits_budget_done = leftover_nits_are_acceptable(
        autofix_count=autofix_count,
        minor_autofix_count=minor_autofix_count,
        max_iterations=max_iterations,
        minor_max_iterations=minor_max_iterations,
    )

    sections = _section_bodies(body)
    if sections:
        if _section_has_findings(sections.get("blockers", "")):
            return True, "actionable findings in review"
        if _section_has_findings(sections.get("important", "")):
            return True, "actionable findings in review"
        if _section_has_findings(sections.get("minor", "")):
            if nits_budget_done:
                return False, "only non-blocking / nit suggestions"
            return True, "leftover minor findings"
        return False, "review looks clean (LGTM)"

    has_actionable = bool(_ACTIONABLE.search(body))
    looks_clean = bool(_CLEAN.search(body))
    nit_only = bool(_NIT_ONLY.search(body)) and not has_actionable
    soft_only = bool(_SOFT_ONLY.search(body)) and not has_actionable

    if has_actionable:
        return True, "actionable findings in review"
    if nit_only or soft_only:
        if nits_budget_done:
            return False, "only non-blocking / nit suggestions"
        return True, "leftover minor findings"
    if looks_clean:
        return False, "review looks clean (LGTM)"

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
    autofix_count: int = 0,
    minor_autofix_count: int = 0,
    max_iterations: int | None = None,
    minor_max_iterations: int | None = None,
) -> bool:
    """
    True when the review is fully clean, or leftover Minor may be accepted.

    Leftover nits are accepted after two nits-only autofix rounds, or when the
    overall autofix cap is reached and only Minor remains.
    """
    kwargs = {
        "autofix_count": autofix_count,
        "minor_autofix_count": minor_autofix_count,
        "max_iterations": max_iterations,
        "minor_max_iterations": minor_max_iterations,
    }
    should_fix, _reason = review_needs_autofix(review_body, **kwargs)
    if should_fix:
        return False
    body = (review_body or "").strip()
    if not body:
        return False
    if review_is_nits_only(body) and leftover_nits_are_acceptable(**kwargs):
        return True
    sections = _section_bodies(body)
    if sections:
        # A trailing "ready to merge" line must not override leftover nits
        # unless the nits autofix budget above already accepted them.
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
