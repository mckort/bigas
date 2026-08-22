"""Configuration helpers for internal board vs external Jira."""
from __future__ import annotations

import os


def jira_configured() -> bool:
    """True when all required Jira env vars are set."""
    base = (os.environ.get("JIRA_BASE_URL") or "").strip()
    email = (os.environ.get("JIRA_EMAIL") or "").strip()
    token = (os.environ.get("JIRA_API_TOKEN") or "").strip()
    keys = (
        os.environ.get("JIRA_PROJECT_KEYS")
        or os.environ.get("JIRA_PROJECT_KEY")
        or ""
    ).strip()
    return bool(base and email and token and keys)


def use_internal_board() -> bool:
    """
    Use the native Bigas Kanban board instead of Jira.

    Default: internal board when Jira is not configured, or when
    USE_INTERNAL_BOARD=true is set explicitly.
    """
    flag = (os.environ.get("USE_INTERNAL_BOARD") or "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return not jira_configured()
