"""Preserve human Brief; replace Bigas AI sections in issue descriptions."""

from __future__ import annotations

import re
from typing import Optional, Tuple

BRIEF_HEADING = "## Brief"
RESEARCH_HEADING = "## AI Research (Bigas)"
PLAN_HEADING = "## AI Plan (Bigas)"

_SECTION_HEADINGS = (BRIEF_HEADING, RESEARCH_HEADING, PLAN_HEADING)


def _normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _find_section_spans(text: str) -> dict[str, Tuple[int, int]]:
    """
    Map heading -> (start_of_heading, start_of_next_known_heading_or_eof).
    Matching is case-insensitive on the heading line.
    """
    normalized = _normalize_newlines(text)
    if not normalized:
        return {}

    pattern = re.compile(
        r"^(##\s+(?:Brief|AI Research \(Bigas\)|AI Plan \(Bigas\)))\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(pattern.finditer(normalized))
    spans: dict[str, Tuple[int, int]] = {}
    heading_map = {
        "brief": BRIEF_HEADING,
        "ai research (bigas)": RESEARCH_HEADING,
        "ai plan (bigas)": PLAN_HEADING,
    }
    for i, m in enumerate(matches):
        label = re.sub(r"^##\s+", "", m.group(1), flags=re.IGNORECASE).strip().lower()
        canonical = heading_map.get(label)
        if not canonical:
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(normalized)
        spans[canonical] = (start, end)
    return spans


def extract_brief(description: str) -> str:
    """
    Return the human Brief body (without heading).
    If no Brief heading exists, treat all content before the first AI section as the brief.
    """
    text = _normalize_newlines(description)
    if not text:
        return ""
    spans = _find_section_spans(text)
    if BRIEF_HEADING in spans:
        start, end = spans[BRIEF_HEADING]
        # skip heading line
        body = text[start:end]
        body = re.sub(r"^##\s+Brief\s*\n?", "", body, count=1, flags=re.IGNORECASE).strip()
        return body

    # No Brief heading: everything before first AI section
    first_ai = len(text)
    for h in (RESEARCH_HEADING, PLAN_HEADING):
        if h in spans:
            first_ai = min(first_ai, spans[h][0])
    return text[:first_ai].strip()


def extract_section(description: str, heading: str) -> str:
    text = _normalize_newlines(description)
    spans = _find_section_spans(text)
    if heading not in spans:
        return ""
    start, end = spans[heading]
    body = text[start:end]
    body = re.sub(
        rf"^{re.escape(heading)}\s*\n?",
        "",
        body,
        count=1,
        flags=re.IGNORECASE,
    ).strip()
    return body


def upsert_research_section(
    description: str,
    *,
    research_markdown: str,
    brief_fallback: Optional[str] = None,
) -> str:
    """
    Rebuild description with Brief preserved and AI Research replaced.
    Keeps any existing AI Plan section.
    """
    text = _normalize_newlines(description)
    brief = extract_brief(text)
    if not brief and brief_fallback:
        brief = brief_fallback.strip()
    if not brief:
        brief = "(No brief provided — please add a short human summary above.)"

    plan = extract_section(text, PLAN_HEADING)
    research = (research_markdown or "").strip()

    parts = [
        BRIEF_HEADING,
        brief,
        "",
        RESEARCH_HEADING,
        research,
    ]
    if plan:
        parts.extend(["", PLAN_HEADING, plan])
    return "\n".join(parts).strip() + "\n"


def upsert_plan_section(
    description: str,
    *,
    plan_markdown: str,
    brief_fallback: Optional[str] = None,
) -> str:
    """
    Rebuild description with Brief + AI Research preserved and AI Plan replaced.
    """
    text = _normalize_newlines(description)
    brief = extract_brief(text)
    if not brief and brief_fallback:
        brief = brief_fallback.strip()
    if not brief:
        brief = "(No brief provided — please add a short human summary above.)"

    research = extract_section(text, RESEARCH_HEADING)
    if not research:
        research = (
            "(No AI Research section yet — consider running Research and describe first.)"
        )
    plan = (plan_markdown or "").strip()

    parts = [
        BRIEF_HEADING,
        brief,
        "",
        RESEARCH_HEADING,
        research,
        "",
        PLAN_HEADING,
        plan,
    ]
    return "\n".join(parts).strip() + "\n"
