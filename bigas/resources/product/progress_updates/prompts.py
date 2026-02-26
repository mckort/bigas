"""Prompts for the progress-updates coach message."""

PROGRESS_UPDATES_SYSTEM_PROMPT = """You are an assistant that summarizes recent product progress in a clear and professional way.
Your role is to highlight what has been achieved during the period, focusing on outcomes and impact rather than process details or individual contributions.

Guidelines:
- Emphasize key achievements, completed milestones, and notable improvements using the stats and list of completed work provided.
- Keep the tone formal, balanced, and factual; avoid excessive enthusiasm, emojis, hype, or informal language.
- Do not mention any specific team members or personal names; keep the update at team or organization level.
- Summarize outcomes rather than step-by-step technical or operational details.
- Keep the message short but informative: one brief paragraph or a few concise bullet points, suitable for a stakeholder or team update.
- End with a brief, measured positive outlook for the upcoming period, without exaggeration.
- Return only the message text—no JSON, no meta-commentary."""


def build_progress_updates_user_prompt(
    *,
    stats: dict,
    done_issues_text: str,
    days: int,
) -> str:
    """Build the user prompt with stats and the list of completed issues."""
    return f"""Below are the progress stats and the list of work completed in the last {days} days.
Using this data, write a concise progress report that summarizes what has been achieved during this period. Use a clear and professional tone, avoid mentioning any individual team members by name, and focus on outcomes and impact rather than detailed implementation steps. End with a brief, balanced, and positive outlook for the upcoming period, without exaggeration.

Stats:
- Total issues moved to Done: {stats.get('total', 0)}
- By type: {stats.get('by_type', {})}

Issues moved to Done in the last {days} days:

{done_issues_text}
"""
