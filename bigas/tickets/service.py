"""Business logic for internal ticket boards."""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

import requests

from bigas.tickets.constants import AI_TRIGGER_STATUSES, columns_for_board, next_column
from bigas.tickets.labels import resolve_ticket_labels
from bigas.tickets.store import get_ticket_store

logger = logging.getLogger(__name__)

SYNC_USER_ENV = "BIGAS_EMAIL_SYNC_USER_EMAIL"


def _sync_user_id() -> str:
    """User id for agent-created tickets (first admin or dev-user)."""
    from bigas.chat.db import get_chat_store

    email = (
        os.environ.get(SYNC_USER_ENV)
        or os.environ.get("CHAT_ADMIN_EMAILS", "").split(",")[0].strip()
        or "dev@localhost"
    )
    store = get_chat_store()
    user = store.find_user_by_email(email)
    if user:
        return user["uid"]
    return "dev-user"


def _should_run_interpretation_inline() -> bool:
    """Run screenshot interpretation inline in tests."""
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get("TESTING"):
            return True
    except Exception:
        pass
    return False


def ticket_url(key: str) -> str:
    return f"/board?ticket={key}"


def _ticket_automation_worker_url() -> str:
    """Loopback only — do not use public SERVER_URL (e.g. https://bigas.me)."""
    port = (os.environ.get("PORT") or "8080").strip() or "8080"
    return f"http://127.0.0.1:{port}/api/tickets/automation-worker"


def _request_authorization() -> str:
    try:
        from flask import has_request_context, request

        if has_request_context():
            return (request.headers.get("Authorization") or "").strip()
    except Exception:
        pass
    return ""


def _ticket_automation_dispatch_headers() -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "bigas-core/1.0 (ticket-automation-worker)",
    }
    auth = _request_authorization()
    if auth:
        headers["Authorization"] = auth
    return headers


def _should_run_automation_inline() -> bool:
    """Skip HTTP dispatch in tests, or when there is no logged-in request to forward."""
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get("TESTING"):
            return True
    except Exception:
        pass
    return not _request_authorization()


def run_ticket_status_automation(
    ticket: Dict[str, Any],
    *,
    old_status: str,
    new_status: str,
    project_key: str,
) -> None:
    """Run AI automation and Done-side effects for a status change."""
    service = TicketService()
    try:
        if new_status in AI_TRIGGER_STATUSES:
            from bigas.tickets.automation import InternalTicketAutomation

            InternalTicketAutomation().handle_status_change(
                issue_key=ticket.get("key") or "",
                to_status=new_status,
                from_status=old_status,
                project_key=project_key,
            )
        elif new_status == "Done" and not ticket.get("done_processed"):
            service._handle_done(ticket, project_key=project_key)
    except Exception:
        logger.exception(
            "Ticket automation failed for %s → %s",
            ticket.get("key"),
            new_status,
        )


def dispatch_ticket_status_automation(
    ticket: Dict[str, Any],
    *,
    old_status: str,
    new_status: str,
    project_key: str,
) -> None:
    """Dispatch status automation via loopback HTTP so Cloud Run allocates CPU."""
    payload = {
        "ticket_id": ticket.get("ticket_id") or "",
        "issue_key": ticket.get("key") or "",
        "old_status": old_status,
        "new_status": new_status,
        "project_key": project_key,
        "done_processed": bool(ticket.get("done_processed")),
    }
    if _should_run_automation_inline():
        run_ticket_status_automation(
            ticket,
            old_status=old_status,
            new_status=new_status,
            project_key=project_key,
        )
        return

    try:
        requests.post(
            _ticket_automation_worker_url(),
            json=payload,
            headers=_ticket_automation_dispatch_headers(),
            timeout=(10, 1),
        )
    except requests.exceptions.ReadTimeout:
        return
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.ConnectTimeout,
    ) as e:
        logger.warning(
            "Ticket automation worker dispatch failed for %s; running inline: %s",
            ticket.get("key"),
            e,
        )
        run_ticket_status_automation(
            ticket,
            old_status=old_status,
            new_status=new_status,
            project_key=project_key,
        )
    except requests.exceptions.RequestException as e:
        logger.warning(
            "Ticket automation worker dispatch failed for %s: %s",
            ticket.get("key"),
            e,
        )


def comment_author_name(user: Optional[Dict[str, Any]]) -> str:
    if not user:
        return "Someone"
    email = (user.get("email") or "").strip()
    if "@" in email:
        return email.split("@", 1)[0]
    if email:
        return email
    return (user.get("uid") or "Someone").strip() or "Someone"


