"""Column definitions for internal Kanban boards."""
from __future__ import annotations

from typing import List, Optional, Tuple

# Personal task boards — no AI workflow automation.
PERSONAL_COLUMNS: Tuple[str, ...] = (
    "To Do",
    "In Progress",
    "Review",
    "Done",
)

# Project-connected boards — mirrors Jira AI workflow columns.
PROJECT_COLUMNS: Tuple[str, ...] = (
    "To Do",
    "Research and describe (AI)",
    "Description approval (manual)",
    "Design and plan (AI)",
    "Design approval (manual)",
    "In Progress (AI)",
    "Final approval (manual)",
    "Done",
)

AI_TRIGGER_STATUSES = frozenset(
    {
        "Research and describe (AI)",
        "Design and plan (AI)",
        "In Progress (AI)",
    }
)

# Shipped enough to belong on a versioned cut. Final approval is tested in prod
# before the card is dragged to Done, so it must stay on the release.
RELEASE_CUT_STATUSES = frozenset(
    {
        "Done",
        "Final approval (manual)",
    }
)


def is_in_release_cut(status: str) -> bool:
    raw = (status or "").strip()
    if raw in RELEASE_CUT_STATUSES:
        return True
    resolved = resolve_column_status(raw, project_key="X")
    return bool(resolved and resolved in RELEASE_CUT_STATUSES)


def columns_for_board(*, project_key: Optional[str]) -> List[str]:
    """Return workflow columns for a board (project-linked vs personal)."""
    if project_key:
        return list(PROJECT_COLUMNS)
    return list(PERSONAL_COLUMNS)


def next_column(current: str, *, project_key: Optional[str]) -> Optional[str]:
    """Return the next column after *current*, or None if already at the end."""
    cols = columns_for_board(project_key=project_key)
    try:
        idx = cols.index(current)
    except ValueError:
        return cols[0] if cols else None
    if idx + 1 >= len(cols):
        return None
    return cols[idx + 1]


def is_valid_status(status: str, *, project_key: Optional[str]) -> bool:
    return status in columns_for_board(project_key=project_key)


def resolve_column_status(name: str, *, project_key: Optional[str]) -> Optional[str]:
    """Map a column/status label (or alias like 'Final Review') to a board column."""
    cols = columns_for_board(project_key=project_key)
    raw = (name or "").strip()
    if not raw:
        return None
    if raw in cols:
        return raw
    lower = raw.lower()
    for col in cols:
        if col.lower() == lower:
            return col
    aliases = {
        "todo": "To Do",
        "to-do": "To Do",
        "in progress": "In Progress (AI)" if project_key else "In Progress",
        "research": "Research and describe (AI)",
        "research and describe": "Research and describe (AI)",
        "description approval": "Description approval (manual)",
        "design and plan": "Design and plan (AI)",
        "design approval": "Design approval (manual)",
        "final review": "Final approval (manual)",
        "final approval": "Final approval (manual)",
        "done": "Done",
        "review": None if project_key else "Review",
    }
    aliased = aliases.get(lower)
    if aliased and aliased in cols:
        return aliased
    matches = [col for col in cols if lower in col.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def unknown_column_error(name: str, *, project_key: Optional[str]) -> str:
    cols = ", ".join(columns_for_board(project_key=project_key))
    return f"Unknown column {name!r}. Use one of: {cols}"
