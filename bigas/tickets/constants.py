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
