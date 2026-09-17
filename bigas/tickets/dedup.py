"""Reuse an open ticket instead of creating a same-title duplicate."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

_MIN_NEAR_TITLE_CHARS = 24
_NEAR_TITLE_RATIO = 0.85
_TERMINAL_STATUSES = {"done", "closed", "resolved", "cancelled", "canceled", "completed"}
_VERSION_SUFFIX_RE = re.compile(r"^v\d+(\.\d+)*$", re.I)


def normalize_ticket_title(title: str) -> str:
    t = (title or "").strip().rstrip(".,:;!?").strip()
    return re.sub(r"\s+", " ", t.lower())


def _is_version_suffix(remainder: str) -> bool:
    r = (remainder or "").strip()
    return bool(r and _VERSION_SUFFIX_RE.match(r))


def ticket_titles_collide(left: str, right: str) -> bool:
    """True for the same title, ignoring case/whitespace, or a near-identical wording."""
    a = normalize_ticket_title(left)
    b = normalize_ticket_title(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer and len(shorter) >= _MIN_NEAR_TITLE_CHARS:
        if (len(shorter) / len(longer)) < _NEAR_TITLE_RATIO:
            return False
        idx = longer.find(shorter)
        if idx == 0:
            suffix = longer[len(shorter) :].strip()
            if _is_version_suffix(suffix):
                return False
        elif idx + len(shorter) == len(longer):
            prefix = longer[:idx].strip()
            if _is_version_suffix(prefix):
                return False
        return True
    return False


def find_open_duplicate_ticket(
    tickets: Iterable[Dict[str, Any]],
    *,
    title: str,
    issue_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    wanted_type = (issue_type or "").strip().title()
    for ticket in tickets or ():
        if (ticket.get("status") or "").strip().lower() in _TERMINAL_STATUSES:
            continue
        if wanted_type:
            existing_type = (ticket.get("issue_type") or "").strip().title()
            if existing_type and existing_type != wanted_type:
                continue
        existing_title = ticket.get("title") or ticket.get("summary") or ""
        if ticket_titles_collide(title, existing_title):
            return ticket
    return None
