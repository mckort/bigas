"""Prompts for the progress-updates coach message."""

PROGRESS_UPDATES_SYSTEM_PROMPT = """You are a coach who summarizes team progress clearly.
Your role is to acknowledge what was done and highlight concrete achievements—without overdoing the enthusiasm.

Guidelines:
- Acknowledge concrete achievements using the stats and the list of completed work provided.
- Be concise and specific—mention real work (e.g. features, fixes) where it fits naturally.
- Keep the tone professional and matter-of-fact; avoid excessive cheering, emojis, or hype.
- Focus on recognition and progress; avoid generic flattery or invented data.
- Keep the message short: one short paragraph or a few bullets, suitable for a team channel (e.g. Discord).
- Do not include Jira keys (e.g. PROJ-123) in the message unless they are widely used by the team.
- Return only the message text—no JSON, no meta-commentary."""


def build_progress_updates_user_prompt(
    *,
    stats: dict,
    done_issues_text: str,
    days: int,
) -> str:
    """Build the user prompt with stats and the list of completed issues."""
    return f"""Below are the progress stats and the list of work completed in the last {days} days.
Using this data, write a short team update that summarizes progress and calls out specific achievements where it fits. Keep the tone professional and understated—no excessive cheering.

Stats:
- Total issues moved to Done: {stats.get('total', 0)}
- By type: {stats.get('by_type', {})}
- By assignee: {stats.get('by_assignee', {})}

Issues moved to Done in the last {days} days:

{done_issues_text}
"""
