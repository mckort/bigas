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
- Do NOT include Jira keys (e.g. SCRUM-123) or any internal tracking identifiers.
- Return ONLY valid JSON for the requested schema.
"""

COMMS_PACK_SYSTEM_PROMPT = """You are a product communications specialist.
Create customer-facing release notes and social drafts from provided Jira issues.

Rules:
- Do not invent details. Stay faithful to the issue summaries.
- Do NOT include Jira keys (e.g. SCRUM-123) or any internal tracking identifiers.
- Use customer-friendly language and explain value briefly.
- Do not omit any issues. Every issue must appear exactly once across the three sections.
- Return ONLY valid JSON for the requested schema.
"""

# Example of the desired level of detail for the proposed blog post (features = title + short paragraph; improvements = descriptive bullets; bug fixes = one line each; end with a short closing).
BLOG_POST_DETAIL_EXAMPLE = """
New Feature
Multiple Logos on a Single Garment

You can now add multiple logos to your custom apparel!

For example: place your company logo on the left chest and a special message like "Team Meetup 2026" on the back. This gives you more creative flexibility to design the perfect promotional or team wear.

Key Improvements
We've focused on streamlining the experience and adding helpful details:

Clearer post-order flow: More transparency on what happens after you click "Accept Order."
Fewer clicks to complete and accept orders.
Simplified one-size products: Recipients now only need to provide a shipping address.
Smarter preference selection: The correct preference tab (e.g., t-shirt vs. sweatshirt) is automatically selected based on the item received.
Editable template names for easier organization.
Better size selection visibility: See at a glance whether sizes are available for all workspace members (while keeping individual size choices private).
Less intrusive feedback prompts: Feedback requests now appear only once per user.
Worker conditions transparency: Added information about manufacturing and worker conditions for each product.

Bug Fixes
Discounted prices are now correctly displayed in the order overview (previously visible only in details).

Thank you for your continued support and feedback—it's helping us build a better product every day. Log in now to try the new features and let us know what you think!
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


def build_comms_pack_user_prompt(*, fix_version: str, grouped_issues_json: str) -> str:
    return f"""Create a customer communications pack for release {fix_version}.

Input JSON (grouped by category, each item includes key+summary for reference only):
{grouped_issues_json}

Return JSON with this schema:
{{
  "sections": {{
    "new_features": ["string", "..."],
    "improvements": ["string", "..."],
    "bug_fixes": ["string", "..."]
  }},
  "blog_markdown": "string",
  "social": {{
    "x": "string (<= 280 chars)",
    "linkedin": "string",
    "facebook": "string",
    "instagram": "string"
  }}
}}

Constraints:
- The three lists in sections must have EXACTLY the same item counts as the input lists (no drops, no merges).
- blog_markdown must be a full proposed blog post (plain text or minimal markdown) at the following level of detail. Use this example as the target style—do not copy it verbatim; adapt the structure and depth to the Jira issues provided:

{BLOG_POST_DETAIL_EXAMPLE}

- Structure for blog_markdown: (1) "New Feature(s)": for each new feature, a short title and 2–3 sentences explaining what it is and why it matters. (2) "Key Improvements": bullets with a brief label and one sentence of context (e.g. "Clearer post-order flow: More transparency on what happens after you click Accept Order."). (3) "Bug Fixes": one short line per fix. (4) End with a one- or two-sentence closing (e.g. thank you + CTA to try the release).
- Social drafts should not contain keys; keep X within 280 chars; LinkedIn should be professional; Facebook/Instagram can be slightly warmer.
"""

