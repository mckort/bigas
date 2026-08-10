"""Prompts for the progress-updates coach message."""

PROGRESS_UPDATES_SYSTEM_PROMPT = """You are an assistant that summarizes recent product progress in a clear and professional way.
Your role is to highlight what has been achieved during the period, focusing on outcomes and impact rather than process details.

Guidelines:
- Use the provided stats and list of completed work to identify the main themes, milestones, and improvements.
- Keep the tone clear, professional, and balanced; moderate enthusiasm is fine, but avoid hype or overly informal language.
- Do not repeat internal ticket IDs (like SCRUM-123) or individual assignee names; instead, talk about the team’s work at a collective level.
- Summarize outcomes rather than step-by-step technical or operational details.
- When multiple Jira projects are listed, structure the update with one short section per project (use the project key as the heading). Include projects with zero completed issues as a single line noting no Done items this period.
- Keep the overall message concise but informative; suitable for a stakeholder or team Discord update.
- Always return a non-empty response.
- Return a plain-text message suitable for posting in a team or stakeholder channel."""


def build_progress_updates_user_prompt(
    *,
    stats: dict,
    done_issues_text: str,
    days: int,
) -> str:
    """Build the user prompt with stats and the list of completed issues."""
    by_project = stats.get("by_project") or {}
    project_lines = []
    if by_project:
        project_lines.append("Per project:")
        for key, count in by_project.items():
            project_lines.append(f"- {key}: {count} issue(s) moved to Done")
    project_block = "\n".join(project_lines)

    return f"""Below are the progress stats and the list of work completed in the last {days} days.

Using this data, write a concise progress report for the period.
If more than one project appears below, present progress **project by project** with a clear heading for each project key (e.g. "## VFA"), then 2–5 short bullets or one short paragraph for that project. Mention projects with 0 Done items briefly.
Focus on themes, outcomes, and impact — not internal ticket IDs or individual team members. Do not mention Jira issue keys (like SCRUM-123) or personal names; describe the work at the level of “the team” or “we”.
End with one brief, balanced outlook for the upcoming period. Always provide a non-empty answer.

Stats:
- Total issues moved to Done: {stats.get('total', 0)}
- By type: {stats.get('by_type', {})}
{project_block}

Issues moved to Done in the last {days} days (for your reference only – do not copy IDs or names directly):

{done_issues_text}
"""
