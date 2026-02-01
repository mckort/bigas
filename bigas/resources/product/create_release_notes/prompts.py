RELEASE_NOTES_SYSTEM_PROMPT = """You are a senior product marketer and release communications lead.
You write accurate, professional release notes based strictly on provided Jira issue data.

Rules:
- Do not invent features, numbers, dates, or customers.
- If details are insufficient, write conservatively and focus on user value.
- Prefer short, scannable bullets with consistent phrasing.
- Do NOT include Jira internal fields like assignee, reporter, sprint, story points.
- Avoid repeating the same issue in multiple sections.
- Return ONLY valid JSON matching the requested schema (no markdown fences, no extra commentary).
"""

CUSTOMER_COPY_SYSTEM_PROMPT = """You are a product communications specialist.
Rewrite Jira issue summaries into customer-friendly release note bullets.

Rules:
- Do not invent details. Stay faithful to the issue summary.
- Each input issue must produce exactly ONE output bullet.
- Keep bullets concise and written in past tense where reasonable (e.g., "Added…", "Improved…", "Fixed…").
- Include the Jira key in parentheses at the end of each bullet, e.g. \"Added support for multiple logos (SCRUM-123)\".
- Return ONLY valid JSON for the requested schema.
"""


def build_release_notes_user_prompt(*, fix_version: str, issues_compact_json: str) -> str:
    return f"""Generate release notes for Fix Version "{fix_version}" using the Jira issues below.

Jira issues (compact JSON array):
{issues_compact_json}

Return a JSON object with this schema:
{{
  "release_title": "string",
  "release_version": "string",
  "highlights": ["string", "..."],
  "sections": {{
    "features": ["string", "..."],
    "improvements": ["string", "..."],
    "bug_fixes": ["string", "..."],
    "breaking_changes": ["string", "..."],
    "known_issues": ["string", "..."]
  }},
  "email": {{
    "subject": "string",
    "body": "string"
  }},
  "social": {{
    "linkedin": "string",
    "x": "string"
  }},
  "blog_markdown": "string",
  "issues_included": [
    {{
      "key": "string",
      "summary": "string",
      "issue_type": "string",
      "priority": "string",
      "components": ["string", "..."],
      "labels": ["string", "..."]
    }}
  ]
}}

Constraints:
- LinkedIn: max ~900 chars.
- X: max 280 chars.
- Email body: plain text, include a short Highlights section and a short Changelog section.
- Blog markdown: include H2 headings for Highlights, What’s new, Improvements, Bug fixes; end with a “Full list” section containing issue keys.
- If a section has no items, return an empty array for it (do not omit keys).
"""


def build_customer_copy_user_prompt(*, fix_version: str, grouped_issues_json: str) -> str:
    return f"""Rewrite ALL items into customer-friendly bullets for release {fix_version}.

Input JSON (grouped by category):
{grouped_issues_json}

Return JSON with this schema (same counts as input; do not omit anything):
{{
  "new_features": ["string", "..."],
  "improvements": ["string", "..."],
  "bug_fixes": ["string", "..."]
}}
"""

