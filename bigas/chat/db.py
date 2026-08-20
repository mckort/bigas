"""Chat persistence: Firestore in production, in-memory fallback for local dev/tests."""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DEFAULT_AGENTS = [
    {
        "agent_id": "chief",
        "name": "Chief of Staff",
        "icon": "👔",
        "system_prompt_goals": (
            "You are the Chief of Staff for Bigas. You coordinate the virtual AI team across "
            "all Jira projects and GitHub repos in the portfolio (VFA, WAYW, BIG, REM, GPWW, FYDA, MYL), "
            "answer general questions directly, and delegate domain-specific work to "
            "Marketing, Product, CTO, or DevOps specialists when appropriate. Monitor task progress "
            "and summarize results clearly for the user."
        ),
    },
    {
        "agent_id": "marketing",
        "name": "Marketing Analyst",
        "icon": "📊",
        "system_prompt_goals": (
            "You are the Senior Marketing Analyst for Bigas. Your goals include GA4 analytics "
            "for each site that has a property in BIGAS_GA4_PROPERTY_MAP (Green Promo Wear / GPWW "
            "is configured by default; other brands need their own property IDs), "
            "paid ads reporting across Google/Meta/LinkedIn/Reddit, trend analysis, and "
            "cross-platform marketing insights. Always pass project_key when the user names a brand. "
            "Use available analytics tools to answer questions."
        ),
    },
    {
        "agent_id": "product",
        "name": "Product Manager",
        "icon": "🧠",
        "system_prompt_goals": (
            "You are the Product Manager for Bigas. You cover every Jira board in the portfolio "
            "(VFA, WAYW, BIG, REM, GPWW, FYDA, MYL) and the mapped GitHub repos. "
            "Your goals include Jira automation, release notes, progress updates, and social content drafts. "
            "Help with product planning, issue tracking, and team communication. "
            "When the user wants new development work, tell them to create or drag a Jira card "
            "in the matching project so Bigas can research, plan, and open a PR."
        ),
    },
    {
        "agent_id": "cto",
        "name": "CTO",
        "icon": "</>",
        "system_prompt_goals": (
            "You are the CTO for Bigas. You review and improve code across all mapped GitHub repos "
            "(vcfieldassistant, roadpal, bigas, remotebrief, greenpromowear-website, "
            "fulfillyourdreamadventure, mylifesdeed). "
            "Your goals include GitHub PR review, autofix workflows, AI usage reporting, and website monitoring. "
            "When the user shares a GitHub PR URL, call review_and_comment_pr with that URL as pr_url "
            "(or repo + pr_number extracted from it). "
            "Focus on code quality, engineering operations, and technical leadership."
        ),
    },
    {
        "agent_id": "devops",
        "name": "DevOps",
        "icon": "🚀",
        "system_prompt_goals": (
            "You are the DevOps specialist for Bigas. You deploy production sites across the portfolio "
            "(e.g. vcfieldassistant.com) by triggering GitHub Actions workflows — never SSH directly. "
            "When the user asks to deploy, a pipeline posts progress in this chat: pre-check "
            "(prod version vs the code about to ship), then deploy, then post-check. "
            "For other questions (status of an existing run, health check only), use the matching tool. "
            "Ask for confirmation when risk_level is high or medium before triggering. "
            "vcfieldassistant uses separate backend and web workflows."
        ),
    },
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryChatStore:
    """Thread-safe in-memory store for tests and local development."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: Dict[str, Dict[str, Any]] = {}
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._threads: Dict[str, Dict[str, Any]] = {}
        self._messages: Dict[str, List[Dict[str, Any]]] = {}
        self._activity: List[Dict[str, Any]] = []

    def seed_agents(self) -> None:
        with self._lock:
            for agent in DEFAULT_AGENTS:
                aid = agent["agent_id"]
                if aid not in self._agents:
                    self._agents[aid] = {**agent, "updated_at": _utcnow_iso()}
                else:
                    self._agents[aid]["icon"] = agent["icon"]

    def upsert_user(self, uid: str, email: str) -> Dict[str, Any]:
        with self._lock:
            user = self._users.get(uid) or {"uid": uid, "email": email, "created_at": _utcnow_iso()}
            user["email"] = email
            self._users[uid] = user
            return dict(user)

    def list_agents(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(a) for a in self._agents.values()]

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            agent = self._agents.get(agent_id)
            return dict(agent) if agent else None

    def update_agent(self, agent_id: str, *, name: str, system_prompt_goals: str) -> Dict[str, Any]:
        with self._lock:
            existing = self._agents.get(agent_id) or {"agent_id": agent_id, "icon": "🤖"}
            existing.update(
                {
                    "name": name,
                    "system_prompt_goals": system_prompt_goals,
                    "updated_at": _utcnow_iso(),
                }
            )
            self._agents[agent_id] = existing
            return dict(existing)

    def create_thread(self, user_id: str, agent_id: str) -> Dict[str, Any]:
        thread_id = str(uuid.uuid4())
        now = _utcnow_iso()
        thread = {
            "thread_id": thread_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }
        with self._lock:
            self._threads[thread_id] = thread
            self._messages[thread_id] = []
        return dict(thread)

    def list_threads(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            threads = [t for t in self._threads.values() if t.get("user_id") == user_id]
            return sorted(threads, key=lambda t: t.get("updated_at", ""), reverse=True)

    def get_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            thread = self._threads.get(thread_id)
            return dict(thread) if thread else None

    def touch_thread(self, thread_id: str) -> None:
        with self._lock:
            if thread_id in self._threads:
                self._threads[thread_id]["updated_at"] = _utcnow_iso()

    def patch_thread(self, thread_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            thread = self._threads.get(thread_id)
            if not thread:
                return None
            thread.update(fields)
            thread["updated_at"] = _utcnow_iso()
            return dict(thread)

    def add_message(
        self,
        thread_id: str,
        *,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        message = {
            "message_id": str(uuid.uuid4()),
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "created_at": _utcnow_iso(),
            "metadata": metadata or {},
        }
        with self._lock:
            self._messages.setdefault(thread_id, []).append(message)
            if thread_id in self._threads:
                self._threads[thread_id]["updated_at"] = message["created_at"]
                self._threads[thread_id]["message_count"] = int(self._threads[thread_id].get("message_count") or 0) + 1
        return dict(message)

    def list_messages(self, thread_id: str, *, since: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            messages = list(self._messages.get(thread_id, []))
        if since:
            messages = [m for m in messages if m.get("created_at", "") > since]
        return messages

    def add_activity(self, *, type_: str, content: str, source: str = "system") -> Dict[str, Any]:
        event = {
            "id": str(uuid.uuid4()),
            "type": type_,
            "content": content,
            "source": source,
            "created_at": _utcnow_iso(),
        }
        with self._lock:
            self._activity.append(event)
            if len(self._activity) > 500:
                self._activity = self._activity[-500:]
        return dict(event)

    def list_activity(self, *, since: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._activity)
        if since:
            events = [e for e in events if e.get("created_at", "") > since]
        return list(reversed(events[-limit:]))


class FirestoreChatStore:
    """Firestore-backed chat store for production."""

    def __init__(self, project_id: str) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=project_id)
        self._users = self._db.collection("users")
        self._agents = self._db.collection("agent_configs")
        self._threads = self._db.collection("threads")
        self._messages = self._db.collection("messages")
        self._activity = self._db.collection("activity_feed")

    def seed_agents(self) -> None:
        for agent in DEFAULT_AGENTS:
            doc = self._agents.document(agent["agent_id"])
            snap = doc.get()
            if not snap.exists:
                doc.set({**agent, "updated_at": _utcnow_iso()})
                continue
            existing_icon = (snap.to_dict() or {}).get("icon")
            if existing_icon != agent["icon"]:
                doc.update({"icon": agent["icon"]})

    def upsert_user(self, uid: str, email: str) -> Dict[str, Any]:
        doc = self._users.document(uid)
        snap = doc.get()
        if snap.exists:
            data = snap.to_dict() or {}
            data["email"] = email
            doc.update({"email": email})
            return data
        user = {"uid": uid, "email": email, "created_at": _utcnow_iso()}
        doc.set(user)
        return user

    def list_agents(self) -> List[Dict[str, Any]]:
        return [doc.to_dict() for doc in self._agents.stream() if doc.exists]

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        snap = self._agents.document(agent_id).get()
        return snap.to_dict() if snap.exists else None

    def update_agent(self, agent_id: str, *, name: str, system_prompt_goals: str) -> Dict[str, Any]:
        doc = self._agents.document(agent_id)
        payload = {
            "agent_id": agent_id,
            "name": name,
            "system_prompt_goals": system_prompt_goals,
            "updated_at": _utcnow_iso(),
        }
        snap = doc.get()
        if snap.exists:
            existing = snap.to_dict() or {}
            payload["icon"] = existing.get("icon", "🤖")
            doc.update(payload)
        else:
            payload["icon"] = "🤖"
            doc.set(payload)
        return payload

    def create_thread(self, user_id: str, agent_id: str) -> Dict[str, Any]:
        thread_id = str(uuid.uuid4())
        now = _utcnow_iso()
        thread = {
            "thread_id": thread_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }
        self._threads.document(thread_id).set(thread)
        return thread

    def list_threads(self, user_id: str) -> List[Dict[str, Any]]:
        docs = self._threads.where("user_id", "==", user_id).stream()
        threads = [doc.to_dict() for doc in docs if doc.exists]
        return sorted(threads, key=lambda t: t.get("updated_at", ""), reverse=True)

    def get_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        snap = self._threads.document(thread_id).get()
        return snap.to_dict() if snap.exists else None

    def touch_thread(self, thread_id: str) -> None:
        self._threads.document(thread_id).update({"updated_at": _utcnow_iso()})

    def patch_thread(self, thread_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        payload = {**fields, "updated_at": _utcnow_iso()}
        self._threads.document(thread_id).update(payload)
        return self.get_thread(thread_id)

    def add_message(
        self,
        thread_id: str,
        *,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        message_id = str(uuid.uuid4())
        now = _utcnow_iso()
        message = {
            "message_id": message_id,
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "created_at": now,
            "metadata": metadata or {},
        }
        from google.cloud import firestore

        batch = self._db.batch()
        batch.set(self._messages.document(message_id), message)
        batch.update(
            self._threads.document(thread_id),
            {"updated_at": now, "message_count": firestore.Increment(1)},
        )
        batch.commit()
        return message

    def list_messages(self, thread_id: str, *, since: Optional[str] = None) -> List[Dict[str, Any]]:
        messages = [
            doc.to_dict()
            for doc in self._messages.where("thread_id", "==", thread_id).stream()
            if doc.exists
        ]
        if since:
            messages = [m for m in messages if m.get("created_at", "") > since]
        return sorted(messages, key=lambda m: m.get("created_at", ""))

    def add_activity(self, *, type_: str, content: str, source: str = "system") -> Dict[str, Any]:
        event_id = str(uuid.uuid4())
        event = {
            "id": event_id,
            "type": type_,
            "content": content,
            "source": source,
            "created_at": _utcnow_iso(),
        }
        self._activity.document(event_id).set(event)
        return event

    def list_activity(self, *, since: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        query = self._activity.order_by("created_at", direction="DESCENDING").limit(limit)
        events = [doc.to_dict() for doc in query.stream() if doc.exists]
        if since:
            events = [e for e in events if e.get("created_at", "") > since]
        return events


_store: Optional[Any] = None
_store_lock = threading.Lock()


def get_chat_store():
    """Return singleton chat store (Firestore or in-memory)."""
    global _store
    with _store_lock:
        if _store is not None:
            return _store

        storage_mode = (os.environ.get("CHAT_STORAGE_MODE") or "").strip().lower()
        project_id = (os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_PROJECT_ID") or "").strip()

        if storage_mode == "memory" or (storage_mode != "firestore" and not project_id):
            _store = MemoryChatStore()
        else:
            try:
                _store = FirestoreChatStore(project_id)
            except Exception:
                _store = MemoryChatStore()

        _store.seed_agents()
        return _store
