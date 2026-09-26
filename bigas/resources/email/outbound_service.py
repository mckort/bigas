"""Business logic for board outbound email campaigns."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from bigas.llm.factory import get_llm_client
from bigas.providers.email.templates import render_personalized_template
from bigas.resources.email.outbound_store import config_for_provider, get_outbound_email_store

logger = logging.getLogger(__name__)

DEFAULT_SEND_DELAY_SECONDS = float(os.environ.get("OUTBOUND_EMAIL_SEND_DELAY_SECONDS") or "1.5")


def generate_email_draft(
    *,
    prompt: str,
    tone: str = "professional",
    goal: str = "",
) -> Dict[str, str]:
    llm, _model = get_llm_client(feature="marketing")
    system = (
        "You write concise plain-text outreach emails for founders. "
        "Always personalize with the placeholder {{first_name}} in subject and body "
        "(at least once in the greeting). Output JSON only: "
        '{"subject":"...","body":"..."}'
    )
    user = f"Goal: {goal or 'outreach'}\nTone: {tone}\nPrompt: {prompt}"
    raw = llm.complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.5,
        max_tokens=2048,
    )
    text = (raw or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"subject": "Quick note for you, {{first_name}}", "body": text}
    subject = str(data.get("subject") or "").strip() or "Hello {{first_name}}"
    body = str(data.get("body") or "").strip() or "Hi {{first_name}},\n\n"
    if "{{first_name}}" not in subject and "{first_name}" not in subject:
        subject = f"{subject} — {{first_name}}"
    if "{{first_name}}" not in body and "{first_name}" not in body:
        body = f"Hi {{first_name}},\n\n{body}"
    return {"subject": subject, "body": body}


def preview_message(
    *,
    subject_template: str,
    body_template: str,
    recipient: Dict[str, Any],
) -> Dict[str, str]:
    return {
        "subject": render_personalized_template(subject_template, recipient),
        "body": render_personalized_template(body_template, recipient),
    }


def _run_campaign_send(
    board_id: str,
    campaign_id: str,
    *,
    delay_seconds: float = DEFAULT_SEND_DELAY_SECONDS,
) -> None:
    store = get_outbound_email_store()
    campaign = store.get_campaign(campaign_id, board_id=board_id)
    if not campaign or campaign.get("board_id") != board_id:
        logger.error("Campaign %s not found for board %s", campaign_id, board_id)
        return

    config = store.get_email_config(board_id)
    if not config:
        store.update_campaign(campaign_id, status="failed", error="Email not configured")
        return

    try:
        provider = config_for_provider(config)
    except Exception as exc:
        store.update_campaign(campaign_id, status="failed", error=str(exc))
        return

    store.update_campaign(campaign_id, status="in_progress", error=None)
    subject_template = campaign.get("subject_template") or ""
    body_template = campaign.get("body_template") or ""

    for entry in campaign.get("recipients") or []:
        if entry.get("status") == "sent":
            continue
        recipient = {
            "first_name": entry.get("first_name"),
            "email": entry.get("email"),
        }
        subject = render_personalized_template(subject_template, recipient)
        body = render_personalized_template(body_template, recipient)
        try:
            provider.send_email(to_email=entry.get("email") or "", subject=subject, body=body)
            store.update_campaign_recipient(
                campaign_id,
                entry.get("recipient_id") or "",
                status="sent",
                error=None,
                sent_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        except Exception as exc:
            logger.exception("Failed sending to %s", entry.get("email"))
            store.update_campaign_recipient(
                campaign_id,
                entry.get("recipient_id") or "",
                status="failed",
                error=str(exc),
            )
        time.sleep(max(0.0, delay_seconds))

    refreshed = store.get_campaign(campaign_id, board_id=board_id) or campaign
    statuses = [r.get("status") for r in refreshed.get("recipients") or []]
    if statuses and all(s == "sent" for s in statuses):
        final = "completed"
    elif any(s == "sent" for s in statuses):
        final = "completed_with_errors"
    else:
        final = "failed"
    store.update_campaign(campaign_id, status=final)


def start_campaign_send(board_id: str, campaign_id: str) -> None:
    thread = threading.Thread(
        target=_run_campaign_send,
        args=(board_id, campaign_id),
        daemon=True,
        name=f"outbound-email-{campaign_id[:8]}",
    )
    thread.start()


def campaign_summary(campaign: Dict[str, Any]) -> Dict[str, Any]:
    recipients = campaign.get("recipients") or []
    sent = sum(1 for r in recipients if r.get("status") == "sent")
    failed = sum(1 for r in recipients if r.get("status") == "failed")
    pending = sum(1 for r in recipients if r.get("status") == "pending")
    return {
        **campaign,
        "counts": {"sent": sent, "failed": failed, "pending": pending, "total": len(recipients)},
    }


def resolve_recipients_for_send(
    board_id: str,
    recipient_ids: Optional[List[str]],
    *,
    select_all: bool,
) -> List[Dict[str, Any]]:
    store = get_outbound_email_store()
    all_rows = store.list_recipients(board_id)
    if select_all:
        return all_rows
    wanted = {str(i).strip() for i in (recipient_ids or []) if str(i).strip()}
    return [r for r in all_rows if r.get("recipient_id") in wanted]
