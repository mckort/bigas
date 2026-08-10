"""Prompts for the progress-updates coach message."""

PROGRESS_UPDATES_SYSTEM_PROMPT = """You are an assistant that summarizes recent product progress in a clear and professional way.
Your role is to highlight what has been achieved during the period, focusing on outcomes and impact rather than process details.

Guidelines:
- Use the provided stats and list of completed work to identify the main themes, milestones, and improvements.
- Keep the tone clear, professional, and balanced; moderate enthusiasm is fine, but avoid hype or overly informal language.
- Do not repeat internal ticket IDs (like SCRUM-123) or individual assignee names; instead, talk about the team’s work at a collective level.
- Summarize outcomes rather than step-by-step technical or operational details.
- Keep the message compact for Discord: short sections, no blank lines between inactive projects, no fluff.
- When multiple Jira projects are listed:
  - Write a short **ProjectKey** heading + 2–5 bullets (or one short paragraph) only for projects that have Done items.
  - Put all projects with zero Done items on a single line: `No Done items: KEY1, KEY2, KEY3`.
  - Do not give each empty project its own section.
- End with a brief **Outlook** (1–2 sentences).
- Always return a non-empty plain-text message suitable for a team Discord channel."""


def build_progress_updates_user_prompt(
    *,
    stats: dict,
    done_issues_text: str,
    days: int,
) -> str:
    """Build the user prompt with stats and the list of completed issues."""
    by_project = stats.get("by_project") or {}
    active = [k for k, n in by_project.items() if int(n or 0) > 0]
    empty = [k for k, n in by_project.items() if int(n or 0) <= 0]

    project_lines = []
    if by_project:
        project_lines.append("Per project:")
        for key, count in by_project.items():
            project_lines.append(f"- {key}: {count} issue(s) moved to Done")
        if active:
            project_lines.append(f"Projects with Done items (expand these): {', '.join(active)}")
        if empty:
            project_lines.append(
                f"Projects with zero Done items (collapse to one line): {', '.join(empty)}"
            )
    project_block = "\n".join(project_lines)

    return f"""Below are the progress stats and the list of work completed in the last {days} days.

Write a **compact** Discord progress report for the period.

Format requirements:
1. Optional one-line intro.
2. For each project with Done items only: a bold/heading project key, then 2–5 short bullets (or one short paragraph).
3. If any projects have 0 Done items, add exactly one line:
   `No Done items: KEY1, KEY2, …`
   Do **not** create a section per empty project.
4. End with a short **Outlook** (1–2 sentences).

Focus on themes, outcomes, and impact — not internal ticket IDs or individual team members. Do not mention Jira issue keys (like SCRUM-123) or personal names; describe the work at the level of “the team” or “we”. Always provide a non-empty answer.

Stats:
- Total issues moved to Done: {stats.get('total', 0)}
- By type: {stats.get('by_type', {})}
{project_block}

Issues moved to Done in the last {days} days (for your reference only – do not copy IDs or names directly):

{done_issues_text}
"""
