"""Normalize and merge ticket labels the same way Jira does."""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional

MARKETING_LABEL = "marketing"

_LABEL_KEEP_RE = re.compile(r"[^a-z0-9._-]+")
_MULTI_DASH_RE = re.compile(r"-{2,}")


def normalize_label(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = raw.get("name") or raw.get("label") or ""
    text = str(raw or "").strip().lower().replace(" ", "-")
    text = _LABEL_KEEP_RE.sub("-", text)
    text = _MULTI_DASH_RE.sub("-", text).strip("-._")
    return text[:255]


def normalize_labels(
    raw: Optional[Iterable[Any]] = None,
    *,
    marketing: bool = False,
) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in raw or []:
        label = normalize_label(item)
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    if marketing and MARKETING_LABEL not in seen:
        out.append(MARKETING_LABEL)
    return out


def has_marketing(labels: Optional[Iterable[Any]] = None) -> bool:
    return any(normalize_label(item) == MARKETING_LABEL for item in (labels or []))


def resolve_ticket_labels(ticket: Optional[dict] = None) -> List[str]:
    ticket = ticket or {}
    return normalize_labels(ticket.get("labels"), marketing=bool(ticket.get("marketing")))


def merge_labels(*groups: Optional[Iterable[Any]]) -> List[str]:
    combined: List[Any] = []
    for group in groups:
        if group:
            combined.extend(list(group))
    return normalize_labels(combined)
