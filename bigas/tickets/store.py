"""Persistence for internal Kanban boards and tickets."""
from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bigas.tickets.constants import columns_for_board, is_valid_status

_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _board_prefix(project_key: Optional[str]) -> str:
    if project_key:
        return project_key.strip().upper()
    return "PERS"


class MemoryTicketStore:
    """Thread-safe in-memory store for boards and tickets."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._boards: Dict[str, Dict[str, Any]] = {}
        self._tickets: Dict[str, Dict[str, Any]] = {}
        self._key_index: Dict[str, str] = {}
        self._counters: Dict[str, int] = {}

    def _next_key(self, board: Dict[str, Any]) -> str:
        prefix = _board_prefix(board.get("project_key"))
        with self._lock:
            seq = self._counters.get(prefix, 0) + 1
            self._counters[prefix] = seq
        return f"{prefix}-{seq}"

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
        parent_key: Optional[str] = None,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        board = self.get_board(board_id)
        if not board:
            raise ValueError("board not found")
        if user_id and board.get("user_id") != user_id:
            raise ValueError("forbidden")
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

        ticket_id = str(uuid.uuid4())
        display_key = self._next_key(board)
        now = _utcnow_iso()
        ticket = {
            "ticket_id": ticket_id,
            "board_id": board_id,
            "key": display_key,
            "title": summary,
            "description": (description or "").strip(),
            "status": st,
            "issue_type": itype,
            "assignee": (assignee or "").strip() or None,
            "fix_version": (fix_version or "").strip() or None,
            "marketing": bool(marketing),
            "parent_key": parent,
            "thread_id": (thread_id or "").strip() or None,
            "comments": [],
            "done_processed": False,
            "project_key": proj,
            "created_at": now,
            "updated_at": now,
        }
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
            allowed = {
                "title",
                "description",
                "status",
                "assignee",
                "fix_version",
                "thread_id",
                "marketing",
                "parent_key",
                "done_processed",
            }
            for key, value in fields.items():
                if key not in allowed:
                    continue
                if key == "status":
                    st = (value or "").strip()
                    if not is_valid_status(st, project_key=proj):
                        continue
                    ticket["status"] = st
                elif key == "title":
                    title = (value or "").strip()
                    if title:
                        ticket["title"] = title
                elif key == "description":
                    ticket["description"] = str(value or "")
                elif key == "assignee":
                    ticket["assignee"] = (value or "").strip() or None
                elif key == "fix_version":
                    ticket["fix_version"] = (value or "").strip() or None
                elif key == "thread_id":
                    ticket["thread_id"] = (value or "").strip() or None
                elif key == "marketing":
                    ticket["marketing"] = bool(value)
                elif key == "parent_key":
                    pk = (value or "").strip().upper() or None
                    ticket["parent_key"] = pk if pk and _ISSUE_KEY_RE.match(pk) else None
                elif key == "done_processed":
                    ticket["done_processed"] = bool(value)
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

    def add_comment(self, ticket_id: str, body: str) -> Optional[Dict[str, Any]]:
        text = (body or "").strip()
        if not text:
            return None
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None
            comment = {
                "id": str(uuid.uuid4()),
                "body": text,
                "created_at": _utcnow_iso(),
            }
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


class FirestoreTicketStore:
    """Firestore-backed ticket store."""

    def __init__(self, project_id: str) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=project_id)
        self._boards = self._db.collection("ticket_boards")
        self._tickets = self._db.collection("tickets")
        self._counters = self._db.collection("ticket_counters")

    def _next_key(self, board: Dict[str, Any]) -> str:
        from google.cloud import firestore

        prefix = _board_prefix(board.get("project_key"))
        counter_ref = self._counters.document(prefix)

        @firestore.transactional
        def _increment(transaction):
            snap = counter_ref.get(transaction=transaction)
            current = int((snap.to_dict() or {}).get("seq") or 0) if snap.exists else 0
            seq = current + 1
            transaction.set(counter_ref, {"seq": seq, "prefix": prefix}, merge=True)
            return f"{prefix}-{seq}"

        transaction = self._db.transaction()
        return _increment(transaction)

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
        parent_key: Optional[str] = None,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        board = self.get_board(board_id)
        if not board:
            raise ValueError("board not found")
        if user_id and board.get("user_id") != user_id:
            raise ValueError("forbidden")
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

        ticket_id = str(uuid.uuid4())
        display_key = self._next_key(board)
        now = _utcnow_iso()
        ticket = {
            "ticket_id": ticket_id,
            "board_id": board_id,
            "key": display_key,
            "title": summary,
            "description": (description or "").strip(),
            "status": st,
            "issue_type": itype,
            "assignee": (assignee or "").strip() or None,
            "fix_version": (fix_version or "").strip() or None,
            "marketing": bool(marketing),
            "parent_key": parent,
            "thread_id": (thread_id or "").strip() or None,
            "comments": [],
            "done_processed": False,
            "project_key": proj,
            "created_at": now,
            "updated_at": now,
        }
        self._tickets.document(ticket_id).set(ticket)
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
        allowed = {
            "title",
            "description",
            "status",
            "assignee",
            "fix_version",
            "thread_id",
            "marketing",
            "parent_key",
            "done_processed",
        }
        updates: Dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "status":
                st = (value or "").strip()
                if not is_valid_status(st, project_key=proj):
                    continue
                updates["status"] = st
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
            elif key == "marketing":
                updates["marketing"] = bool(value)
            elif key == "parent_key":
                pk = (value or "").strip().upper() or None
                updates["parent_key"] = pk if pk and _ISSUE_KEY_RE.match(pk) else None
            elif key == "done_processed":
                updates["done_processed"] = bool(value)
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
        ref.delete()
        return True

    def add_comment(self, ticket_id: str, body: str) -> Optional[Dict[str, Any]]:
        from google.cloud.firestore import ArrayUnion

        text = (body or "").strip()
        if not text:
            return None
        ref = self._tickets.document(ticket_id)
        snap = ref.get()
        if not snap.exists:
            return None
        now = _utcnow_iso()
        comment = {
            "id": str(uuid.uuid4()),
            "body": text,
            "created_at": now,
        }
        ref.update({"comments": ArrayUnion([comment]), "updated_at": now})
        return comment

    def list_comments(self, ticket_id: str) -> List[Dict[str, Any]]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return []
        return list(ticket.get("comments") or [])

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
