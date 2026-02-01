from __future__ import annotations

from typing import Any, Dict, List, Literal

SectionKey = Literal["new_features", "improvements", "bug_fixes"]


def categorize_issue(issue: Dict[str, Any]) -> SectionKey:
    """
    Deterministic categorization so we don't miss anything.
    """
    issue_type = (issue.get("issue_type") or "").strip().lower()
    labels = [str(x).lower() for x in (issue.get("labels") or [])]

    if "bug" in issue_type or "bugfix" in labels or "bug-fix" in labels:
        return "bug_fixes"

    # Common Jira types that tend to be user-facing.
    if issue_type in {"story", "epic", "feature"}:
        return "new_features"

    # Tasks, improvements, chores, spikes usually go here.
    return "improvements"


def group_issues(issues: List[Dict[str, Any]]) -> Dict[SectionKey, List[Dict[str, Any]]]:
    grouped: Dict[SectionKey, List[Dict[str, Any]]] = {
        "new_features": [],
        "improvements": [],
        "bug_fixes": [],
    }
    for issue in issues:
        grouped[categorize_issue(issue)].append(issue)
    return grouped


def render_customer_markdown(grouped_lines: Dict[SectionKey, List[str]]) -> str:
    """
    Render customer-facing markdown with required headings.
    """
    def section(title: str, items: List[str]) -> str:
        if not items:
            return f"## {title}\n\n- (No items)\n"
        bullets = "\n".join(f"- {x}" for x in items)
        return f"## {title}\n\n{bullets}\n"

    return "\n".join(
        [
            section("New features", grouped_lines.get("new_features", [])),
            section("Improvements", grouped_lines.get("improvements", [])),
            section("Bug Fixes", grouped_lines.get("bug_fixes", [])),
        ]
    ).strip()

