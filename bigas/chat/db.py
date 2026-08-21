"""Chat persistence: Firestore in production, in-memory fallback for local dev/tests."""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

DEFAULT_ACTIVITY_KEEP_DAYS = 7
DEFAULT_ACTIVITY_MAX_DELETE = 500

DEFAULT_AGENTS = [
    {
        "agent_id": "chief",
        "name": "Chief of Staff",
        "icon": "👔",
        "system_prompt_goals": (
            "You are the Chief of Staff for Bigas. You coordinate the virtual AI team across "
            "all Jira projects and GitHub repos in the portfolio (VFA, WAYW, BIG, REM, GPWW, FYDA, MYL), "
            "answer general questions directly, and delegate domain-specific work to "
            "Marketing, Product, CTO, or DevOps specialists when appropriate. "
            "DevOps owns production deploys (GitHub Actions, including a manual trigger) "
            "and site health; never claim deploy cannot be done from chat. "
            "Monitor task progress and summarize results clearly for the user."
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
            "Your goals include GitHub PR review, autofix workflows, fixing failed production "
            "deploys (fix_failed_deployment), AI usage reporting, and website monitoring. "
            "When a GitHub Actions deploy fails, call fix_failed_deployment with repo and run_id "
            "so a Cursor cloud agent can open a fix PR. "
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
            "If a workflow fails, use fetch_github_action_logs to inspect the failure, analyze the "
            "commit diff, and open a hotfix PR with create_github_pr on a bigas-hotfix/* branch. "
            "For failed production deploys you may also call fix_failed_deployment (Cursor agent). "
            "For other questions (status of an existing run, health check only), use the matching tool. "
            "Ask for confirmation when risk_level is high or medium before triggering. "
            "vcfieldassistant uses separate backend and web workflows."
        ),
    },
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _activity_cutoff_iso(keep_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()


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

    def patch_message(self, message_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            for messages in self._messages.values():
                for message in messages:
                    if message.get("message_id") != message_id:
                        continue
                    patch = dict(fields)
                    if "metadata" in patch and isinstance(patch["metadata"], dict):
                        message["metadata"] = {
                            **(message.get("metadata") or {}),
                            **patch["metadata"],
                        }
                        patch = {k: v for k, v in patch.items() if k != "metadata"}
                    message.update(patch)
                    return dict(message)
        return None

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
                thread = self._threads[thread_id]
                thread["updated_at"] = message["created_at"]
                thread["message_count"] = int(thread.get("message_count") or 0) + 1
                thread["last_message_role"] = role
                if role != "user":
                    thread["last_incoming_at"] = message["created_at"]
        return dict(message)

    def list_messages(self, thread_id: str, *, since: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            messages = list(self._messages.get(thread_id, []))
        if since:
            messages = [m for m in messages if m.get("created_at", "") > since]
        return messages

    def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for msgs in self._messages.values():
                for m in msgs:
                    if m.get("message_id") == message_id:
                        return dict(m)
        return None

    def update_message_metadata(self, message_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            for msgs in self._messages.values():
                for m in msgs:
                    if m.get("message_id") == message_id:
                        meta = dict(m.get("metadata") or {})
                        meta.update(updates)
                        m["metadata"] = meta
                        return dict(m)
        return None

    def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        target = (email or "").strip().lower()
        if not target:
            return None
        with self._lock:
            for user in self._users.values():
                if (user.get("email") or "").strip().lower() == target:
                    return dict(user)
        return None

    def claim_proposal_for_approval(
        self,
        message_id: str,
        *,
        proposal_id: str,
        user_id: str,
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        with self._lock:
            message = self.get_message(message_id)
            if not message:
                return None, "not_found"
            meta = message.get("metadata") or {}
            if meta.get("type") != "action_proposal":
                return None, "invalid"
            if meta.get("proposal_id") != proposal_id:
                return None, "mismatch"
            if meta.get("status") != "pending":
                return None, "not_pending"
            thread = self.get_thread(message.get("thread_id") or "")
            if not thread or thread.get("user_id") != user_id:
                return None, "forbidden"
            for msgs in self._messages.values():
                for m in msgs:
                    if m.get("message_id") == message_id:
                        m_meta = dict(m.get("metadata") or {})
                        m_meta["status"] = "processing"
                        m["metadata"] = m_meta
                        message = dict(m)
                        break
            return message, None

    def get_or_create_agent_thread(self, user_id: str, agent_id: str) -> Dict[str, Any]:
        with self._lock:
            matches = [
                t
                for t in self._threads.values()
                if t.get("user_id") == user_id and t.get("agent_id") == agent_id
            ]
            if matches:
                matches.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
                return dict(matches[0])
            return self.create_thread(user_id, agent_id)

    def get_or_create_chief_thread(self, user_id: str) -> Dict[str, Any]:
        return self.get_or_create_agent_thread(user_id, "chief")

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

    def delete_old_activity(
        self,
        *,
        keep_days: int = DEFAULT_ACTIVITY_KEEP_DAYS,
        max_to_delete: int = DEFAULT_ACTIVITY_MAX_DELETE,
    ) -> int:
        cutoff = _activity_cutoff_iso(keep_days)
        with self._lock:
            stale = [e for e in self._activity if e.get("created_at", "") < cutoff]
            stale.sort(key=lambda e: e.get("created_at", ""))
            to_delete_ids = {e.get("id") for e in stale[:max_to_delete]}
            if not to_delete_ids:
                return 0
            self._activity = [e for e in self._activity if e.get("id") not in to_delete_ids]
            return len(to_delete_ids)


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
        self._agent_thread_index = self._db.collection("agent_thread_index")

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
        ref = self._threads.document(thread_id)
        if not ref.get().exists:
            return None
        payload = {**fields, "updated_at": _utcnow_iso()}
        ref.update(payload)
        return self.get_thread(thread_id)

    def patch_message(self, message_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        ref = self._messages.document(message_id)
        snap = ref.get()
        if not snap.exists:
            return None
        existing = snap.to_dict() or {}
        payload = dict(fields)
        if "metadata" in payload and isinstance(payload["metadata"], dict):
            payload["metadata"] = {**(existing.get("metadata") or {}), **payload["metadata"]}
        ref.update(payload)
        updated = ref.get()
        return updated.to_dict() if updated.exists else None

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
        payload = {
            "updated_at": now,
            "message_count": firestore.Increment(1),
            "last_message_role": role,
        }
        if role != "user":
            payload["last_incoming_at"] = now
        batch.update(self._threads.document(thread_id), payload)
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

    def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        snap = self._messages.document(message_id).get()
        return snap.to_dict() if snap.exists else None

    def update_message_metadata(self, message_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        doc = self._messages.document(message_id)
        snap = doc.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        meta = dict(data.get("metadata") or {})
        meta.update(updates)
        doc.update({"metadata": meta})
        data["metadata"] = meta
        return data

    def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        target = (email or "").strip()
        if not target:
            return None
        docs = self._users.where("email", "==", target).limit(1).stream()
        for doc in docs:
            if doc.exists:
                return doc.to_dict()
        return None

    def claim_proposal_for_approval(
        self,
        message_id: str,
        *,
        proposal_id: str,
        user_id: str,
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        from google.cloud import firestore

        doc_ref = self._messages.document(message_id)

        @firestore.transactional
        def _claim(transaction):
            snap = doc_ref.get(transaction=transaction)
            if not snap.exists:
                return None, "not_found"
            data = snap.to_dict() or {}
            meta = dict(data.get("metadata") or {})
            if meta.get("type") != "action_proposal":
                return None, "invalid"
            if meta.get("proposal_id") != proposal_id:
                return None, "mismatch"
            if meta.get("status") != "pending":
                return None, "not_pending"
            thread_id = data.get("thread_id") or ""
            thread_snap = self._threads.document(thread_id).get(transaction=transaction)
            if not thread_snap.exists:
                return None, "forbidden"
            thread = thread_snap.to_dict() or {}
            if thread.get("user_id") != user_id:
                return None, "forbidden"
            meta["status"] = "processing"
            transaction.update(doc_ref, {"metadata": meta})
            data["metadata"] = meta
            return data, None

        transaction = self._db.transaction()
        return _claim(transaction)

    def get_or_create_agent_thread(self, user_id: str, agent_id: str) -> Dict[str, Any]:
        from google.cloud import firestore

        index_ref = self._agent_thread_index.document(f"{user_id}__{agent_id}")

        @firestore.transactional
        def _get_or_create(transaction):
            index_snap = index_ref.get(transaction=transaction)
            if index_snap.exists:
                thread_id = (index_snap.to_dict() or {}).get("thread_id")
                if thread_id:
                    thread_snap = self._threads.document(thread_id).get(transaction=transaction)
                    if thread_snap.exists:
                        return thread_snap.to_dict()

            query = (
                self._threads.where("user_id", "==", user_id).where("agent_id", "==", agent_id)
            )
            threads = [doc.to_dict() for doc in query.stream(transaction=transaction) if doc.exists]
            if threads:
                threads.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
                best = threads[0]
                transaction.set(
                    index_ref,
                    {
                        "thread_id": best["thread_id"],
                        "user_id": user_id,
                        "agent_id": agent_id,
                    },
                )
                return best

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
            transaction.set(self._threads.document(thread_id), thread)
            transaction.set(
                index_ref,
                {"thread_id": thread_id, "user_id": user_id, "agent_id": agent_id},
            )
            return thread

        transaction = self._db.transaction()
        return _get_or_create(transaction)

    def get_or_create_chief_thread(self, user_id: str) -> Dict[str, Any]:
        return self.get_or_create_agent_thread(user_id, "chief")

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

    def delete_old_activity(
        self,
        *,
        keep_days: int = DEFAULT_ACTIVITY_KEEP_DAYS,
        max_to_delete: int = DEFAULT_ACTIVITY_MAX_DELETE,
    ) -> int:
        cutoff = _activity_cutoff_iso(keep_days)
        docs = list(
            self._activity.where("created_at", "<", cutoff)
            .order_by("created_at")
            .limit(max_to_delete)
            .stream()
        )
        if not docs:
            return 0
        batch = self._db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        return len(docs)


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
