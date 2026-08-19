"""Activity feed helpers — mirrors Discord notifications into chat UI."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def mirror_to_activity_feed(
    message: str,
    *,
    type_: str = "notification",
    source: str = "discord",
) -> None:
    """Write a notification to the activity feed (best-effort)."""
    if not message or not message.strip():
        return
    try:
        from bigas.chat.db import get_chat_store

        store = get_chat_store()
        store.add_activity(type_=type_, content=message.strip(), source=source)
    except Exception as e:
        logger.debug("Activity feed mirror skipped: %s", e)


def mirror_discord_message(webhook_url: Optional[str], message: str, *, channel_hint: str = "") -> None:
    """Mirror a Discord-bound message to the activity feed."""
    source = channel_hint or "discord"
    if webhook_url:
        if "CTO" in webhook_url.upper() or "cto" in (channel_hint or "").lower():
            source = "cto"
        elif "PRODUCT" in webhook_url.upper() or "product" in (channel_hint or "").lower():
            source = "product"
        elif "MARKETING" in webhook_url.upper() or "marketing" in (channel_hint or "").lower():
            source = "marketing"
        elif "QA" in webhook_url.upper():
            source = "qa"
    mirror_to_activity_feed(message, type_="discord", source=source)
