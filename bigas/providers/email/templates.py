"""Personalization helpers for outbound email templates."""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping

_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*first_name\s*\}\}|\{\s*first_name\s*\}",
    re.IGNORECASE,
)


def render_personalized_template(template: str, recipient: Mapping[str, Any]) -> str:
    """Replace {first_name} / {{first_name}} with the recipient first name."""
    first = str(recipient.get("first_name") or "").strip()

    def _sub(_match: re.Match[str]) -> str:
        return first

    return _PLACEHOLDER_RE.sub(_sub, template or "")


def validate_email_address(value: str) -> bool:
    text = (value or "").strip()
    if not text or "@" not in text or " " in text:
        return False
    local, _, domain = text.partition("@")
    return bool(local and domain and "." in domain)
