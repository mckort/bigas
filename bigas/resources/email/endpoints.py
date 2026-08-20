"""Email sync HTTP API and proposal approve/reject handlers."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from flask import Blueprint, g, jsonify, request

from bigas.agents.email_processor import (
    analyze_email,
    execute_proposal_action,
    post_email_to_chief_thread,
    resolve_sync_target_user_id,
)
from bigas.chat.auth import require_chat_auth
from bigas.chat.db import get_chat_store
from bigas.registry import registry

logger = logging.getLogger(__name__)

email_bp = Blueprint("email", __name__)


def _email_provider():
    provider = registry.get("email")
    if provider is None:
        from bigas.providers.email.imap import ImapEmailProvider

        if ImapEmailProvider.is_configured():
            return ImapEmailProvider()
    return provider


@email_bp.route("/api/v1/providers/email/sync", methods=["POST"])
def sync_email():
    """
    Fetch unread IMAP mail, triage with Chief of Staff, post to the main chief chat.
    Secured by BIGAS_ACCESS_KEYS when BIGAS_ACCESS_MODE=restricted (Cloud Scheduler).
    """
    provider = _email_provider()
    if provider is None:
        return jsonify({"error": "Email provider not configured (set BIGAS_EMAIL_IMAP_* env vars)"}), 503

    user_id = resolve_sync_target_user_id()
    if not user_id:
        return jsonify(
            {
                "error": (
                    "No chat user found for email sync. Set BIGAS_EMAIL_SYNC_USER_UID, "
                    "BIGAS_EMAIL_SYNC_USER_EMAIL, or CHAT_ADMIN_EMAILS (user must have logged in once)."
                )
            }
        ), 503

    store = get_chat_store()
    thread = store.get_or_create_chief_thread(user_id)

    try:
        emails = provider.fetch_unread()
    except Exception as e:
        logger.exception("IMAP fetch failed")
        return jsonify({"error": f"IMAP sync failed: {e}"}), 500

    processed: List[Dict[str, Any]] = []
    skipped_spam = 0
    errors: List[str] = []

    for msg in emails:
        try:
            analysis = analyze_email(msg)
            if analysis is None:
                errors.append(f"LLM unavailable for {msg.message_id}")
                continue
            if analysis.get("is_spam"):
                skipped_spam += 1
                try:
                    provider.mark_processed(msg.uid)
                except Exception:
                    logger.exception("Failed to mark spam email %s as processed", msg.message_id)
                continue
            chat_message = post_email_to_chief_thread(thread["thread_id"], msg, analysis)
            try:
                provider.mark_processed(msg.uid)
            except Exception:
                logger.exception("Failed to mark email %s as processed after sync", msg.message_id)
                errors.append(f"{msg.message_id}: marked in chat but IMAP mark failed")
            processed.append(
                {
                    "message_id": msg.message_id,
                    "subject": msg.subject,
                    "chat_message_id": chat_message.get("message_id"),
                    "proposals": len(analysis.get("proposals") or []),
                }
            )
        except Exception as e:
            logger.exception("Failed to process email %s", msg.message_id)
            errors.append(f"{msg.message_id}: {e}")

    return jsonify(
        {
            "synced": len(processed),
            "fetched": len(emails),
            "skipped_spam": skipped_spam,
            "thread_id": thread["thread_id"],
            "processed": processed,
            "errors": errors,
        }
    )


def _find_action(message: Dict[str, Any], action_id: str) -> Dict[str, Any] | None:
    actions = (message.get("metadata") or {}).get("actions") or []
    for action in actions:
        if str(action.get("id")) == action_id:
            return action
    return None


@email_bp.route("/api/v1/chat/proposals/<proposal_id>/approve", methods=["POST"])
@require_chat_auth
def approve_proposal(proposal_id: str):
    body = request.get_json(silent=True) or {}
    message_id = (body.get("message_id") or "").strip()
    action_id = (body.get("action_id") or "").strip()
    if not message_id or not action_id:
        return jsonify({"error": "message_id and action_id are required"}), 400

    store = get_chat_store()
    message, claim_error = store.claim_proposal_for_approval(
        message_id,
        proposal_id=proposal_id,
        user_id=g.chat_user["uid"],
    )
    if claim_error == "not_found":
        return jsonify({"error": "Message not found"}), 404
    if claim_error == "invalid":
        return jsonify({"error": "Message is not an action proposal"}), 400
    if claim_error == "mismatch":
        return jsonify({"error": "Proposal ID mismatch"}), 400
    if claim_error == "not_pending":
        meta = (store.get_message(message_id) or {}).get("metadata") or {}
        return jsonify({"error": f"Proposal already {meta.get('status')}"}), 409
    if claim_error == "forbidden":
        return jsonify({"error": "Thread not found"}), 404

    thread = store.get_thread(message.get("thread_id") or "")
    if not thread:
        return jsonify({"error": "Thread not found"}), 404

    action = _find_action(message, action_id)
    if not action:
        store.update_message_metadata(message_id, {"status": "pending"})
        return jsonify({"error": "Action not found in proposal"}), 404

    user_context = message.get("content") or ""
    try:
        result = execute_proposal_action(
            action,
            thread_id=thread["thread_id"],
            user_context=user_context,
        )
    except Exception as e:
        logger.exception("Proposal execution failed")
        store.update_message_metadata(message_id, {"status": "pending"})
        return jsonify({"error": str(e)}), 500

    store.update_message_metadata(
        message_id,
        {"status": "approved", "approved_action_id": action_id},
    )
    follow_up = store.add_message(
        thread["thread_id"],
        role="assistant",
        content=f"✅ **Approved:** {action.get('label')}\n\n{result}",
        metadata={"agent_id": "chief", "proposal_id": proposal_id, "approved_action_id": action_id},
    )
    return jsonify({"status": "approved", "result": result, "message": follow_up})


@email_bp.route("/api/v1/chat/proposals/<proposal_id>/reject", methods=["POST"])
@require_chat_auth
def reject_proposal(proposal_id: str):
    body = request.get_json(silent=True) or {}
    message_id = (body.get("message_id") or "").strip()
    if not message_id:
        return jsonify({"error": "message_id is required"}), 400

    store = get_chat_store()
    message = store.get_message(message_id)
    if not message:
        return jsonify({"error": "Message not found"}), 404

    meta = message.get("metadata") or {}
    if meta.get("type") != "action_proposal":
        return jsonify({"error": "Message is not an action proposal"}), 400
    if meta.get("proposal_id") != proposal_id:
        return jsonify({"error": "Proposal ID mismatch"}), 400
    if meta.get("status") != "pending":
        return jsonify({"error": f"Proposal already {meta.get('status')}"}), 409

    thread = store.get_thread(message.get("thread_id") or "")
    if not thread or thread.get("user_id") != g.chat_user["uid"]:
        return jsonify({"error": "Thread not found"}), 404

    store.update_message_metadata(message_id, {"status": "rejected"})
    follow_up = store.add_message(
        thread["thread_id"],
        role="system",
        content="Proposal dismissed.",
        metadata={"agent_id": "chief", "proposal_id": proposal_id, "status": "rejected"},
    )
    return jsonify({"status": "rejected", "message": follow_up})
