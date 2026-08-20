"""Outbound SMTP replies for Chief of Staff email triage."""
from __future__ import annotations

import logging
import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Optional

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)


def extract_email_address(raw: str) -> str:
    """Pull a single RFC-ish address out of a From/Reply-To header."""
    _, addr = parseaddr(raw or "")
    addr = (addr or "").strip()
    if addr and "@" in addr and " " not in addr:
        return addr
    match = _EMAIL_RE.search(raw or "")
    return match.group(0) if match else ""


def _reply_subject(subject: str) -> str:
    text = (subject or "").strip() or "(no subject)"
    if text.lower().startswith("re:"):
        return text
    return f"Re: {text}"


def send_outbound_reply(
    *,
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: str = "",
    from_addr: Optional[str] = None,
) -> None:
    """Send a plain-text reply via SMTP (Migadu defaults). Uses IMAP mailbox creds."""
    username = (os.environ.get("BIGAS_EMAIL_USERNAME") or "").strip()
    password = (os.environ.get("BIGAS_EMAIL_PASSWORD") or "").strip()
    if not username or not password:
        raise RuntimeError("SMTP not configured (set BIGAS_EMAIL_USERNAME and BIGAS_EMAIL_PASSWORD)")

    recipient = extract_email_address(to_addr) or (to_addr or "").strip()
    if not recipient or "@" not in recipient:
        raise RuntimeError("Cannot send reply: no valid recipient address")

    sender = (from_addr or username).strip()
    server = (os.environ.get("BIGAS_EMAIL_SMTP_SERVER") or "smtp.migadu.com").strip()
    port = int((os.environ.get("BIGAS_EMAIL_SMTP_PORT") or "465").strip())

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = _reply_subject(subject)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)

    logger.info("Sending email reply from %s to %s via %s:%s", sender, recipient, server, port)
    with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
        smtp.login(username, password)
        smtp.send_message(msg)
