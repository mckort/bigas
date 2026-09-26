"""SMTP outbound email provider (custom SMTP and Gmail App Password)."""
from __future__ import annotations

import logging
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SMTPConfig:
    host: str
    port: int
    security: str  # starttls | ssl
    username: str
    password: str
    sender_email: str
    sender_name: str = ""
    reply_to: Optional[str] = None


class SMTPOutboundProvider:
    """Send plain-text (or HTML) mail via SMTP with TLS/SSL."""

    def __init__(self, config: SMTPConfig) -> None:
        self._config = config

    def test_connection(self) -> None:
        """Verify SMTP handshake and authentication."""
        with self._connect() as smtp:
            smtp.noop()

    def send_email(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        is_html: bool = False,
    ) -> str:
        recipient = (to_email or "").strip()
        if not recipient or "@" not in recipient:
            raise ValueError("Invalid recipient email")

        cfg = self._config
        from_header = cfg.sender_email
        if (cfg.sender_name or "").strip():
            from_header = f"{cfg.sender_name.strip()} <{cfg.sender_email}>"

        msg = EmailMessage()
        msg["From"] = from_header
        msg["To"] = recipient
        msg["Subject"] = (subject or "").strip() or "(no subject)"
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=cfg.sender_email.split("@")[-1] or None)
        if cfg.reply_to:
            msg["Reply-To"] = cfg.reply_to.strip()
        if is_html:
            msg.add_alternative(body, subtype="html")
        else:
            msg.set_content(body)

        message_id = msg["Message-ID"]
        with self._connect() as smtp:
            smtp.send_message(msg)
        logger.info("Sent outbound email to %s via %s:%s", recipient, cfg.host, cfg.port)
        return str(message_id)

    def _connect(self) -> smtplib.SMTP:
        cfg = self._config
        security = (cfg.security or "starttls").strip().lower()
        timeout = 30
        if security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=timeout)
        else:
            smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=timeout)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
        smtp.login(cfg.username, cfg.password)
        return smtp
