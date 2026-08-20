"""IMAP email provider for Migadu and other standard mail hosts."""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from bigas.providers.email.base import EmailProvider, InboundEmail
from bigas.providers.email.outbound import extract_email_address

logger = logging.getLogger(__name__)


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out: List[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            out.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return " ".join(out).strip()


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_body(msg: email.message.Message) -> str:
    plain_parts: List[str] = []
    html_parts: List[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = (part.get_content_type() or "").lower()
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            if content_type == "text/plain":
                plain_parts.append(decoded.strip())
            elif content_type == "text/html":
                html_parts.append(decoded)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            if (msg.get_content_type() or "").lower() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded.strip())

    if plain_parts:
        return "\n\n".join(p for p in plain_parts if p)
    if html_parts:
        return "\n\n".join(_html_to_text(h) for h in html_parts if h)
    return ""


def truncate_body(text: str, max_chars: Optional[int] = None) -> str:
    limit = max_chars
    if limit is None:
        raw = (os.environ.get("BIGAS_EMAIL_MAX_BODY_CHARS") or "8000").strip()
        try:
            limit = int(raw)
        except ValueError:
            limit = 8000
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 40)].rstrip() + "\n\n[… truncated for length …]"


class ImapEmailProvider(EmailProvider):
    name = "imap"
    display_name = "IMAP (Migadu)"

    @classmethod
    def is_configured(cls) -> bool:
        return all(
            os.getenv(k)
            for k in (
                "BIGAS_EMAIL_IMAP_SERVER",
                "BIGAS_EMAIL_USERNAME",
                "BIGAS_EMAIL_PASSWORD",
            )
        )

    def __init__(self) -> None:
        self._server = (os.environ.get("BIGAS_EMAIL_IMAP_SERVER") or "").strip()
        self._port = int((os.environ.get("BIGAS_EMAIL_IMAP_PORT") or "993").strip())
        self._username = (os.environ.get("BIGAS_EMAIL_USERNAME") or "").strip()
        self._password = (os.environ.get("BIGAS_EMAIL_PASSWORD") or "").strip()
        self._mailbox = (os.environ.get("BIGAS_EMAIL_IMAP_MAILBOX") or "INBOX").strip()
        self._processed_folder = (os.environ.get("BIGAS_EMAIL_PROCESSED_FOLDER") or "").strip()

    def _connect(self) -> imaplib.IMAP4_SSL:
        client = imaplib.IMAP4_SSL(self._server, self._port)
        client.login(self._username, self._password)
        return client

    def fetch_unread(self) -> List[InboundEmail]:
        messages: List[InboundEmail] = []
        client = self._connect()
        try:
            status, _ = client.select(self._mailbox, readonly=False)
            if status != "OK":
                raise RuntimeError(f"IMAP select failed for mailbox {self._mailbox!r}")

            status, data = client.search(None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []

            uids = data[0].split()
            for uid_bytes in uids:
                uid = uid_bytes.decode("ascii", errors="replace")
                status, msg_data = client.fetch(uid_bytes, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                msg = email.message_from_bytes(raw)
                message_id = (msg.get("Message-ID") or f"uid-{uid}").strip()
                sender = _decode_header_value(msg.get("From") or "")
                reply_raw = _decode_header_value(msg.get("Reply-To") or "") or sender
                reply_to = extract_email_address(reply_raw) or extract_email_address(sender)
                subject = _decode_header_value(msg.get("Subject") or "(no subject)")
                body = truncate_body(_extract_body(msg))
                received_at: Optional[str] = None
                date_hdr = msg.get("Date")
                if date_hdr:
                    try:
                        received_at = parsedate_to_datetime(date_hdr).isoformat()
                    except (TypeError, ValueError, OverflowError):
                        received_at = None

                messages.append(
                    InboundEmail(
                        message_id=message_id,
                        uid=uid,
                        sender=sender,
                        subject=subject,
                        body_text=body,
                        received_at=received_at,
                        reply_to=reply_to or None,
                    )
                )

            return messages
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def mark_processed(self, uid: str) -> None:
        uid_bytes = uid.encode("ascii", errors="replace")
        client = self._connect()
        try:
            status, _ = client.select(self._mailbox, readonly=False)
            if status != "OK":
                raise RuntimeError(f"IMAP select failed for mailbox {self._mailbox!r}")

            if self._processed_folder:
                self._move_to_processed(client, uid_bytes)
            else:
                client.store(uid_bytes, "+FLAGS", "\\Seen")
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def _move_to_processed(self, client: imaplib.IMAP4_SSL, uid_bytes: bytes) -> None:
        folder = self._processed_folder
        try:
            client.create(folder)
        except imaplib.IMAP4.error:
            pass
        status, _ = client.copy(uid_bytes, folder)
        if status != "OK":
            logger.error("IMAP copy to %r failed for uid %s", folder, uid_bytes.decode("ascii", errors="replace"))
            return
        client.store(uid_bytes, "+FLAGS", "\\Seen \\Deleted")
        client.expunge()
