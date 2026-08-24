"""Prompts for the progress-updates coach message."""

from __future__ import annotations

from typing import Optional, Sequence

PROGRESS_UPDATES_SYSTEM_PROMPT = """You are an assistant that summarizes recent product progress in a clear and professional way.
Your role is to highlight what has been achieved during the period, focusing on outcomes and impact rather than process details.

Guidelines:
- Use Done items (Jira or the internal board) and git commit activity together.
- Keep the tone clear, professional, and balanced; moderate enthusiasm is fine, but avoid hype or overly informal language.
- Do not repeat internal ticket IDs (like SCRUM-123), commit SHAs, or individual assignee/author names.
- Summarize outcomes rather than step-by-step technical or operational details.
- Keep the message compact for Discord: short sections, no fluff.
- When grouping by project:
  - Write a short **ProjectKey** heading only for projects with real activity (Done items and/or meaningful git commits).
  - Under each active project, briefly cover themes from tickets and/or git (you may note autofix/automation lightly if relevant, without drowning the update).
  - Put projects with neither Done items nor git commits on a single line:
    `No activity: KEY1, KEY2, KEY3`
  - Do not give each inactive project its own section.
- When grouping by label:
  - Use the provided label headings EXACTLY (including Unlabeled). Do not invent product-area names such as Billing, Dealflow, or Competitive Intelligence.
  - Ignored labels (e.g. customer-request) are not section headings; tickets still appear under their remaining labels or Unlabeled.
  - A ticket may appear under more than one remaining label; mention it once.
- Deduplicate: if Done tickets and git commits describe the same work, mention it once.
- End with a brief **Outlook** (1–2 sentences).
- Always return a non-empty plain-text message suitable for a team Discord channel."""


def build_progress_updates_user_prompt(
    *,
    stats: dict,
    done_issues_text: str,
    days: int,
    git_commits_text: str = "",
    git_stats: Optional[dict] = None,
    group_by: str = "project",
    ignore_labels: Optional[Sequence[str]] = None,
) -> str:
    """Build the user prompt with Done stats and optional git commit activity."""
    by_project = stats.get("by_project") or {}
    git_stats = git_stats or {}
    grouping = (group_by or "project").strip().lower()
    ignored = list(ignore_labels or [])

    active: list[str] = []
    inactive: list[str] = []
    all_keys = sorted(set(list(by_project.keys()) + list(git_stats.keys())))
    for key in all_keys:
        jira_n = int((by_project.get(key) if by_project else 0) or 0)
        git_n = int(((git_stats.get(key) or {}).get("total")) or 0)
        if jira_n > 0 or git_n > 0:
            active.append(key)
        else:
            inactive.append(key)

    project_lines = ["Per project:"]
    for key in all_keys:
        jira_n = int((by_project.get(key) if by_project else 0) or 0)
        g = git_stats.get(key) or {}
        git_n = int(g.get("total") or 0)
        autofix_n = int(g.get("autofix") or 0)
        repo = g.get("repo") or ""
        project_lines.append(
            f"- {key}: Done={jira_n}, git commits={git_n}"
            + (f" (autofix={autofix_n})" if autofix_n else "")
            + (f", repo={repo}" if repo else "")
        )
    if active:
        project_lines.append(f"Projects with activity (expand these): {', '.join(active)}")
    if inactive:
        project_lines.append(
            f"Projects with no activity (collapse to one line): {', '.join(inactive)}"
        )
    project_block = "\n".join(project_lines)

    git_block = (git_commits_text or "").strip() or "(No git commit activity fetched.)"
    label_counts = stats.get("by_label") or {}
    label_block = ""
    if grouping == "label" and label_counts:
        ignore_text = ", ".join(ignored) if ignored else "(none)"
        label_lines = [f"Per remaining label (ignored: {ignore_text}):"]
        for label in label_counts:
            label_lines.append(f"- {label}: {label_counts[label]}")
        label_block = "\n".join(label_lines)

    if grouping == "label":
        ignore_text = ", ".join(ignored) if ignored else "(none)"
        format_requirements = f"""Format requirements:
1. Optional one-line intro.
2. One section per **label heading already listed below** (including Unlabeled). Use those names verbatim.
3. Do **not** invent product-area headings. Ignored labels ({ignore_text}) are not groups.
4. If any projects have no activity, add exactly one line:
   `No activity: KEY1, KEY2, …`
5. End with a short **Outlook** (1–2 sentences)."""
    else:
        format_requirements = """Format requirements:
1. Optional one-line intro.
2. For each project with activity (Done items and/or git commits): a bold/heading project key, then 2–5 short bullets covering the main outcomes. Merge overlapping ticket/git signals; do not list every commit.
3. If any projects have no activity, add exactly one line:
   `No activity: KEY1, KEY2, …`
   Do **not** create a section per inactive project.
4. End with a short **Outlook** (1–2 sentences)."""

    extra_stats = f"\n{label_block}\n" if label_block else "\n"

    return f"""Below are Done-ticket stats and git commit activity for the last {days} days.

Write a **compact** Discord progress report for the period.

{format_requirements}

Focus on themes, outcomes, and impact — not ticket IDs, commit hashes, or personal names. Always provide a non-empty answer.

Stats:
- Total issues moved to Done: {stats.get('total', 0)}
- By type: {stats.get('by_type', {})}
{project_block}
{extra_stats}
Done issues (reference only – do not copy IDs or names):

{done_issues_text}

Git commits on default branches (reference only – do not copy hashes or author names; commits tagged [autofix] are automated PR fixes):

{git_block}
"""
