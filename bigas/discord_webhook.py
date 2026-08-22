"""Shared Discord webhook helpers for short and long messages."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

DISCORD_HTTP_TIMEOUT = int(os.environ.get("DISCORD_HTTP_TIMEOUT", "10"))


def _mirror_to_chat(
    webhook_url: Optional[str],
    message: str,
    *,
    chat_agent_id: Optional[str] = None,
    chat_metadata: Optional[Dict[str, Any]] = None,
    channel_hint: str = "",
    mirror_thread: bool = True,
) -> None:
    try:
        from bigas.chat.activity import mirror_discord_message

        mirror_discord_message(
            webhook_url,
            message,
            channel_hint=channel_hint,
            chat_agent_id=chat_agent_id,
            chat_metadata=chat_metadata,
            mirror_thread=mirror_thread,
        )
    except Exception:
        logger.debug("Discord chat mirror skipped", exc_info=True)


def _discord_url_ready(webhook_url: Optional[str]) -> bool:
    url = (webhook_url or "").strip()
    return bool(url) and not url.lower().startswith("placeholder")


def post_to_discord(
    webhook_url: Optional[str],
    message: str,
    *,
    chat_agent_id: Optional[str] = None,
    chat_metadata: Optional[Dict[str, Any]] = None,
    mirror_chat: bool = True,
    mirror_thread: bool = True,
    channel_hint: str = "",
) -> bool:
    """
    Post a single message to Discord, truncating hard at 2000 characters.
    Returns True if the request succeeded (HTTP 204), False otherwise.

    When chat is enabled, the same text is mirrored to the matching specialist
    thread (and the activity feed), even if the Discord webhook is unset.
    Set mirror_thread=False to keep the activity-feed card without posting
    into the specialist thread (pipeline status cards).

    Retries briefly on HTTP 429 so multi-part posts from post_long_to_discord
    are less likely to drop continuation chunks.

    NOTE: For longer, multi-part messages use post_long_to_discord instead.
    """
    if mirror_chat and (message or "").strip():
        _mirror_to_chat(
            webhook_url,
            message,
            chat_agent_id=chat_agent_id,
            chat_metadata=chat_metadata,
            channel_hint=channel_hint,
            mirror_thread=mirror_thread,
        )

    if not _discord_url_ready(webhook_url):
        logger.info("Discord webhook URL not provided or is placeholder, skipping Discord notification")
        return False

    if len(message) > 2000:
        message = message[:1997] + "..."
    data = {"content": message}
    for _attempt in range(4):
        try:
            response = requests.post(
                webhook_url,
                json=data,
                timeout=DISCORD_HTTP_TIMEOUT,
            )
            if response.status_code == 204:
                logger.info("Successfully posted to Discord")
                return True
            if response.status_code == 429:
                retry_after = 1.0
                try:
                    retry_after = float(response.headers.get("Retry-After") or "1")
                except ValueError:
                    retry_after = 1.0
                time.sleep(min(max(retry_after, 0.5), 5.0))
                continue
            logger.error("Failed to post to Discord: %s, %s", response.status_code, response.text)
            return False
        except Exception as e:
            logger.error("Error posting to Discord: %s", e)
            return False
    logger.error("Failed to post to Discord after retries (rate limited)")
    return False


def post_long_to_discord(
    webhook_url: Optional[str],
    text: str,
    chunk_size: int = 1900,
    *,
    chat_agent_id: Optional[str] = None,
    chat_metadata: Optional[Dict[str, Any]] = None,
    mirror_chat: bool = True,
    mirror_thread: bool = True,
    channel_hint: str = "",
) -> None:
    """
    Post a long markdown-like text to Discord, splitting it into multiple
    messages that respect Discord's 2000 character limit.

    Splits on newline boundaries where possible to keep sections readable.
    Paces posts slightly to stay under Discord webhook rate limits.
    Chat receives the full text once (not one message per Discord chunk).
    """
    body = text or ""
    if mirror_chat and body.strip():
        _mirror_to_chat(
            webhook_url,
            body,
            chat_agent_id=chat_agent_id,
            chat_metadata=chat_metadata,
            channel_hint=channel_hint,
            mirror_thread=mirror_thread,
        )

    if not _discord_url_ready(webhook_url):
        logger.info("Discord webhook URL not provided or is placeholder, skipping Discord notification")
        return

    lines = body.split("\n")
    parts = []
    current_lines = []
    current_len = 0

    for line in lines:
        # +1 accounts for the newline we'll reinsert
        projected = current_len + len(line) + 1
        if projected > chunk_size and current_lines:
            parts.append("\n".join(current_lines))
            current_lines = [line]
            current_len = len(line) + 1
        else:
            current_lines.append(line)
            current_len += len(line) + 1

    if current_lines:
        parts.append("\n".join(current_lines))

    for i, part in enumerate(parts):
        post_to_discord(webhook_url, part, mirror_chat=False)
        if i + 1 < len(parts):
            time.sleep(0.45)