def ticket_to_api(ticket: Dict[str, Any], *, include_comments: bool = True) -> Dict[str, Any]:
    key = ticket.get("key") or ""
    comments = list(ticket.get("comments") or [])
    attachments = list(ticket.get("attachments") or [])
    labels = resolve_ticket_labels(ticket)
    payload = {
        **ticket,
        "labels": labels,
        "marketing": any(label == "marketing" for label in labels),
        "url": ticket_url(key),
        "summary": ticket.get("title") or key,
    }
    if include_comments:
        payload["comments"] = comments
        payload["attachments"] = attachments
    else:
        payload.pop("comments", None)
        payload.pop("attachments", None)
        payload["comment_count"] = len(comments)
        payload["attachment_count"] = len(attachments)
    return payload


class TicketService:
    def __init__(self) -> None:
        self._store = get_ticket_store()

    def list_boards(self, user_id: str) -> List[Dict[str, Any]]:
        boards = self._store.ensure_default_boards(user_id)
        return [
            {
                **b,
                "columns": columns_for_board(project_key=b.get("project_key")),
                "workflow_enabled": bool(b.get("project_key")),
            }
            for b in boards
        ]

    def create_board(
        self,
        user_id: str,
        *,
        name: str,
        project_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        board = self._store.create_board(user_id, name=name, project_key=project_key)
        return {
            **board,
            "columns": columns_for_board(project_key=board.get("project_key")),
            "workflow_enabled": bool(board.get("project_key")),
        }

    def delete_board(self, board_id: str, *, user_id: str) -> bool:
        tickets = self._store.list_tickets(board_id, user_id=user_id)
        if not self._store.delete_board(board_id, user_id=user_id):
            return False
        for ticket in tickets:
            self._delete_ticket_attachment_blobs(ticket)
        return True

    def delete_ticket(self, ticket_id: str, *, user_id: str, delete_children: bool = False) -> bool:
        ticket = self._store.get_ticket(ticket_id)
        if not ticket:
            return False
        board = self._store.get_board(ticket.get("board_id") or "")
        if board and board.get("user_id") != user_id:
            return False
        parent_key = (ticket.get("key") or "").strip().upper()
        children = self._store.list_tickets_for_parent(parent_key) if parent_key else []
        if delete_children:
            for child in children:
                child_id = child.get("ticket_id") or ""
                if child_id and self._store.delete_ticket(child_id, user_id=user_id):
                    self._delete_ticket_attachment_blobs(child)
        else:
            for child in children:
                child_id = child.get("ticket_id") or ""
                if child_id:
                    self._store.update_ticket(child_id, parent_key="", parent_kr_id="")
        if not self._store.delete_ticket(ticket_id, user_id=user_id):
            return False
        self._delete_ticket_attachment_blobs(ticket)
        return True

    def _delete_ticket_attachment_blobs(self, ticket: Dict[str, Any]) -> None:
        from bigas.tickets.attachments import delete_attachment_blobs

        delete_attachment_blobs(list(ticket.get("attachments") or []))

    def list_tickets(self, board_id: str, *, user_id: str) -> List[Dict[str, Any]]:
        tickets = self._store.list_tickets(board_id, user_id=user_id)
        return [ticket_to_api(t, include_comments=False) for t in tickets]

    def add_comment(
        self,
        ticket_id: str,
        *,
        body: str,
        author_name: Optional[str] = None,
        author_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self._store.add_comment(
            ticket_id,
            body,
            author_name=author_name,
            author_id=author_id,
        )

    def add_attachment(
        self,
        ticket_id: str,
        *,
        filename: str,
        content_type: Optional[str],
        data: bytes,
        uploaded_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        from bigas.tickets.attachments import (
            AttachmentError,
            IMAGE_MIME_TYPES,
            attachment_blob_name,
            build_attachment_record,
            extract_attachment_text,
            get_attachment_blob_store,
            validate_upload,
        )

        ticket = self._store.get_ticket(ticket_id)
        if not ticket:
            raise AttachmentError("Ticket not found")
        existing = list(ticket.get("attachments") or [])
        safe_name, mime = validate_upload(
            filename=filename,
            content_type=content_type,
            size_bytes=len(data or b""),
            existing_count=len(existing),
        )
        defer_image_llm = mime in IMAGE_MIME_TYPES and not _should_run_interpretation_inline()
        extracted = extract_attachment_text(
            data=data,
            filename=safe_name,
            content_type=mime,
            defer_image_llm=defer_image_llm,
        )
        record = build_attachment_record(
            filename=safe_name,
            content_type=mime,
            size_bytes=len(data),
            storage_path="",
            extracted_text=extracted,
            uploaded_by=uploaded_by,
        )
        path = attachment_blob_name(ticket_id, record["id"], safe_name)
        record["storage_path"] = path
        blobs = get_attachment_blob_store()
        blobs.put(path, data, mime)
        saved = self._store.add_attachment(ticket_id, record)
        if not saved:
            blobs.delete(path)
            raise AttachmentError("Could not save attachment")
        if defer_image_llm:
            self._dispatch_attachment_interpretation(
                ticket_id=ticket_id,
                attachment_id=record["id"],
                data=data,
                filename=safe_name,
                content_type=mime,
            )
        return saved

    def _dispatch_attachment_interpretation(
        self,
        *,
        ticket_id: str,
        attachment_id: str,
        data: bytes,
        filename: str,
        content_type: str,
    ) -> None:
        thread = threading.Thread(
            target=self._interpret_attachment_background,
            kwargs={
                "ticket_id": ticket_id,
                "attachment_id": attachment_id,
                "data": data,
                "filename": filename,
                "content_type": content_type,
            },
            daemon=True,
        )
        thread.start()

    def _interpret_attachment_background(
        self,
        *,
        ticket_id: str,
        attachment_id: str,
        data: bytes,
        filename: str,
        content_type: str,
    ) -> None:
        from bigas.tickets.attachments import clip_extracted_text, describe_image, _image_fallback

        try:
            extracted = clip_extracted_text(
                describe_image(data, content_type, filename),
            )
            self._store.update_attachment(
                ticket_id,
                attachment_id,
                extracted_text=extracted,
            )
        except Exception:
            logger.warning(
                "Background screenshot interpretation failed for ticket %s attachment %s",
                ticket_id,
                attachment_id,
                exc_info=True,
            )
            try:
                failure_text = clip_extracted_text(
                    _image_fallback(filename, "interpretation failed"),
                )
                self._store.update_attachment(
                    ticket_id,
                    attachment_id,
                    extracted_text=failure_text,
                )
            except Exception:
                logger.warning(
                    "Could not mark attachment interpretation failure for ticket %s attachment %s",
                    ticket_id,
                    attachment_id,
                    exc_info=True,
                )

    def delete_attachment(self, ticket_id: str, attachment_id: str) -> Optional[Dict[str, Any]]:
        from bigas.tickets.attachments import get_attachment_blob_store

        removed = self._store.remove_attachment(ticket_id, attachment_id)
        if not removed:
            return None
        path = (removed.get("storage_path") or "").strip()
        if path:
            get_attachment_blob_store().delete(path)
        return removed

    def get_attachment_bytes(
        self,
        ticket_id: str,
        attachment_id: str,
    ) -> Optional[tuple]:
        from bigas.tickets.attachments import get_attachment_blob_store

        items = self._store.list_attachments(ticket_id)
        record = next((a for a in items if (a.get("id") or "") == attachment_id), None)
        if not record:
            return None
        path = (record.get("storage_path") or "").strip()
        if not path:
            return None
        data = get_attachment_blob_store().get(path)
        if data is None:
            return None
        return record, data

    def create_ticket(
        self,
        board_id: str,
        *,
        user_id: Optional[str] = None,
        title: str,
        description: str = "",
        status: str = "To Do",
        issue_type: str = "Task",
        assignee: Optional[str] = None,
        fix_version: Optional[str] = None,
        marketing: bool = False,
        labels: Optional[List[Any]] = None,
        parent_key: Optional[str] = None,
        parent_kr_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        key: Optional[str] = None,
        key_results: Optional[List[Any]] = None,
        okr_cycle: Optional[str] = None,
        okr_owner: Optional[str] = None,
        okr_briefing: Optional[str] = None,
        okr_phase: Optional[str] = None,
    ) -> Dict[str, Any]:
        ticket = self._store.create_ticket(
            board_id,
            title=title,
            description=description,
            status=status,
            issue_type=issue_type,
            assignee=assignee,
            fix_version=fix_version,
            marketing=marketing,
            labels=labels,
            parent_key=parent_key,
            parent_kr_id=parent_kr_id,
            thread_id=thread_id,
            user_id=user_id,
            key=key,
            key_results=key_results,
            okr_cycle=okr_cycle,
            okr_owner=okr_owner,
            okr_briefing=okr_briefing,
            okr_phase=okr_phase,
        )
        return ticket_to_api(ticket)

    def update_ticket(
        self,
        ticket_id: str,
        *,
        user_id: str,
        previous_status: Optional[str] = None,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        existing = self._store.get_ticket(ticket_id)
        if not existing:
            return None
        old_status = previous_status or existing.get("status")
        ticket = self._store.update_ticket(ticket_id, user_id=user_id, **fields)
        if not ticket:
            return None
        new_status = ticket.get("status")
        if new_status and new_status != old_status:
            self._on_status_change(ticket, old_status=old_status or "", new_status=new_status)
            ticket = self._store.get_ticket(ticket_id) or ticket
        return ticket_to_api(ticket)

    def create_ticket_for_project(
        self,
        project_key: str,
        *,
        title: str,
        description: str,
        issue_type: str = "Task",
        marketing: bool = False,
        labels: Optional[List[Any]] = None,
        parent_key: Optional[str] = None,
        parent_kr_id: Optional[str] = None,
        user_id: Optional[str] = None,
        key: Optional[str] = None,
        status: str = "To Do",
    ) -> Dict[str, Any]:
        uid = user_id or _sync_user_id()
        board = self._store.find_board_for_project(project_key, uid)
        if not board:
            board = self._store.create_board(
                uid, name=f"{project_key} Board", project_key=project_key
            )
        ticket = self._store.create_ticket(
            board["board_id"],
            title=title,
            description=description,
            issue_type=issue_type,
            marketing=marketing,
            labels=labels,
            parent_key=parent_key,
            parent_kr_id=parent_kr_id,
            user_id=uid,
            key=key,
            status=status,
        )
        return ticket_to_api(ticket)

    def transition_to_next(self, issue_key: str) -> Dict[str, Any]:
        from bigas.tickets.jira_adapter import TicketJiraAdapter

        adapter = TicketJiraAdapter()
        result = adapter.transition_issue_to_next(issue_key)
        ticket = self._store.get_ticket_by_key(issue_key)
        if ticket:
            self._on_status_change(
                ticket,
                old_status=result.get("previous_status") or "",
                new_status=result.get("new_status") or "",
            )
        return result

    def _on_status_change(
        self,
        ticket: Dict[str, Any],
        *,
        old_status: str,
        new_status: str,
    ) -> None:
        from bigas.okr.model import is_objective

        board = self._store.get_board(ticket.get("board_id") or "")
        if new_status == old_status:
            return

        if is_objective(ticket) and new_status in AI_TRIGGER_STATUSES:
            dispatch_ticket_status_automation(
                ticket,
                old_status=old_status or "",
                new_status=new_status,
                project_key=(board or {}).get("project_key") or "",
            )
            return

        if not board or not board.get("project_key"):
            return

        project_key = board.get("project_key") or ""

        dispatch_ticket_status_automation(
            ticket,
            old_status=old_status or "",
            new_status=new_status,
            project_key=project_key,
        )

    def _handle_done(self, ticket: Dict[str, Any], *, project_key: str) -> None:
        from bigas.chat.activity import mirror_to_activity_feed, post_to_agent_thread

        key = ticket.get("key") or ""
        title = ticket.get("title") or key
        msg = f"Ticket **{key}** ({title}) moved to Done on the {project_key} board."
        mirror_to_activity_feed(msg, type_="ticket", source="product")
        post_to_agent_thread(
            "product",
            f"Progress note: {msg}\n\nConsider drafting a progress update or release notes if this completes a batch.",
        )
        self._store.update_ticket(ticket["ticket_id"], done_processed=True)

    def lookup_ticket(self, key: str) -> Optional[Dict[str, Any]]:
        ticket = self._store.get_ticket_by_key(key)
        return ticket_to_api(ticket) if ticket else None

    def lookup_tickets(self, keys: List[str]) -> List[Dict[str, Any]]:
        out = []
        for key in keys:
            ticket = self.lookup_ticket(key)
            if ticket:
                out.append(
                    {
                        "key": ticket["key"],
                        "summary": ticket.get("title") or ticket["key"],
                        "status": ticket.get("status"),
                        "url": ticket.get("url"),
                        "issue_type": ticket.get("issue_type"),
                    }
                )
        return out

    def list_epics(self, project_key: str) -> List[Dict[str, Any]]:
        epics = self._store.list_epics(project_key)
        return [
            {
                "key": e["key"],
                "summary": e.get("title") or e["key"],
                "status": e.get("status"),
                "url": ticket_url(e["key"]),
            }
            for e in epics
        ]
