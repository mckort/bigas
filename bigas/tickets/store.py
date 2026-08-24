"""Persistence for internal Kanban boards and tickets."""
from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from bigas.tickets.constants import columns_for_board, is_valid_status
from bigas.tickets.labels import has_marketing, normalize_labels, resolve_ticket_labels

_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _board_prefix(project_key: Optional[str]) -> str:
    if project_key:
        return project_key.strip().upper()
    return "PERS"


def _make_comment(
    body: str,
    *,
    author_name: Optional[str] = None,
    author_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    text = (body or "").strip()
    if not text:
        return None
    comment: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "body": text,
        "created_at": _utcnow_iso(),
    }
    name = (author_name or "").strip()
    if name:
        comment["author_name"] = name
    uid = (author_id or "").strip()
    if uid:
        comment["author_id"] = uid
    return comment


def _compose_ticket(
    *,
    ticket_id: str,
    board_id: str,
    key: str,
    title: str,
    description: str,
    status: str,
    issue_type: str,
    assignee: Optional[str],
    fix_version: Optional[str],
    labels: List[str],
    parent_key: Optional[str],
    thread_id: Optional[str],
    project_key: Optional[str],
    now: str,
) -> Dict[str, Any]:
    normalized = normalize_labels(labels)
    return {
        "ticket_id": ticket_id,
        "board_id": board_id,
        "key": key,
        "title": title,
        "description": description,
        "status": status,
        "issue_type": issue_type,
        "assignee": assignee,
        "fix_version": fix_version,
        "labels": normalized,
        "marketing": has_marketing(normalized),
        "parent_key": parent_key,
        "thread_id": thread_id,
        "comments": [],
        "attachments": [],
        "done_processed": False,
        "project_key": project_key,
        "created_at": now,
        "updated_at": now,
        "done_at": now if status == "Done" else "",
    }


