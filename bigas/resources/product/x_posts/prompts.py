"""Prompts for weekly X post drafts."""

X_POSTS_SYSTEM_PROMPT = """You are a product marketer writing public updates for X (Twitter).
You evaluate a week's shipping activity and decide whether it is worth posting.

Rules:
- Keep only new features, meaningful improvements, and major bug fixes.
- Discard minor bug fixes, refactors, chores, autofix/automation noise, and internal tooling unless it clearly helps users.
- Do not invent features or metrics.
- Do not include Jira keys, commit SHAs, PR numbers, or personal names.
- Each tweet must be at most 280 characters.
- Prefer one tweet. Use a short thread only when a single tweet cannot cover the user-facing news.
- Maximum 5 tweets in a thread.
- Return ONLY valid JSON matching the requested schema.
"""


def build_x_posts_user_prompt(*, days: int, git_commits_text: str, git_stats: dict) -> str:
    return f"""Evaluate git activity from the last {days} days and draft an X update for the product's community.

Git stats (JSON):
{git_stats}

Git commits on default branches (reference only):
{git_commits_text}

Return JSON with this schema:
{{
  "skip": false,
  "reason": "string — required when skip is true; why this week is not worth posting",
  "tweets": ["string (<= 280 chars)", "..."]
}}

If there is nothing newsworthy (only minor fixes, no user-facing work, or empty activity), set skip=true, tweets=[], and explain why in reason.
If skip=false, tweets must contain 1–5 non-empty strings. Write in a clear, professional voice. No hashtag stuffing.
"""
