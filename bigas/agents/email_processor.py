"""Chief of Staff email triage — summarize inbox messages and propose actions."""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from bigas.agents.chief_of_staff import _parse_json_action, run_specialist_task
from bigas.chat.db import get_chat_store
from bigas.llm.factory import get_llm_client
from bigas.portfolio import prompt_block
from bigas.providers.email.base import InboundEmail
from bigas.providers.email.outbound import extract_email_address, send_outbound_reply

logger = logging.getLogger(__name__)

VALID_KINDS = {"delegate", "tool", "draft_reply"}


def _email_system_prompt() -> str:
    portfolio = ""
    try:
        portfolio = prompt_block()
    except Exception:
        logger.exception("Failed to load portfolio block for email triage")
    return (
        "You are the Chief of Staff for Bigas. You triage incoming emails for the human operator.\n"
        "Do NOT take autonomous actions — only propose actions the human can approve.\n"
        "The human will see the original email body verbatim; do not rewrite it.\n"
        "Silently discard obvious spam or marketing noise (set is_spam=true and leave proposals empty).\n"
        "Propose 1–3 concrete next steps when applicable (e.g. delegate to a specialist, call a tool, "
        "or draft a reply). Never send email yourself — use draft_reply so the human can edit and send.\n\n"
        f"{portfolio}\n\n"
        "Respond with ONLY a JSON object:\n"
        "{\n"
        '  "is_spam": false,\n'
        '  "proposals": [\n'
        '    {"id": "unique_id", "label": "Short button label", "kind": "delegate|tool|draft_reply", '
        '"params": {...}}\n'
        "  ]\n"
        "}\n"
        "For delegate: params must include agent_id (marketing|product|cto|devops) and task.\n"
        "For tool: params must include tool_name and arguments object.\n"
        "For draft_reply: params must include text (the suggested reply body). Label it Send.\n"
    )


def _normalize_proposals(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in VALID_KINDS:
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        label = str(item.get("label") or f"Action {idx + 1}").strip()
        action_id = str(item.get("id") or f"action_{idx + 1}").strip()
        out.append({"id": action_id, "label": label, "kind": kind, "params": params})
    return out


def analyze_email(email_msg: InboundEmail) -> Optional[Dict[str, Any]]:
    """
    Analyze one email with the COS LLM (spam filter + action proposals).
    Returns dict with proposals, is_spam — or None if LLM unavailable.
    """
    llm, _ = get_llm_client(feature="chat")
    user_content = (
        f"From: {email_msg.sender}\n"
        f"Subject: {email_msg.subject}\n"
        f"Message-ID: {email_msg.message_id}\n\n"
        f"{email_msg.body_text}"
    )
    messages = [
        {"role": "system", "content": _email_system_prompt()},
        {"role": "user", "content": user_content},
    ]
    try:
        raw = llm.complete(messages, temperature=0.2)
    except Exception:
        logger.exception("Email triage LLM call failed")
        return None

    parsed = _parse_json_action(raw)
    if not parsed:
        return {"is_spam": False, "proposals": []}

    if parsed.get("is_spam"):
        return {"is_spam": True, "proposals": []}

    return {
        "is_spam": False,
        "proposals": _normalize_proposals(parsed.get("proposals")),
    }


def format_email_for_chat(email_msg: InboundEmail) -> str:
    """Literal From/Subject/body for the chat bubble (no LLM rewrite)."""
    body = (email_msg.body_text or "").strip() or "(empty body)"
    return (
        f"📬 **Email triage** — {email_msg.subject}\n\n"
        f"**From:** {email_msg.sender}\n\n"
        f"**Subject:** {email_msg.subject}\n\n"
        f"{body}"
    )


def post_email_to_chief_thread(
    thread_id: str,
    email_msg: InboundEmail,
    analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """Post the original email and optional action proposals to the chief chat thread."""
    store = get_chat_store()
    proposals = analysis.get("proposals") or []
    proposal_id = str(uuid.uuid4())
    content = format_email_for_chat(email_msg)
    reply_to = (email_msg.reply_to or "").strip() or extract_email_address(email_msg.sender)

    metadata: Dict[str, Any] = {
        "agent_id": "chief",
        "source": "email",
        "source_id": email_msg.message_id,
        "email_from": email_msg.sender,
        "email_reply_to": reply_to,
        "email_subject": email_msg.subject,
        "email_body": email_msg.body_text or "",
    }

    if proposals:
        metadata.update(
            {
                "type": "action_proposal",
                "proposal_id": proposal_id,
                "status": "pending",
                "actions": proposals,
            }
        )

    message = store.add_message(
        thread_id,
        role="assistant",
        content=content,
        metadata=metadata,
    )
    store.add_activity(
        type_="email",
        content=f"Email from {email_msg.sender}: {email_msg.subject}",
        source="cos@bigas.me",
    )
    return message


def resolve_sync_target_user_id() -> Optional[str]:
    """Resolve the chat user id that receives overnight email triage."""
    explicit_uid = (os.environ.get("BIGAS_EMAIL_SYNC_USER_UID") or "").strip()
    if explicit_uid:
        return explicit_uid

    from bigas.chat.auth import chat_auth_mode

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

    store = get_chat_store()
    user = store.find_user_by_email(email)
    return user.get("uid") if user else None


def execute_proposal_action(
    action: Dict[str, Any],
    *,
    thread_id: str,
    user_context: str = "",
    edited_text: Optional[str] = None,
    email_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Execute an approved proposal action; returns human-readable result."""
    kind = (action.get("kind") or "").strip().lower()
    params = action.get("params") if isinstance(action.get("params"), dict) else {}

    if kind == "draft_reply":
        text = (edited_text if edited_text is not None else params.get("text") or "")
        text = str(text).strip()
        if not text:
            raise RuntimeError("Reply body is empty.")
        meta = email_meta or {}
        to_addr = extract_email_address(
            str(meta.get("email_reply_to") or meta.get("email_from") or "")
        )
        if not to_addr:
            raise RuntimeError("Cannot send reply: no recipient address on this email.")
        send_outbound_reply(
            to_addr=to_addr,
            subject=str(meta.get("email_subject") or ""),
            body=text,
            in_reply_to=str(meta.get("source_id") or ""),
        )
        return f"Sent reply to {to_addr}:\n\n{text}"

    if kind == "delegate":
        agent_id = str(params.get("agent_id") or "").strip().lower()
        task = str(params.get("task") or user_context).strip()
        if not agent_id or not task:
            return "Missing agent_id or task for delegation."
        return run_specialist_task(agent_id, task, thread_id=thread_id, async_mode=True)

    if kind == "tool":
        from bigas.agents.chief_of_staff import _enrich_tool_args, _mcp_client, _run_tool_call

        tool_name = str(params.get("tool_name") or "").strip()
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not tool_name:
            return "Missing tool_name for tool action."
        client = _mcp_client()
        enriched = _enrich_tool_args(tool_name, arguments, user_context)
        return _run_tool_call(client, tool_name, enriched)

    return f"Unknown action kind: {kind}"
