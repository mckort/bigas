"""Persistence for per-board outbound email settings, recipients, drafts, and campaigns."""
from __future__ import annotations

import csv
import io
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bigas.providers.email.templates import validate_email_address
from bigas.resources.email.crypto import (
    decrypt_secret,
    encrypt_secret,
    is_masked_password,
    mask_secret,
)
from bigas.tickets.store import get_ticket_store


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def outbound_email_enabled() -> bool:
    return (os.environ.get("ENABLE_OUTBOUND_EMAIL") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


MAX_RECIPIENT_CSV_BYTES = int(os.environ.get("OUTBOUND_MAX_CSV_BYTES", "524288"))
MAX_RECIPIENTS_PER_UPLOAD = int(os.environ.get("OUTBOUND_MAX_RECIPIENTS", "1000"))
CAMPAIGN_STALE_SECONDS = int(os.environ.get("OUTBOUND_CAMPAIGN_STALE_SECONDS", "7200"))
FIRESTORE_BATCH_SIZE = 400


def _resolve_password_enc(existing: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Preserve stored password unless the client submits a new plaintext secret."""
    password_enc = existing.get("password_enc", "")
    if "password" not in payload:
        return password_enc
    password = payload.get("password")
    if password is None:
        return password_enc
    plain = str(password).strip()
    if not plain or is_masked_password(plain):
        return password_enc
    return encrypt_secret(plain)


def _parse_iso_timestamp(value: str) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _normalize_headers(row: Dict[str, str]) -> Dict[str, str]:
    return {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}


def compose_draft(
    existing: Optional[Dict[str, Any]],
    *,
    subject: str,
    body: str,
    purpose: Optional[str] = None,
    tone: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a draft document. Omitted purpose/tone keep the previous values."""
    previous = existing or {}
    if purpose is None:
        stored_purpose = str(previous.get("purpose") or "")
    else:
        stripped_purpose = (purpose or "").strip()
        stored_purpose = (
            stripped_purpose if stripped_purpose else str(previous.get("purpose") or "")
        )
    if tone is None:
        stored_tone = str(previous.get("tone") or "professional").strip() or "professional"
    else:
        stripped_tone = (tone or "").strip()
        stored_tone = (
            stripped_tone
            if stripped_tone
            else str(previous.get("tone") or "professional").strip() or "professional"
        )
    return {
        "subject": (subject or "").strip(),
        "body": body or "",
        "purpose": stored_purpose,
        "tone": stored_tone,
        "updated_at": _utcnow_iso(),
    }


def parse_recipient_csv(text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse CSV with first_name and email columns. Returns (valid_rows, invalid_rows)."""
    valid: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    if not (text or "").strip():
        return valid, [{"row": 0, "error": "Empty file"}]

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return valid, [{"row": 0, "error": "Missing header row"}]

    headers = {h.strip().lower() for h in reader.fieldnames if h}
    if "email" not in headers or "first_name" not in headers:
        return valid, [
            {
                "row": 0,
                "error": "CSV must include columns: first_name, email",
            }
        ]

    for idx, raw in enumerate(reader, start=2):
        row = _normalize_headers(raw)
        first_name = row.get("first_name", "")
        email = row.get("email", "")
        if not first_name:
            invalid.append({"row": idx, "email": email, "error": "first_name is required"})
            continue
        if not validate_email_address(email):
            invalid.append({"row": idx, "email": email, "error": "invalid email"})
            continue
        valid.append(
            {
                "recipient_id": str(uuid.uuid4()),
                "first_name": first_name,
                "email": email.lower(),
                "created_at": _utcnow_iso(),
            }
        )
    return valid, invalid


def _public_email_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not config:
        return {"configured": False}
    out = {k: v for k, v in config.items() if k != "password_enc"}
    out["configured"] = True
    out["password"] = mask_secret("") if config.get("password_enc") else ""
    return out


class MemoryOutboundEmailStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._config: Dict[str, Dict[str, Any]] = {}
        self._recipients: Dict[str, List[Dict[str, Any]]] = {}
        self._drafts: Dict[str, Dict[str, Any]] = {}
        self._campaigns: Dict[str, Dict[str, Any]] = {}

    def get_email_config(self, board_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cfg = self._config.get(board_id)
            return dict(cfg) if cfg else None

    def save_email_config(self, board_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            existing = self._config.get(board_id) or {}
            password_enc = _resolve_password_enc(existing, payload)

            cfg = {
                "provider_type": (payload.get("provider_type") or "smtp").strip(),
                "smtp_host": (payload.get("smtp_host") or "").strip(),
                "smtp_port": int(payload.get("smtp_port") or 587),
                "security": (payload.get("security") or "starttls").strip().lower(),
                "username": (payload.get("username") or "").strip(),
                "password_enc": password_enc,
                "sender_name": (payload.get("sender_name") or "").strip(),
                "sender_email": (payload.get("sender_email") or "").strip(),
                "reply_to": (payload.get("reply_to") or "").strip() or None,
                "updated_at": _utcnow_iso(),
            }
            self._config[board_id] = cfg
            return dict(cfg)

    def list_recipients(self, board_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._recipients.get(board_id, [])]

    def add_recipients(self, board_id: str, rows: List[Dict[str, Any]], *, replace: bool) -> int:
        with self._lock:
            if replace:
                self._recipients[board_id] = []
            bucket = self._recipients.setdefault(board_id, [])
            seen = {r["email"] for r in bucket}
            added = 0
            for row in rows:
                email = row.get("email")
                if email in seen:
                    continue
                bucket.append(dict(row))
                seen.add(email)
                added += 1
            return added

    def get_draft(self, board_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            draft = self._drafts.get(board_id)
            return dict(draft) if draft else None

    def save_draft(
        self,
        board_id: str,
        *,
        subject: str,
        body: str,
        purpose: Optional[str] = None,
        tone: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            draft = compose_draft(
                self._drafts.get(board_id),
                subject=subject,
                body=body,
                purpose=purpose,
                tone=tone,
            )
            self._drafts[board_id] = draft
            return dict(draft)

    def create_campaign(
        self,
        board_id: str,
        *,
        subject_template: str,
        body_template: str,
        recipients: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        campaign_id = str(uuid.uuid4())
        now = _utcnow_iso()
        entries = [
            {
                "recipient_id": r.get("recipient_id"),
                "email": r.get("email"),
                "first_name": r.get("first_name"),
                "status": "pending",
                "sent_at": None,
                "error": None,
            }
            for r in recipients
        ]
        campaign = {
            "campaign_id": campaign_id,
            "board_id": board_id,
            "subject_template": subject_template,
            "body_template": body_template,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "recipients": entries,
        }
        with self._lock:
            self._campaigns[campaign_id] = campaign
        return dict(campaign)

    def _maybe_mark_stale_campaign(self, campaign: Dict[str, Any]) -> Dict[str, Any]:
        if campaign.get("status") != "in_progress":
            return campaign
        updated_at = _parse_iso_timestamp(str(campaign.get("updated_at") or ""))
        if not updated_at:
            return campaign
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age <= CAMPAIGN_STALE_SECONDS:
            return campaign
        campaign_id = str(campaign.get("campaign_id") or "")
        if not campaign_id:
            return campaign
        failed = self.update_campaign(
            campaign_id,
            status="failed",
            error=(
                "Campaign timed out or was interrupted before completion. "
                "Please try sending again."
            ),
        )
        return failed or campaign

    def get_campaign(
        self, campaign_id: str, board_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        del board_id
        with self._lock:
            camp = self._campaigns.get(campaign_id)
        if not camp:
            return None
        return self._maybe_mark_stale_campaign(dict(camp))

    def update_campaign(self, campaign_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            camp = self._campaigns.get(campaign_id)
            if not camp:
                return None
            camp.update(fields)
            camp["updated_at"] = _utcnow_iso()
            return dict(camp)

    def update_campaign_recipient(
        self,
        campaign_id: str,
        recipient_id: str,
        *,
        status: str,
        error: Optional[str] = None,
        sent_at: Optional[str] = None,
    ) -> None:
        with self._lock:
            camp = self._campaigns.get(campaign_id)
            if not camp:
                return
            for entry in camp.get("recipients") or []:
                if entry.get("recipient_id") == recipient_id:
                    entry["status"] = status
                    entry["error"] = error
                    entry["sent_at"] = sent_at
                    break
            camp["updated_at"] = _utcnow_iso()


class FirestoreOutboundEmailStore(MemoryOutboundEmailStore):
    """Firestore-backed store (boards/{id}/outbound_email/*)."""

    def __init__(self, project_id: str) -> None:
        super().__init__()
        from google.cloud import firestore

        self._db = firestore.Client(project=project_id)
        self._boards = self._db.collection("boards")

    def _board_ref(self, board_id: str):
        return self._boards.document(board_id)

    def get_email_config(self, board_id: str) -> Optional[Dict[str, Any]]:
        snap = self._board_ref(board_id).collection("outbound_email").document("settings").get()
        return snap.to_dict() if snap.exists else None

    def save_email_config(self, board_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        existing = self.get_email_config(board_id) or {}
        password_enc = _resolve_password_enc(existing, payload)
        cfg = {
            "provider_type": (payload.get("provider_type") or "smtp").strip(),
            "smtp_host": (payload.get("smtp_host") or "").strip(),
            "smtp_port": int(payload.get("smtp_port") or 587),
            "security": (payload.get("security") or "starttls").strip().lower(),
            "username": (payload.get("username") or "").strip(),
            "password_enc": password_enc,
            "sender_name": (payload.get("sender_name") or "").strip(),
            "sender_email": (payload.get("sender_email") or "").strip(),
            "reply_to": (payload.get("reply_to") or "").strip() or None,
            "updated_at": _utcnow_iso(),
        }
        self._board_ref(board_id).collection("outbound_email").document("settings").set(cfg)
        return cfg

    def list_recipients(self, board_id: str) -> List[Dict[str, Any]]:
        docs = self._board_ref(board_id).collection("outbound_recipients").stream()
        return [doc.to_dict() for doc in docs if doc.exists]

    def add_recipients(self, board_id: str, rows: List[Dict[str, Any]], *, replace: bool) -> int:
        col = self._board_ref(board_id).collection("outbound_recipients")
        if replace:
            batch = self._db.batch()
            pending = 0
            for doc in col.stream():
                batch.delete(doc.reference)
                pending += 1
                if pending >= FIRESTORE_BATCH_SIZE:
                    batch.commit()
                    batch = self._db.batch()
                    pending = 0
            if pending:
                batch.commit()
        existing = set() if replace else {r.get("email") for r in self.list_recipients(board_id)}
        added = 0
        batch = self._db.batch()
        pending = 0
        for row in rows:
            email = row.get("email")
            if email in existing:
                continue
            batch.set(col.document(row["recipient_id"]), row)
            existing.add(email)
            added += 1
            pending += 1
            if pending >= FIRESTORE_BATCH_SIZE:
                batch.commit()
                batch = self._db.batch()
                pending = 0
        if pending:
            batch.commit()
        return added

    def get_draft(self, board_id: str) -> Optional[Dict[str, Any]]:
        snap = self._board_ref(board_id).collection("outbound_email").document("draft").get()
        return snap.to_dict() if snap.exists else None

    def save_draft(
        self,
        board_id: str,
        *,
        subject: str,
        body: str,
        purpose: Optional[str] = None,
        tone: Optional[str] = None,
    ) -> Dict[str, Any]:
        draft = compose_draft(
            self.get_draft(board_id),
            subject=subject,
            body=body,
            purpose=purpose,
            tone=tone,
        )
        self._board_ref(board_id).collection("outbound_email").document("draft").set(draft)
        return draft

    def create_campaign(
        self,
        board_id: str,
        *,
        subject_template: str,
        body_template: str,
        recipients: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        campaign_id = str(uuid.uuid4())
        now = _utcnow_iso()
        entries = [
            {
                "recipient_id": r.get("recipient_id"),
                "email": r.get("email"),
                "first_name": r.get("first_name"),
                "status": "pending",
                "sent_at": None,
                "error": None,
            }
            for r in recipients
        ]
        campaign = {
            "campaign_id": campaign_id,
            "board_id": board_id,
            "subject_template": subject_template,
            "body_template": body_template,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "recipients": entries,
        }
        self._board_ref(board_id).collection("outbound_campaigns").document(campaign_id).set(campaign)
        with self._lock:
            self._campaigns[campaign_id] = campaign
        return campaign

    def get_campaign(
        self, campaign_id: str, board_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        camp: Optional[Dict[str, Any]] = None
        if board_id:
            snap = (
                self._board_ref(board_id)
                .collection("outbound_campaigns")
                .document(campaign_id)
                .get()
            )
            if snap.exists:
                camp = snap.to_dict()
                with self._lock:
                    self._campaigns[campaign_id] = camp
        if not camp:
            with self._lock:
                cached = self._campaigns.get(campaign_id)
            if cached:
                camp = dict(cached)
        if not camp:
            return None
        return self._maybe_mark_stale_campaign(dict(camp))

    def update_campaign_recipient(
        self,
        campaign_id: str,
        recipient_id: str,
        *,
        status: str,
        error: Optional[str] = None,
        sent_at: Optional[str] = None,
    ) -> None:
        super().update_campaign_recipient(
            campaign_id,
            recipient_id,
            status=status,
            error=error,
            sent_at=sent_at,
        )
        with self._lock:
            camp = self._campaigns.get(campaign_id)
        if not camp or not camp.get("board_id"):
            return
        self._board_ref(camp["board_id"]).collection("outbound_campaigns").document(
            campaign_id
        ).set(camp)

    def update_campaign(self, campaign_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        updated = super().update_campaign(campaign_id, **fields)
        if not updated:
            return None
        board_id = updated.get("board_id")
        if board_id:
            self._board_ref(board_id).collection("outbound_campaigns").document(campaign_id).set(
                updated
            )
        return updated


_store: Optional[Any] = None
_store_lock = threading.Lock()


def get_outbound_email_store():
    global _store
    with _store_lock:
        if _store is not None:
            return _store
        storage_mode = (os.environ.get("CHAT_STORAGE_MODE") or "").strip().lower()
        project_id = (
            os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_PROJECT_ID") or ""
        ).strip()
        if storage_mode == "memory" or (storage_mode != "firestore" and not project_id):
            _store = MemoryOutboundEmailStore()
        else:
            try:
                _store = FirestoreOutboundEmailStore(project_id)
            except Exception:
                _store = MemoryOutboundEmailStore()
        return _store


def assert_board_owner(board_id: str, user_id: str) -> Dict[str, Any]:
    board = get_ticket_store().get_board(board_id)
    if not board:
        raise PermissionError("Board not found")
    if board.get("user_id") != user_id:
        raise PermissionError("Board not found")
    return board


def config_for_provider(config: Dict[str, Any]):
    from bigas.providers.email.smtp_provider import SMTPConfig, SMTPOutboundProvider

    password = decrypt_secret(config.get("password_enc") or "")
    if not password:
        raise RuntimeError("SMTP password is not configured for this board")
    return SMTPOutboundProvider(
        SMTPConfig(
            host=config.get("smtp_host") or "smtp.gmail.com",
            port=int(config.get("smtp_port") or 587),
            security=config.get("security") or "starttls",
            username=config.get("username") or "",
            password=password,
            sender_email=config.get("sender_email") or config.get("username") or "",
            sender_name=config.get("sender_name") or "",
            reply_to=config.get("reply_to"),
        )
    )


def public_email_settings(board_id: str) -> Dict[str, Any]:
    cfg = get_outbound_email_store().get_email_config(board_id)
    return _public_email_config(cfg)
