"""Human-friendly chat reply style and raw tool-dump detection."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

# Appended to every chat agent system prompt at runtime.
REPLY_STYLE = """
Reply style (default, always):
- The user never sees tool output. Your reply is a human-friendly summary in their language, not JSON, not a commit list, not a ticket dump.
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
