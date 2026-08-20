"""Activity feed helpers — mirrors Discord notifications into chat UI."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def resolve_chat_target_user_id() -> Optional[str]:
    """Resolve the chat user that receives scheduled specialist messages."""
    explicit_uid = (os.environ.get("BIGAS_EMAIL_SYNC_USER_UID") or "").strip()
    if explicit_uid:
        return explicit_uid

    from bigas.chat.auth import chat_auth_mode
    from bigas.chat.db import get_chat_store

    email = (os.environ.get("BIGAS_EMAIL_SYNC_USER_EMAIL") or "").strip()
    if not email:
        admins = (os.environ.get("CHAT_ADMIN_EMAILS") or "").split(",")
        for item in admins:
            if item.strip():
                email = item.strip()
                break
    if not email and chat_auth_mode() == "dev":
        return "dev-user"

    if not email:
        return None

    user = get_chat_store().find_user_by_email(email)
    return user.get("uid") if user else None


def post_to_agent_thread(
    agent_id: str,
    content: str,
    *,
    role: str = "assistant",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Post a message to a specialist thread for the configured chat user.

    Best-effort: returns None if no chat user exists or persistence fails.
    """
    if not (content or "").strip() or not (agent_id or "").strip():
        return None
    try:
        from bigas.chat.db import get_chat_store

        user_id = resolve_chat_target_user_id()
        if not user_id:
            logger.warning("No chat user found for scheduled %s message", agent_id)
            return None
        store = get_chat_store()
        thread = store.get_or_create_agent_thread(user_id, agent_id)
        thread_id = thread.get("thread_id")
        if not thread_id:
            return None
        meta = {"agent_id": agent_id, **(metadata or {})}
        return store.add_message(
            thread_id,
            role=role,
            content=content.strip(),
            metadata=meta,
        )
    except Exception:
        logger.exception("Failed to post scheduled message to %s chat", agent_id)
        return None


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
