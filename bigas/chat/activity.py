"""Activity feed helpers — mirrors Discord notifications into chat UI."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def chat_enabled() -> bool:
    return os.environ.get("CHAT_ENABLED", "true").strip().lower() in ("1", "true", "yes")


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


_MARKETING_CHAT_SKIP = frozenset(
    {
        "# 📊 Weekly Analytics Report on its way...",
    }
)

# Prefer explicit chat_agent_id. URL matching uses this order when several
# env vars happen to share the same webhook.
_DISCORD_WEBHOOK_AGENT_ENVS = (
    ("DISCORD_WEBHOOK_URL_MARKETING", "marketing"),
    ("DISCORD_WEBHOOK_URL_PRODUCT", "product"),
    ("DISCORD_WEBHOOK_URL_CTO", "cto"),
    ("DISCORD_WEBHOOK_URL_CFO", "cfo"),
    ("DISCORD_WEBHOOK_URL_DEVOPS", "devops"),
    ("DISCORD_WEBHOOK_URL_CHIEF", "chief"),
    ("DISCORD_WEBHOOK_URL_QA", "cto"),
    ("DISCORD_WEBHOOK_URL", "marketing"),
)

_HINT_TO_AGENT = {
    "pm": "product",
    "product": "product",
    "cto": "cto",
    "cfo": "cfo",
    "marketing": "marketing",
    "devops": "devops",
    "chief": "chief",
    "qa": "cto",
}


def should_skip_discord_chat_mirror(message: str) -> bool:
    """True for empty text or short Discord status pings (e.g. “on its way…”)."""
    text = (message or "").strip()
    if not text:
        return True
    if text in _MARKETING_CHAT_SKIP:
        return True
    return "on its way" in text.lower() and len(text) < 200


def resolve_discord_chat_agent(
    webhook_url: Optional[str] = None,
    *,
    channel_hint: str = "",
    chat_agent_id: Optional[str] = None,
) -> Optional[str]:
    """Map a Discord destination to a specialist thread (product, cto, …)."""
    explicit = (chat_agent_id or "").strip().lower()
    if explicit in _HINT_TO_AGENT:
        return _HINT_TO_AGENT[explicit]
    if explicit:
        return explicit

    hint = (channel_hint or "").strip().lower()
    if hint in _HINT_TO_AGENT:
        return _HINT_TO_AGENT[hint]

    url = (webhook_url or "").strip()
    if url:
        for env_name, agent_id in _DISCORD_WEBHOOK_AGENT_ENVS:
            env_url = (os.environ.get(env_name) or "").strip()
            if env_url and env_url == url:
                return agent_id
        upper = url.upper()
        if "CTO" in upper:
            return "cto"
        if "CFO" in upper:
            return "cfo"
        if "PRODUCT" in upper or "PM" in upper:
            return "product"
        if "MARKETING" in upper:
            return "marketing"
        if "DEVOPS" in upper:
            return "devops"
        if "CHIEF" in upper:
            return "chief"
        if "QA" in upper:
            return "cto"
    return None


def post_marketing_report_to_chat(
    message: str,
    *,
    skip_status_pings: bool = False,
) -> Optional[Dict[str, Any]]:
    """Best-effort post of a marketing Discord report into the Marketing Analyst thread."""
    text = (message or "").strip()
    if not text:
        return None
    if skip_status_pings and should_skip_discord_chat_mirror(text):
        return None
    return post_to_agent_thread("marketing", text, metadata={"source": "marketing_report"})


def post_to_agent_thread(
    agent_id: str,
    content: str,
    *,
    role: str = "assistant",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Post a message to a specialist thread for the configured chat user.

    Best-effort: returns None if chat is disabled, no chat user exists, or persistence fails.
    """
    if not chat_enabled():
        return None
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
        text = content.strip()
        existing = store.list_messages(thread_id)
        if existing:
            last = existing[-1]
            if last.get("role") == role and (last.get("content") or "").strip() == text:
                return last
        meta = {"agent_id": agent_id, **(metadata or {})}
        return store.add_message(
            thread_id,
            role=role,
            content=text,
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


def mirror_discord_message(
    webhook_url: Optional[str],
    message: str,
    *,
    channel_hint: str = "",
    chat_agent_id: Optional[str] = None,
    chat_metadata: Optional[Dict[str, Any]] = None,
    mirror_thread: bool = True,
) -> None:
    """Mirror a Discord-bound message to the activity feed and specialist thread."""
    text = (message or "").strip()
    if not text:
        return
    agent_id = resolve_discord_chat_agent(
        webhook_url,
        channel_hint=channel_hint,
        chat_agent_id=chat_agent_id,
    )
    source = agent_id or channel_hint or "discord"
    if webhook_url and source == "discord":
        if "CTO" in webhook_url.upper():
            source = "cto"
        elif "PRODUCT" in webhook_url.upper():
            source = "product"
        elif "MARKETING" in webhook_url.upper():
            source = "marketing"
        elif "QA" in webhook_url.upper():
            source = "qa"
    mirror_to_activity_feed(text, type_="discord", source=source)
    if not mirror_thread or should_skip_discord_chat_mirror(text):
        return
    if not agent_id:
        return
    meta = {"source": "discord_mirror", **(chat_metadata or {})}
    post_to_agent_thread(agent_id, text, metadata=meta)
