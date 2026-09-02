"""Assign active fix versions when implementation work starts (BIG-42)."""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def ensure_active_fix_version(
    jira: Any,
    *,
    issue_key: str,
    project_key: str = "",
) -> Optional[str]:
    """
    Assign the active unreleased fix version when missing.

    Works with JiraClient (Jira Cloud) and TicketJiraAdapter (internal board).
    """
    if not hasattr(jira, "ensure_issue_fix_version"):
        return None
    try:
        return jira.ensure_issue_fix_version(
            issue_key,
            project_key=project_key or None,
        )
    except Exception as exc:
        logger.warning(
            "Could not assign fix version for %s: %s",
            issue_key,
            exc,
            exc_info=True,
        )
        return None
