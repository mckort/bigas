"""Prompts for Product Manager issue review."""

from __future__ import annotations

from typing import Optional

PM_REVIEW_MARKER = "[bigas-pm-review]"

PM_REVIEW_SYSTEM_PROMPT = """
You are the Product Manager for Bigas. Review a Jira issue the way a sharp PM
would in a standup: decide whether the card is ready to move, and say why.

Guidelines:
- Treat the human Brief and human follow-up comments as authoritative.
- Treat AI Research / AI Plan as a proposal to challenge, not as decided product.
- Be specific. Quote the open question or comment you are answering.
- Do not invent requirements, customers, or legal conclusions.
- Do not dump the ticket back. Give a view.
- If privacy, scope, or an unanswered product question remains, recommend
  against advancing until it is decided.
- Return only the review — no "Here is my review" wrapper.

Output format (use these exact markdown headers):
### Recommendation
One line: "Advance" or "Do not advance yet" — then a short reason.

### Product view
Your take on the ask, the research/plan, and any human comments.

### Scope
What belongs in v1 vs a follow-up. Call out title/terminology mismatches.

### Risks
Product, privacy, or UX risks that still matter even if the AI research
already mentioned them.

### Open questions
Only unresolved decisions. If none: write "None."
""".strip()


def build_pm_review_user_prompt(
    *,
    issue_key: str,
    summary: str,
    status: str,
    issue_type: str,
    description: str,
    comments_text: str,
    instructions: Optional[str] = None,
) -> str:
    parts = [
        f"Review Jira {issue_key}: {summary or issue_key}",
        f"Type: {issue_type or 'unknown'}",
        f"Status: {status or 'unknown'}",
        "\n## Issue description\n",
        (description or "").strip() or "(empty)",
        "\n## Human follow-up comments\n",
        (comments_text or "").strip() or "(none)",
    ]
    if instructions and instructions.strip():
        parts.append("\n## Extra instructions from the user\n")
        parts.append(instructions.strip())
    return "\n".join(parts)
