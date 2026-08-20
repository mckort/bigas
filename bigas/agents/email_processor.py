"""Chief of Staff email triage — summarize inbox messages and propose actions."""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from bigas.agents.chief_of_staff import _parse_json_action, run_specialist_task
from bigas.chat.db import get_chat_store
from bigas.llm.factory import get_llm_client
from bigas.portfolio import prompt_block
from bigas.providers.email.base import InboundEmail

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
        "Do NOT take autonomous actions — only summarize and propose actions the human can approve.\n"
        "Silently discard obvious spam or marketing noise (set is_spam=true and leave proposals empty).\n"
        "For legitimate emails, write a concise markdown summary highlighting what matters and why.\n"
        "Propose 1–3 concrete next steps when applicable (e.g. delegate to a specialist, call a tool, "
        "or draft a reply text). Never propose sending email directly — use draft_reply for reply drafts.\n\n"
        f"{portfolio}\n\n"
        "Respond with ONLY a JSON object:\n"
        "{\n"
        '  "is_spam": false,\n'
        '  "summary": "**From:** ...\\n\\n**Subject:** ...\\n\\nKey points...",\n'
        '  "proposals": [\n'
        '    {"id": "unique_id", "label": "Short button label", "kind": "delegate|tool|draft_reply", '
        '"params": {...}}\n'
        "  ]\n"
        "}\n"
        "For delegate: params must include agent_id (marketing|product|cto|devops) and task.\n"
        "For tool: params must include tool_name and arguments object.\n"
        "For draft_reply: params must include text (the draft reply body).\n"
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
    Analyze one email with the COS LLM.
    Returns dict with summary, proposals, is_spam — or None if LLM unavailable.
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
        # Fallback: treat raw text as summary with no proposals
        summary = raw.strip() or f"**Email:** {email_msg.subject}\n\nFrom {email_msg.sender}"
        return {"is_spam": False, "summary": summary, "proposals": []}

    if parsed.get("is_spam"):
        return {"is_spam": True, "summary": "", "proposals": []}

    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        summary = (
            f"**From:** {email_msg.sender}\n\n"
            f"**Subject:** {email_msg.subject}\n\n"
            f"{email_msg.body_text[:500]}"
        )

    return {
        "is_spam": False,
        "summary": summary,
        "proposals": _normalize_proposals(parsed.get("proposals")),
    }


def post_email_to_chief_thread(
    thread_id: str,
    email_msg: InboundEmail,
    analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """Post COS summary and optional action proposals to the chief chat thread."""
    store = get_chat_store()
    proposals = analysis.get("proposals") or []
    proposal_id = str(uuid.uuid4())
    header = f"📬 **Email triage** — {email_msg.subject}\n\n"
    content = header + (analysis.get("summary") or "")

    metadata: Dict[str, Any] = {
        "agent_id": "chief",
        "source": "email",
        "source_id": email_msg.message_id,
        "email_from": email_msg.sender,
        "email_subject": email_msg.subject,
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
) -> str:
    """Execute an approved proposal action; returns human-readable result."""
    kind = (action.get("kind") or "").strip().lower()
    params = action.get("params") if isinstance(action.get("params"), dict) else {}

    if kind == "draft_reply":
        text = str(params.get("text") or "").strip()
        if not text:
            return "No draft reply text was provided."
        store = get_chat_store()
        store.add_message(
            thread_id,
            role="assistant",
            content=f"**Draft reply** (not sent — review and send manually):\n\n{text}",
            metadata={"agent_id": "chief", "source": "email_proposal"},
        )
        return "Draft reply added to the chat."

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