def _prepare_ticket_values(
    board: Dict[str, Any],
    *,
    title: str,
    description: str = "",
    status: str = "To Do",
    issue_type: str = "Task",
    assignee: Optional[str] = None,
    fix_version: Optional[str] = None,
    marketing: bool = False,
    labels: Optional[List[Any]] = None,
    parent_key: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    proj = board.get("project_key")
    cols = columns_for_board(project_key=proj)
    st = (status or cols[0]).strip()
    if st not in cols:
        st = cols[0]
    summary = (title or "").strip()
    if not summary:
        raise ValueError("title is required")
    itype = (issue_type or "Task").strip().title() or "Task"
    if itype not in ("Task", "Bug", "Epic"):
        itype = "Task"
    parent = (parent_key or "").strip().upper() or None
    if parent and not _ISSUE_KEY_RE.match(parent):
        parent = None
    return {
        "title": summary,
        "description": (description or "").strip(),
        "status": st,
        "issue_type": itype,
        "assignee": (assignee or "").strip() or None,
        "fix_version": (fix_version or "").strip() or None,
        "labels": normalize_labels(labels, marketing=bool(marketing)),
        "parent_key": parent,
        "thread_id": (thread_id or "").strip() or None,
        "project_key": proj,
    }


def _apply_ticket_field_updates(
    ticket: Dict[str, Any],
    fields: Dict[str, Any],
    *,
    project_key: Optional[str],
) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    allowed = {
        "title",
        "description",
        "status",
        "issue_type",
        "assignee",
        "fix_version",
        "thread_id",
        "marketing",
        "labels",
        "parent_key",
        "done_processed",
    }
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "status":
            st = (value or "").strip()
            if not is_valid_status(st, project_key=project_key):
                continue
            updates["status"] = st
            old_status = (ticket.get("status") or "").strip()
            if st == "Done" and old_status != "Done":
                updates["done_at"] = _utcnow_iso()
            elif st != "Done" and old_status == "Done":
                updates["done_at"] = ""
        elif key == "title":
            title = (value or "").strip()
            if title:
                updates["title"] = title
        elif key == "description":
            updates["description"] = str(value or "")
        elif key == "assignee":
            updates["assignee"] = (value or "").strip() or None
        elif key == "fix_version":
            updates["fix_version"] = (value or "").strip() or None
        elif key == "thread_id":
            updates["thread_id"] = (value or "").strip() or None
        elif key == "parent_key":
            pk = (value or "").strip().upper() or None
            updates["parent_key"] = pk if pk and _ISSUE_KEY_RE.match(pk) else None
        elif key == "done_processed":
            updates["done_processed"] = bool(value)
        elif key == "issue_type":
            itype = (value or "Task").strip().title() or "Task"
            if itype in ("Task", "Bug", "Epic"):
                updates["issue_type"] = itype
    if "labels" in fields or "marketing" in fields:
        current = fields["labels"] if "labels" in fields else resolve_ticket_labels(ticket)
        marketing_flag = bool(fields["marketing"]) if "marketing" in fields else has_marketing(current)
        labels = normalize_labels(current, marketing=marketing_flag)
        if "marketing" in fields and not fields["marketing"]:
            labels = [item for item in labels if item != "marketing"]
        existing_labels = resolve_ticket_labels(ticket)
        if labels != existing_labels:
            updates["labels"] = labels
            updates["marketing"] = has_marketing(labels)
    return updates


def _key_number(key: str, prefix: str) -> Optional[int]:
    display = (key or "").strip().upper()
    if not display.startswith(f"{prefix}-"):
        return None
    try:
        return int(display.split("-", 1)[1])
    except (IndexError, ValueError):
        return None


class MemoryTicketStore:
    """Thread-safe in-memory store for boards and tickets."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._boards: Dict[str, Dict[str, Any]] = {}
        self._tickets: Dict[str, Dict[str, Any]] = {}
        self._key_index: Dict[str, str] = {}
        self._counters: Dict[str, int] = {}

    def _allocate_key(self, board: Dict[str, Any], requested: Optional[str] = None) -> str:
        display = (requested or "").strip().upper()
        prefix = _board_prefix(board.get("project_key"))
        with self._lock:
            if display:
                if not _ISSUE_KEY_RE.match(display):
                    raise ValueError(f"invalid key {requested!r}")
                if display in self._key_index:
                    raise ValueError(f"key {display} already exists")
                number = _key_number(display, prefix)
                if number is not None:
                    self._counters[prefix] = max(self._counters.get(prefix, 0), number)
                self._key_index[display] = ""
                return display
            seq = self._counters.get(prefix, 0) + 1
            display = f"{prefix}-{seq}"
            while display in self._key_index:
                seq += 1
                display = f"{prefix}-{seq}"
            self._counters[prefix] = seq
            self._key_index[display] = ""
            return display

    def list_boards(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            boards = [dict(b) for b in self._boards.values() if b.get("user_id") == user_id]
        return sorted(boards, key=lambda b: (b.get("project_key") or "ZZZ", b.get("name", "")))

    def get_board(self, board_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            board = self._boards.get(board_id)
            return dict(board) if board else None

    def create_board(
        self,
        user_id: str,
        *,
        name: str,
        project_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        title = (name or "").strip()
        if not title:
            raise ValueError("name is required")
        proj = (project_key or "").strip().upper() or None
        board_id = str(uuid.uuid4())
        now = _utcnow_iso()
        board = {
            "board_id": board_id,
            "user_id": user_id,
            "name": title,
            "project_key": proj,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._boards[board_id] = board
        return dict(board)

    def delete_board(self, board_id: str, *, user_id: str) -> bool:
        with self._lock:
            board = self._boards.get(board_id)
            if not board or board.get("user_id") != user_id:
                return False
            ticket_ids = [
                tid for tid, t in self._tickets.items() if t.get("board_id") == board_id
            ]
            for tid in ticket_ids:
                key = self._tickets[tid].get("key")
                if key:
                    self._key_index.pop(key, None)
                del self._tickets[tid]
            del self._boards[board_id]
        return True

    def update_board(
        self,
        board_id: str,
        *,
        user_id: str,
        name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            board = self._boards.get(board_id)
            if not board or board.get("user_id") != user_id:
                return None
            if name is not None:
                title = (name or "").strip()
                if title:
                    board["name"] = title
            board["updated_at"] = _utcnow_iso()
            return dict(board)

    def ensure_default_boards(self, user_id: str) -> List[Dict[str, Any]]:
        from bigas.portfolio import jira_project_keys

        existing = self.list_boards(user_id)
        if existing:
            return existing

        created: List[Dict[str, Any]] = []
        created.append(
            self.create_board(user_id, name="Personal tasks", project_key=None)
        )
        for key in jira_project_keys() or ["VFA", "BIG"]:
            created.append(
                self.create_board(user_id, name=f"{key} Board", project_key=key)
            )
        return created

    def list_tickets(
        self,
        board_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        board = self.get_board(board_id)
        if not board:
            return []
        if user_id and board.get("user_id") != user_id:
            return []
        with self._lock:
            tickets = [
                dict(t) for t in self._tickets.values() if t.get("board_id") == board_id
            ]
        return sorted(tickets, key=lambda t: t.get("created_at", ""))

    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            return dict(ticket) if ticket else None

    def get_ticket_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        display = (key or "").strip().upper()
        with self._lock:
            ticket_id = self._key_index.get(display)
            if not ticket_id:
                return None
            ticket = self._tickets.get(ticket_id)
            return dict(ticket) if ticket else None

    def create_ticket(
        self,
        board_id: str,
        *,
        title: str,
        description: str = "",
        status: str = "To Do",
        issue_type: str = "Task",
        assignee: Optional[str] = None,
        fix_version: Optional[str] = None,
        marketing: bool = False,
        labels: Optional[List[Any]] = None,
        parent_key: Optional[str] = None,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
        key: Optional[str] = None,
    ) -> Dict[str, Any]:
        board = self.get_board(board_id)
        if not board:
            raise ValueError("board not found")
        if user_id and board.get("user_id") != user_id:
            raise ValueError("forbidden")
        values = _prepare_ticket_values(
            board,
            title=title,
            description=description,
            status=status,
            issue_type=issue_type,
            assignee=assignee,
            fix_version=fix_version,
            marketing=marketing,
            labels=labels,
            parent_key=parent_key,
            thread_id=thread_id,
        )

        ticket_id = str(uuid.uuid4())
        display_key = self._allocate_key(board, key)
        now = _utcnow_iso()
        ticket = _compose_ticket(
            ticket_id=ticket_id,
            board_id=board_id,
            key=display_key,
            now=now,
            **values,
        )
        with self._lock:
            self._tickets[ticket_id] = ticket
            self._key_index[display_key] = ticket_id
        return dict(ticket)

    def update_ticket(
        self,
        ticket_id: str,
        *,
        user_id: Optional[str] = None,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None
            board = self._boards.get(ticket.get("board_id") or "")
            if user_id and board and board.get("user_id") != user_id:
                return None
            proj = board.get("project_key") if board else ticket.get("project_key")
            updates = _apply_ticket_field_updates(ticket, fields, project_key=proj)
            if updates:
                ticket.update(updates)
            ticket["updated_at"] = _utcnow_iso()
            return dict(ticket)

    def delete_ticket(self, ticket_id: str, *, user_id: Optional[str] = None) -> bool:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return False
            board = self._boards.get(ticket.get("board_id") or "")
            if user_id and board and board.get("user_id") != user_id:
                return False
            key = ticket.get("key")
            if key:
                self._key_index.pop(key, None)
            del self._tickets[ticket_id]
        return True

    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        author_name: Optional[str] = None,
        author_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        comment = _make_comment(body, author_name=author_name, author_id=author_id)
        if not comment:
            return None
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None
            comments = list(ticket.get("comments") or [])
            comments.append(comment)
            ticket["comments"] = comments
            ticket["updated_at"] = _utcnow_iso()
            return comment

    def list_comments(self, ticket_id: str) -> List[Dict[str, Any]]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return []
        return list(ticket.get("comments") or [])

    def add_attachment(
        self,
        ticket_id: str,
        attachment: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not attachment or not attachment.get("id"):
            return None
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None
            items = list(ticket.get("attachments") or [])
            items.append(attachment)
            ticket["attachments"] = items
            ticket["updated_at"] = _utcnow_iso()
            return dict(attachment)

    def remove_attachment(
        self,
        ticket_id: str,
        attachment_id: str,
    ) -> Optional[Dict[str, Any]]:
        aid = (attachment_id or "").strip()
        if not aid:
            return None
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None
            items = list(ticket.get("attachments") or [])
            kept = []
            removed = None
            for item in items:
                if not removed and (item.get("id") or "") == aid:
                    removed = item
                    continue
                kept.append(item)
            if removed is None:
                return None
            ticket["attachments"] = kept
            ticket["updated_at"] = _utcnow_iso()
            return dict(removed)

    def update_attachment(
        self,
        ticket_id: str,
        attachment_id: str,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        aid = (attachment_id or "").strip()
        if not aid or not fields:
            return None
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None
            items = list(ticket.get("attachments") or [])
            updated_item = None
            for index, item in enumerate(items):
                if (item.get("id") or "") != aid:
                    continue
                items[index] = {**item, **fields}
                updated_item = items[index]
                break
            if updated_item is None:
                return None
            ticket["attachments"] = items
            ticket["updated_at"] = _utcnow_iso()
            return dict(updated_item)

    def list_attachments(self, ticket_id: str) -> List[Dict[str, Any]]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return []
        return list(ticket.get("attachments") or [])

    def list_tickets_by_project(
        self,
        project_key: str,
        *,
        status: Optional[str] = None,
        issue_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        proj = (project_key or "").strip().upper()
        with self._lock:
            tickets = []
            for ticket in self._tickets.values():
                board = self._boards.get(ticket.get("board_id") or "")
                if not board or board.get("project_key") != proj:
                    continue
                if status and ticket.get("status") != status:
                    continue
                if issue_type and ticket.get("issue_type") != issue_type:
                    continue
                tickets.append(dict(ticket))
        return sorted(tickets, key=lambda t: t.get("key", ""))

    def list_tickets_by_status(
        self,
        status: str,
        *,
        project_keys: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        wanted = {str(k).strip().upper() for k in (project_keys or []) if str(k).strip()}
        st = (status or "").strip()
        with self._lock:
            tickets = []
            for ticket in self._tickets.values():
                if ticket.get("status") != st:
                    continue
                board = self._boards.get(ticket.get("board_id") or "")
                proj = (
                    (board.get("project_key") if board else None)
                    or ticket.get("project_key")
                    or ""
                )
                proj = str(proj).strip().upper()
                if not proj:
                    continue
                if wanted and proj not in wanted:
                    continue
                tickets.append(dict(ticket))
        return sorted(tickets, key=lambda t: t.get("key", ""))

    def list_epics(self, project_key: str) -> List[Dict[str, Any]]:
        return [
            t
            for t in self.list_tickets_by_project(project_key, issue_type="Epic")
            if t.get("status") != "Done"
        ]

    def list_all_epics(self) -> List[Dict[str, Any]]:
        with self._lock:
            tickets = [
                dict(t)
                for t in self._tickets.values()
                if (t.get("issue_type") or "").strip().title() == "Epic"
                and t.get("status") != "Done"
            ]
        out: List[Dict[str, Any]] = []
        for ticket in tickets:
            board = self.get_board(ticket.get("board_id") or "")
            if board and board.get("project_key"):
                out.append(ticket)
        return sorted(out, key=lambda t: t.get("key", ""))

    def list_tickets_for_parent(self, parent_key: str) -> List[Dict[str, Any]]:
        key = (parent_key or "").strip().upper()
        if not key:
            return []
        with self._lock:
            tickets = [
                dict(t) for t in self._tickets.values() if (t.get("parent_key") or "").upper() == key
            ]
        return sorted(tickets, key=lambda t: t.get("key", ""))

    def find_board_for_project(self, project_key: str, user_id: str) -> Optional[Dict[str, Any]]:
        proj = (project_key or "").strip().upper()
        for board in self.list_boards(user_id):
            if board.get("project_key") == proj:
                return board
        return None

    def get_jira_sync(self, board_id: str) -> Optional[Dict[str, Any]]:
        board = self.get_board(board_id)
        if not board:
            return None
        return board.get("jira_sync") or {"status": "idle"}

    def try_begin_jira_sync(self, board_id: str, *, user_id: str) -> bool:
        with self._lock:
            board = self._boards.get(board_id)
            if not board or board.get("user_id") != user_id:
                return False
            sync = board.get("jira_sync") or {}
            if sync.get("status") == "running":
                return False
            board["jira_sync"] = {
                "status": "running",
                "started_at": _utcnow_iso(),
                "finished_at": None,
                "result": None,
                "error": None,
            }
            board["updated_at"] = _utcnow_iso()
            return True

    def finish_jira_sync(
        self,
        board_id: str,
        *,
        user_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            board = self._boards.get(board_id)
            if not board or board.get("user_id") != user_id:
                return
            sync = board.get("jira_sync") or {}
            board["jira_sync"] = {
                **sync,
                "status": status,
                "finished_at": _utcnow_iso(),
                "result": result,
                "error": error,
            }
            board["updated_at"] = _utcnow_iso()


class FirestoreTicketStore:
    """Firestore-backed ticket store."""

    def __init__(self, project_id: str) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=project_id)
        self._boards = self._db.collection("ticket_boards")
        self._tickets = self._db.collection("tickets")
        self._counters = self._db.collection("ticket_counters")
        self._key_locks = self._db.collection("ticket_key_locks")

    def _allocate_key(self, board: Dict[str, Any], requested: Optional[str] = None) -> str:
        from google.cloud import firestore

        display = (requested or "").strip().upper()
        prefix = _board_prefix(board.get("project_key"))
        counter_ref = self._counters.document(prefix)

        @firestore.transactional
        def _allocate(transaction):
            # Do not call twice in the same transaction: reads must precede all writes.
            if display:
                if not _ISSUE_KEY_RE.match(display):
                    raise ValueError(f"invalid key {requested!r}")

                lock_ref = self._key_locks.document(display)
                tickets_query = self._tickets.where("key", "==", display).limit(1)
                existing = list(tickets_query.stream(transaction=transaction))
                if existing:
                    raise ValueError(f"key {display} already exists")
                lock_snap = lock_ref.get(transaction=transaction)
                if lock_snap.exists:
                    raise ValueError(f"key {display} already exists")

                number = _key_number(display, prefix)
                counter_snap = counter_ref.get(transaction=transaction)
                current = (
                    int((counter_snap.to_dict() or {}).get("seq") or 0)
                    if counter_snap.exists
                    else 0
                )
                if number is not None:
                    next_seq = max(current, number)
                    if next_seq != current:
                        transaction.set(
                            counter_ref,
                            {"seq": next_seq, "prefix": prefix},
                            merge=True,
                        )

                transaction.set(lock_ref, {"prefix": prefix, "key": display})
                return display

            # All reads must happen before writes in a Firestore transaction.
            counter_snap = counter_ref.get(transaction=transaction)
            current = (
                int((counter_snap.to_dict() or {}).get("seq") or 0)
                if counter_snap.exists
                else 0
            )
            seq = current + 1
            auto_display = f"{prefix}-{seq}"
            lock_ref = self._key_locks.document(auto_display)
            # Firestore transactions cap at 500 document reads (~2 reads/iteration here).
            for _ in range(200):
                lock_snap = lock_ref.get(transaction=transaction)
                if not lock_snap.exists:
                    existing = list(
                        self._tickets.where("key", "==", auto_display)
                        .limit(1)
                        .stream(transaction=transaction)
                    )
                    if not existing:
                        break
                seq += 1
                auto_display = f"{prefix}-{seq}"
                lock_ref = self._key_locks.document(auto_display)
            else:
                raise ValueError(f"could not allocate a free key for {prefix}")

            transaction.set(counter_ref, {"seq": seq, "prefix": prefix}, merge=True)
            transaction.set(lock_ref, {"prefix": prefix, "key": auto_display})
            return auto_display

        transaction = self._db.transaction()
        return _allocate(transaction)

    def list_boards(self, user_id: str) -> List[Dict[str, Any]]:
        docs = self._boards.where("user_id", "==", user_id).stream()
        boards = [doc.to_dict() for doc in docs if doc.exists]
        return sorted(boards, key=lambda b: (b.get("project_key") or "ZZZ", b.get("name", "")))

    def get_board(self, board_id: str) -> Optional[Dict[str, Any]]:
        snap = self._boards.document(board_id).get()
        return snap.to_dict() if snap.exists else None

    def create_board(
        self,
        user_id: str,
        *,
        name: str,
        project_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        title = (name or "").strip()
        if not title:
            raise ValueError("name is required")
        proj = (project_key or "").strip().upper() or None
        board_id = str(uuid.uuid4())
        now = _utcnow_iso()
        board = {
            "board_id": board_id,
            "user_id": user_id,
            "name": title,
            "project_key": proj,
            "created_at": now,
            "updated_at": now,
        }
        self._boards.document(board_id).set(board)
        return board

    def delete_board(self, board_id: str, *, user_id: str) -> bool:
        board = self.get_board(board_id)
        if not board or board.get("user_id") != user_id:
            return False
        for doc in self._tickets.where("board_id", "==", board_id).stream():
            doc.reference.delete()
        self._boards.document(board_id).delete()
        return True

    def update_board(
        self,
        board_id: str,
        *,
        user_id: str,
        name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        ref = self._boards.document(board_id)
        snap = ref.get()
        if not snap.exists:
            return None
        board = snap.to_dict() or {}
        if board.get("user_id") != user_id:
            return None
        if name is not None:
            title = (name or "").strip()
            if title:
                board["name"] = title
        board["updated_at"] = _utcnow_iso()
        ref.set(board)
        return board

    def ensure_default_boards(self, user_id: str) -> List[Dict[str, Any]]:
        from bigas.portfolio import jira_project_keys

        existing = self.list_boards(user_id)
        if existing:
            return existing
        created: List[Dict[str, Any]] = []
        created.append(
            self.create_board(user_id, name="Personal tasks", project_key=None)
        )
        for key in jira_project_keys() or ["VFA", "BIG"]:
            created.append(
                self.create_board(user_id, name=f"{key} Board", project_key=key)
            )
        return created

    def list_tickets(
        self,
        board_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        board = self.get_board(board_id)
        if not board:
            return []
        if user_id and board.get("user_id") != user_id:
            return []
        docs = self._tickets.where("board_id", "==", board_id).stream()
        tickets = [doc.to_dict() for doc in docs if doc.exists]
        return sorted(tickets, key=lambda t: t.get("created_at", ""))

    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        snap = self._tickets.document(ticket_id).get()
        return snap.to_dict() if snap.exists else None

    def get_ticket_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        display = (key or "").strip().upper()
        docs = list(self._tickets.where("key", "==", display).limit(1).stream())
        if not docs:
            return None
        return docs[0].to_dict()

    def create_ticket(
        self,
        board_id: str,
        *,
        title: str,
        description: str = "",
        status: str = "To Do",
        issue_type: str = "Task",
        assignee: Optional[str] = None,
        fix_version: Optional[str] = None,
        marketing: bool = False,
        labels: Optional[List[Any]] = None,
        parent_key: Optional[str] = None,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
        key: Optional[str] = None,
    ) -> Dict[str, Any]:
        board = self.get_board(board_id)
        if not board:
            raise ValueError("board not found")
        if user_id and board.get("user_id") != user_id:
            raise ValueError("forbidden")
        values = _prepare_ticket_values(
            board,
            title=title,
            description=description,
            status=status,
            issue_type=issue_type,
            assignee=assignee,
            fix_version=fix_version,
            marketing=marketing,
            labels=labels,
            parent_key=parent_key,
            thread_id=thread_id,
        )

        ticket_id = str(uuid.uuid4())
        display_key = self._allocate_key(board, key)
        now = _utcnow_iso()
        ticket = _compose_ticket(
            ticket_id=ticket_id,
            board_id=board_id,
            key=display_key,
            now=now,
            **values,
        )
        self._tickets.document(ticket_id).set(ticket)
        if display_key:
            self._key_locks.document(display_key).set(
                {"prefix": _board_prefix(board.get("project_key")), "key": display_key, "ticket_id": ticket_id},
                merge=True,
            )
        return ticket

    def update_ticket(
        self,
        ticket_id: str,
        *,
        user_id: Optional[str] = None,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        ref = self._tickets.document(ticket_id)
        snap = ref.get()
        if not snap.exists:
            return None
        ticket = snap.to_dict() or {}
        board = self.get_board(ticket.get("board_id") or "")
        if user_id and board and board.get("user_id") != user_id:
            return None
        proj = board.get("project_key") if board else ticket.get("project_key")
        updates = _apply_ticket_field_updates(ticket, fields, project_key=proj)
        if not updates:
            return ticket
        updates["updated_at"] = _utcnow_iso()
        ref.update(updates)
        merged = dict(ticket)
        merged.update(updates)
        return merged

    def delete_ticket(self, ticket_id: str, *, user_id: Optional[str] = None) -> bool:
        ref = self._tickets.document(ticket_id)
        snap = ref.get()
        if not snap.exists:
            return False
        ticket = snap.to_dict() or {}
        board = self.get_board(ticket.get("board_id") or "")
        if user_id and board and board.get("user_id") != user_id:
            return False
        key = (ticket.get("key") or "").strip().upper()
        batch = self._db.batch()
        batch.delete(ref)
        if key:
            batch.delete(self._key_locks.document(key))
        batch.commit()
        return True

    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        author_name: Optional[str] = None,
        author_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        from google.cloud.firestore import ArrayUnion

        comment = _make_comment(body, author_name=author_name, author_id=author_id)
        if not comment:
            return None
        ref = self._tickets.document(ticket_id)
        snap = ref.get()
        if not snap.exists:
            return None
        ref.update({"comments": ArrayUnion([comment]), "updated_at": comment["created_at"]})
        return comment

    def list_comments(self, ticket_id: str) -> List[Dict[str, Any]]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return []
        return list(ticket.get("comments") or [])

    def add_attachment(
        self,
        ticket_id: str,
        attachment: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        from google.cloud.firestore import ArrayUnion

        if not attachment or not attachment.get("id"):
            return None
        ref = self._tickets.document(ticket_id)
        snap = ref.get()
        if not snap.exists:
            return None
        now = _utcnow_iso()
        ref.update({"attachments": ArrayUnion([attachment]), "updated_at": now})
        return dict(attachment)

    def remove_attachment(
        self,
        ticket_id: str,
        attachment_id: str,
    ) -> Optional[Dict[str, Any]]:
        from google.cloud.firestore import ArrayRemove

        aid = (attachment_id or "").strip()
        if not aid:
            return None
        ref = self._tickets.document(ticket_id)
        snap = ref.get()
        if not snap.exists:
            return None
        ticket = snap.to_dict() or {}
        removed = next(
            (item for item in (ticket.get("attachments") or []) if (item.get("id") or "") == aid),
            None,
        )
        if removed is None:
            return None
        ref.update(
            {
                "attachments": ArrayRemove([removed]),
                "updated_at": _utcnow_iso(),
            }
        )
        return dict(removed)

    def update_attachment(
        self,
        ticket_id: str,
        attachment_id: str,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        from google.cloud import firestore

        aid = (attachment_id or "").strip()
        if not aid or not fields:
            return None
        ref = self._tickets.document(ticket_id)

        @firestore.transactional
        def _update(transaction):
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return None
            ticket = snap.to_dict() or {}
            items = list(ticket.get("attachments") or [])
            updated_item = None
            for index, item in enumerate(items):
                if (item.get("id") or "") != aid:
                    continue
                items[index] = {**item, **fields}
                updated_item = items[index]
                break
            if updated_item is None:
                return None
            transaction.update(
                ref,
                {"attachments": items, "updated_at": _utcnow_iso()},
            )
            return dict(updated_item)

        transaction = self._db.transaction()
        return _update(transaction)

    def list_attachments(self, ticket_id: str) -> List[Dict[str, Any]]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return []
        return list(ticket.get("attachments") or [])

    def list_tickets_by_project(
        self,
        project_key: str,
        *,
        status: Optional[str] = None,
        issue_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        proj = (project_key or "").strip().upper()
        docs = self._tickets.where("project_key", "==", proj).stream()
        tickets = []
        for doc in docs:
            if not doc.exists:
                continue
            ticket = doc.to_dict()
            if status and ticket.get("status") != status:
                continue
            if issue_type and ticket.get("issue_type") != issue_type:
                continue
            tickets.append(ticket)
        return sorted(tickets, key=lambda t: t.get("key", ""))

    def list_tickets_by_status(
        self,
        status: str,
        *,
        project_keys: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        wanted = [str(k).strip().upper() for k in (project_keys or []) if str(k).strip()]
        st = (status or "").strip()
        tickets: List[Dict[str, Any]] = []

        def _collect(query) -> None:
            for doc in query.stream():
                if not doc.exists:
                    continue
                ticket = doc.to_dict() or {}
                proj = str(ticket.get("project_key") or "").strip().upper()
                if not proj:
                    continue
                tickets.append(ticket)

        if wanted:
            # Firestore `in` queries accept at most 10 values.
            for start in range(0, len(wanted), 10):
                chunk = wanted[start : start + 10]
                _collect(
                    self._tickets.where("status", "==", st).where(
                        "project_key", "in", chunk
                    )
                )
        else:
            _collect(self._tickets.where("status", "==", st))

        return sorted(tickets, key=lambda t: t.get("key", ""))

    def list_epics(self, project_key: str) -> List[Dict[str, Any]]:
        return [
            t
            for t in self.list_tickets_by_project(project_key, issue_type="Epic")
            if t.get("status") != "Done"
        ]

    def list_all_epics(self) -> List[Dict[str, Any]]:
        tickets = []
        board_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        for doc in self._tickets.where("issue_type", "==", "Epic").stream():
            if not doc.exists:
                continue
            ticket = doc.to_dict() or {}
            if ticket.get("status") == "Done":
                continue
            board_id = ticket.get("board_id") or ""
            if board_id not in board_cache:
                board_cache[board_id] = self.get_board(board_id) if board_id else None
            board = board_cache[board_id]
            proj = ticket.get("project_key") or (board or {}).get("project_key")
            if proj:
                tickets.append(ticket)
        return sorted(tickets, key=lambda t: t.get("key", ""))

    def list_tickets_for_parent(self, parent_key: str) -> List[Dict[str, Any]]:
        key = (parent_key or "").strip().upper()
        if not key:
            return []
        docs = self._tickets.where("parent_key", "==", key).stream()
        tickets = [doc.to_dict() for doc in docs if doc.exists]
        return sorted(tickets, key=lambda t: t.get("key", ""))

    def find_board_for_project(self, project_key: str, user_id: str) -> Optional[Dict[str, Any]]:
        proj = (project_key or "").strip().upper()
        for board in self.list_boards(user_id):
            if board.get("project_key") == proj:
                return board
        return None

    def get_jira_sync(self, board_id: str) -> Optional[Dict[str, Any]]:
        board = self.get_board(board_id)
        if not board:
            return None
        return board.get("jira_sync") or {"status": "idle"}

    def try_begin_jira_sync(self, board_id: str, *, user_id: str) -> bool:
        from google.cloud import firestore

        ref = self._boards.document(board_id)

        @firestore.transactional
        def _begin(transaction):
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return False
            board = snap.to_dict() or {}
            if board.get("user_id") != user_id:
                return False
            sync = board.get("jira_sync") or {}
            if sync.get("status") == "running":
                return False
            transaction.update(
                ref,
                {
                    "jira_sync": {
                        "status": "running",
                        "started_at": _utcnow_iso(),
                        "finished_at": None,
                        "result": None,
                        "error": None,
                    },
                    "updated_at": _utcnow_iso(),
                },
            )
            return True

        transaction = self._db.transaction()
        return _begin(transaction)

    def finish_jira_sync(
        self,
        board_id: str,
        *,
        user_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        ref = self._boards.document(board_id)
        snap = ref.get()
        if not snap.exists:
            return
        board = snap.to_dict() or {}
        if board.get("user_id") != user_id:
            return
        sync = board.get("jira_sync") or {}
        ref.update(
            {
                "jira_sync": {
                    **sync,
                    "status": status,
                    "finished_at": _utcnow_iso(),
                    "result": result,
                    "error": error,
                },
                "updated_at": _utcnow_iso(),
            }
        )


_store: Optional[Any] = None
_store_lock = threading.Lock()


def get_ticket_store():
    """Return singleton ticket store (Firestore or in-memory)."""
    global _store
    with _store_lock:
        if _store is not None:
            return _store

        storage_mode = (os.environ.get("CHAT_STORAGE_MODE") or "").strip().lower()
        project_id = (
            os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_PROJECT_ID") or ""
        ).strip()

        if storage_mode == "memory" or (storage_mode != "firestore" and not project_id):
            _store = MemoryTicketStore()
        else:
            try:
                _store = FirestoreTicketStore(project_id)
            except Exception:
                _store = MemoryTicketStore()

        return _store
