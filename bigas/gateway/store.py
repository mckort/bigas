"""Storage for the agent gateway: agents, tokens, approvals, counters, audit, settings.

Firestore in production (collections prefixed ``gateway_``), in-memory for
tests and local runs. Chosen like the OAuth store: ``CHAT_STORAGE_MODE=memory``
or no Google project id → memory.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

AUDIT_RETENTION_SECONDS = 30 * 24 * 3600


def _now() -> float:
    return time.time()


def _ts(seconds: float):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def window_key(name: str, agent_id: str, window_seconds: int, now: Optional[float] = None) -> str:
    start = int((now if now is not None else _now()) // window_seconds) * window_seconds
    return f"{agent_id}:{name}:{start}"


class MemoryGatewayStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._agents: Dict[str, dict] = {}
        self._tokens: Dict[str, str] = {}
        self._approvals: Dict[str, dict] = {}
        self._counters: Dict[str, int] = {}
        self._audit: Dict[str, List[dict]] = {}
        self._settings: dict = {}

    # Agents
    def put_agent(self, agent: dict) -> None:
        with self._lock:
            self._agents[agent["id"]] = copy.deepcopy(agent)

    def get_agent(self, agent_id: str) -> Optional[dict]:
        with self._lock:
            a = self._agents.get(agent_id)
            return copy.deepcopy(a) if a else None

    def list_agents(self) -> List[dict]:
        with self._lock:
            return [copy.deepcopy(a) for a in self._agents.values()]

    def delete_agent(self, agent_id: str) -> None:
        with self._lock:
            self._agents.pop(agent_id, None)
            self._audit.pop(agent_id, None)

    # Tokens (sha256 → agent id)
    def put_token(self, token_hash: str, agent_id: str) -> None:
        with self._lock:
            self._tokens[token_hash] = agent_id

    def get_token(self, token_hash: str) -> Optional[str]:
        with self._lock:
            return self._tokens.get(token_hash)

    def delete_token(self, token_hash: str) -> None:
        with self._lock:
            self._tokens.pop(token_hash, None)

    # Approvals
    def put_approval(self, approval: dict) -> None:
        with self._lock:
            self._approvals[approval["id"]] = copy.deepcopy(approval)

    def get_approval(self, approval_id: str) -> Optional[dict]:
        with self._lock:
            a = self._approvals.get(approval_id)
            return copy.deepcopy(a) if a else None

    def list_approvals(
        self, *, status: Optional[str] = None, agent_id: Optional[str] = None, limit: int = 100
    ) -> List[dict]:
        with self._lock:
            items = [
                copy.deepcopy(a)
                for a in self._approvals.values()
                if (status is None or a.get("status") == status)
                and (agent_id is None or a.get("agent_id") == agent_id)
            ]
        items.sort(key=lambda a: a.get("created_at") or 0, reverse=True)
        return items[:limit]

    # Counters
    def incr_counter(self, key: str, amount: int = 1) -> int:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount
            return self._counters[key]

    def get_counter(self, key: str) -> int:
        with self._lock:
            return self._counters.get(key, 0)

    # Audit
    def add_audit(self, agent_id: str, entry: dict) -> None:
        cutoff = _now() - AUDIT_RETENTION_SECONDS
        with self._lock:
            rows = self._audit.setdefault(agent_id, [])
            rows.append(copy.deepcopy(entry))
            self._audit[agent_id] = [r for r in rows if (r.get("ts") or 0) >= cutoff][-1000:]

    def list_audit(self, agent_id: str, limit: int = 50) -> List[dict]:
        cutoff = _now() - AUDIT_RETENTION_SECONDS
        with self._lock:
            rows = [copy.deepcopy(r) for r in self._audit.get(agent_id, []) if (r.get("ts") or 0) >= cutoff]
        rows.sort(key=lambda r: r.get("ts") or 0, reverse=True)
        return rows[:limit]

    # Settings
    def get_settings(self) -> dict:
        with self._lock:
            return dict(self._settings)

    def put_settings(self, settings: dict) -> None:
        with self._lock:
            self._settings = dict(settings)


class FirestoreGatewayStore:
    def __init__(self, project_id: str):
        from google.cloud import firestore

        self._firestore = firestore
        self._db = firestore.Client(project=project_id or None)
        self._agents = self._db.collection("gateway_agents")
        self._tokens = self._db.collection("gateway_tokens")
        self._approvals = self._db.collection("gateway_approvals")
        self._counters = self._db.collection("gateway_counters")
        self._settings = self._db.collection("gateway_settings").document("global")

    def put_agent(self, agent: dict) -> None:
        self._agents.document(agent["id"]).set(agent)

    def get_agent(self, agent_id: str) -> Optional[dict]:
        snap = self._agents.document(agent_id).get()
        return snap.to_dict() if snap.exists else None

    def list_agents(self) -> List[dict]:
        return [d.to_dict() for d in self._agents.stream()]

    def delete_agent(self, agent_id: str) -> None:
        ref = self._agents.document(agent_id)
        for row in ref.collection("gateway_audit").limit(500).stream():
            row.reference.delete()
        ref.delete()

    def put_token(self, token_hash: str, agent_id: str) -> None:
        self._tokens.document(token_hash).set({"agent_id": agent_id, "created_at": _now()})

    def get_token(self, token_hash: str) -> Optional[str]:
        snap = self._tokens.document(token_hash).get()
        if not snap.exists:
            return None
        return (snap.to_dict() or {}).get("agent_id")

    def delete_token(self, token_hash: str) -> None:
        self._tokens.document(token_hash).delete()

    def put_approval(self, approval: dict) -> None:
        self._approvals.document(approval["id"]).set(approval)

    def get_approval(self, approval_id: str) -> Optional[dict]:
        snap = self._approvals.document(approval_id).get()
        return snap.to_dict() if snap.exists else None

    def list_approvals(
        self, *, status: Optional[str] = None, agent_id: Optional[str] = None, limit: int = 100
    ) -> List[dict]:
        # Equality filters only: no composite index needed. Sorted here.
        query = self._approvals
        if status is not None:
            query = query.where("status", "==", status)
        if agent_id is not None:
            query = query.where("agent_id", "==", agent_id)
        items = [d.to_dict() for d in query.limit(500).stream()]
        items.sort(key=lambda a: a.get("created_at") or 0, reverse=True)
        return items[:limit]

    def incr_counter(self, key: str, amount: int = 1) -> int:
        ref = self._counters.document(key.replace("/", "_"))
        ref.set(
            {"count": self._firestore.Increment(amount), "expire_at": _ts(_now() + 2 * 86400)},
            merge=True,
        )
        snap = ref.get()
        return int((snap.to_dict() or {}).get("count") or 0)

    def get_counter(self, key: str) -> int:
        snap = self._counters.document(key.replace("/", "_")).get()
        if not snap.exists:
            return 0
        return int((snap.to_dict() or {}).get("count") or 0)

    def add_audit(self, agent_id: str, entry: dict) -> None:
        row = dict(entry)
        # A Firestore TTL policy on `expire_at` deletes old rows (see docs/agent-gateway.md).
        row["expire_at"] = _ts((entry.get("ts") or _now()) + AUDIT_RETENTION_SECONDS)
        self._agents.document(agent_id).collection("gateway_audit").add(row)

    def list_audit(self, agent_id: str, limit: int = 50) -> List[dict]:
        cutoff = _now() - AUDIT_RETENTION_SECONDS
        query = (
            self._agents.document(agent_id)
            .collection("gateway_audit")
            .order_by("ts", direction=self._firestore.Query.DESCENDING)
            .limit(limit)
        )
        rows = []
        for d in query.stream():
            row = d.to_dict() or {}
            row.pop("expire_at", None)
            if (row.get("ts") or 0) >= cutoff:
                rows.append(row)
        return rows

    def get_settings(self) -> dict:
        snap = self._settings.get()
        return snap.to_dict() if snap.exists else {}

    def put_settings(self, settings: dict) -> None:
        self._settings.set(settings)


_store: Optional[Any] = None
_store_lock = threading.Lock()


def reset_gateway_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None


def get_gateway_store():
    global _store
    with _store_lock:
        if _store is not None:
            return _store
        storage_mode = (os.environ.get("CHAT_STORAGE_MODE") or "").strip().lower()
        project_id = (
            os.environ.get("FIREBASE_PROJECT_ID")
            or os.environ.get("GOOGLE_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCLOUD_PROJECT")
            or ""
        ).strip()
        if storage_mode == "memory" or (storage_mode != "firestore" and not project_id):
            _store = MemoryGatewayStore()
        else:
            try:
                _store = FirestoreGatewayStore(project_id)
            except Exception:
                logger.exception("Gateway Firestore store failed (project_id=%r); using memory", project_id)
                _store = MemoryGatewayStore()
        return _store
