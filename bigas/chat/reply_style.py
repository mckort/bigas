"""Human-friendly chat reply style and raw tool-dump detection."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

# Appended to every chat agent system prompt at runtime.
REPLY_STYLE = """
Reply style (default, always):
- The user never sees tool output. Your reply is a human-friendly summary in their language, not JSON, not a commit list, not a ticket dump.
- Read the user's question and answer it. Tools give facts; you interpret them.
- Open with one short sentence that answers the question.
- Group the rest into scannable sections. Use an emoji + bold category header, then bold sub-heads and 1–2 sentence bullets that explain user value (what changed and why it matters). Skip autofix, infra, and internal noise unless asked.
- Prefer markdown: short paragraphs, bullets, bold key terms, clickable links. Never wrap the whole reply in a code fence.
- Only use a code block if the user asked for raw data, a payload, or a command to copy.
""".strip()

_TOOL_DUMP_KEYS = frozenset(
    {
        "autofix_commits",
        "committed_at",
        "commits",
        "epics",
        "html_url",
        "is_error",
        "issues",
        "merged_at",
        "project_key",
        "pull_requests",
        "repo",
        "sha",
        "since",
        "structured",
    }
)

_FENCE_RE = re.compile(r"^```(?:json|javascript|js)?\s*", re.I)
_DUMP_KEY_HINT_RE = re.compile(
    r'"(commits|pull_requests|issues|ok|sha|html_url|repo)"\s*:',
)


_LINK_ONLY_RE = re.compile(r"^\[.+\]\([^)]+\)$")
_LINK_WITH_STATUS_RE = re.compile(r"^\[.+\]\([^)]+\)\s+—\s+.+")
_BULLET_LINK_LINE_RE = re.compile(r"^-\s+\[.+\]\([^)]+\)(?:\s+—\s+.+)?$")
_PARENT_LINE_RE = re.compile(r"^Parent \([^)]+\):\s+")


def _is_ticket_dump_line(stripped: str) -> bool:
    """True when a line is only ticket metadata from lookup humanization."""
    if not stripped:
        return True
    if stripped.startswith("[Move to next"):
        return True
    lower = stripped.lower()
    if lower.startswith(("status:", "agent:", "pr:", "missing:")):
        return True
    if stripped == "Open Epics:" or lower.startswith("open epics:"):
        return True
    if _PARENT_LINE_RE.match(stripped):
        return True
    if _LINK_ONLY_RE.match(stripped):
        return True
    if _LINK_WITH_STATUS_RE.match(stripped):
        return True
    if _BULLET_LINK_LINE_RE.match(stripped):
        return True
    return False


def looks_like_ticket_dump(text: Optional[str]) -> bool:
    """True when a reply is only a ticket title, status, and/or Move button."""
    blob = text.strip() if isinstance(text, str) else str(text or "").strip()
    if not blob:
        return False
    has_button = "bigas://action/jira_transition" in blob or "Move to next column" in blob
    has_ticket_link = "/board?ticket=" in blob or "atlassian.net/browse/" in blob
    if not has_button and not has_ticket_link:
        return False
    prose: list[str] = []
    for line in blob.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _is_ticket_dump_line(stripped):
            continue
        prose.append(stripped)
    return not prose


def looks_like_incomplete_chat_reply(text: Optional[str]) -> bool:
    """True when the user would see a tool dump instead of an answer."""
    return looks_like_raw_tool_dump(text) or looks_like_ticket_dump(text)


def looks_like_raw_tool_dump(text: Optional[str]) -> bool:
    """True when a chat reply is (or is dominated by) raw tool JSON."""
    blob = text.strip() if isinstance(text, str) else str(text or "").strip()
    if not blob:
        return False
    if blob.startswith("```"):
        blob = _FENCE_RE.sub("", blob, count=1)
        blob = re.sub(r"\s*```$", "", blob).strip()
    start = _json_start(blob)
    if start is None:
        return False
    prefix = blob[:start].strip()
    json_blob = blob[start:]
    if prefix and len(prefix) > 80:
        return False
    try:
        parsed = json.loads(json_blob)
    except json.JSONDecodeError:
        return start == 0 and bool(_DUMP_KEY_HINT_RE.search(json_blob[:2000]))
    if isinstance(parsed, list):
        return True
    if not isinstance(parsed, dict):
        return False
    keys = set(parsed.keys())
    if keys & _TOOL_DUMP_KEYS:
        return True
    if keys <= {"answer", "text", "message", "summary", "content", "report", "error"}:
        return False
    dumped = json.dumps(parsed, ensure_ascii=False)
    return len(dumped) > 200


def _json_start(blob: str) -> Optional[int]:
    for index, char in enumerate(blob):
        if char in "{[":
            return index
    return None


def latest_user_text(messages: Any) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def tool_facts_from_messages(messages: Any) -> str:
    chunks = []
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        name = str(message.get("name") or "tool").strip() or "tool"
        chunks.append(f"### {name}\n{content.strip()}")
    return "\n\n".join(chunks)
