"""Import Jira issues onto the internal board, merging labels."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import requests

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    adf_to_plain_text,
)
from bigas.tickets.config import jira_configured
from bigas.tickets.constants import columns_for_board
from bigas.tickets.labels import merge_labels, resolve_ticket_labels
from bigas.tickets.service import TicketService
from bigas.tickets.store import get_ticket_store

logger = logging.getLogger(__name__)

_IMPORT_FIELDS = [
    "summary",
    "description",
    "status",
    "issuetype",
    "labels",
    "parent",
    "project",
    "assignee",
    "fixVersions",
]


class JiraImportError(RuntimeError):
    pass


def _jira_sync_worker_url(board_id: str) -> str:
    port = (os.environ.get("PORT") or "8080").strip() or "8080"
    return f"http://127.0.0.1:{port}/api/boards/{board_id}/sync-jira-worker"


def _request_authorization() -> str:
    try:
        from flask import has_request_context, request

        if has_request_context():
            return (request.headers.get("Authorization") or "").strip()
    except Exception:
        pass
    return ""


def _jira_sync_dispatch_headers() -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "bigas-core/1.0 (jira-sync-worker)",
    }
    auth = _request_authorization()
    if auth:
        headers["Authorization"] = auth
    return headers


def _should_run_jira_sync_inline() -> bool:
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get("TESTING"):
            return True
    except Exception:
        pass
    return not _request_authorization()


def dispatch_jira_board_sync(*, user_id: str, board_id: str) -> Dict[str, Any]:
    """Start Jira board sync via loopback worker so the HTTP request returns quickly."""
    if _should_run_jira_sync_inline():
        return sync_jira_board(user_id=user_id, board_id=board_id)

    try:
        requests.post(
            _jira_sync_worker_url(board_id),
            json={},
            headers=_jira_sync_dispatch_headers(),
            timeout=(10, 1),
        )
    except requests.exceptions.ReadTimeout:
        return {"ok": True, "status": "started"}
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.ConnectTimeout,
    ) as exc:
        logger.warning(
            "Jira sync worker dispatch failed for board %s; running inline: %s",
            board_id,
            exc,
        )
        return sync_jira_board(user_id=user_id, board_id=board_id)
    except requests.exceptions.RequestException as exc:
        logger.warning(
            "Jira sync worker dispatch failed for board %s: %s",
            board_id,
            exc,
        )
        raise JiraImportError("Could not start Jira sync") from exc

    return {"ok": True, "status": "started"}


def _map_issue_type(name: str) -> str:
    lowered = (name or "").strip().lower()
    if lowered == "bug":
        return "Bug"
    if lowered == "epic":
        return "Epic"
    return "Task"


def _map_status(jira_status: str, project_key: str) -> str:
    cols = columns_for_board(project_key=project_key)
    lookup = {col.lower(): col for col in cols}
    mapped = lookup.get((jira_status or "").strip().lower())
    return mapped or cols[0]


def _issue_parent_key(fields: Dict[str, Any]) -> Optional[str]:
    parent = fields.get("parent") if isinstance(fields.get("parent"), dict) else {}
    key = (parent.get("key") or "").strip().upper()
    return key or None


def _issue_assignee(fields: Dict[str, Any]) -> Optional[str]:
    assignee = fields.get("assignee") if isinstance(fields.get("assignee"), dict) else {}
    name = (assignee.get("displayName") or assignee.get("emailAddress") or "").strip()
    return name or None


def _issue_fix_version(fields: Dict[str, Any]) -> Optional[str]:
    versions = fields.get("fixVersions") or []
    for item in versions:
        if isinstance(item, dict):
            name = (item.get("name") or "").strip()
            if name:
                return name
        elif str(item).strip():
            return str(item).strip()
    return None


def sync_jira_board(
    *,
    user_id: str,
    board_id: str,
    jira: Optional[JiraClient] = None,
) -> Dict[str, Any]:
    if not jira_configured() and jira is None:
        raise JiraImportError("Jira is not configured")

    store = get_ticket_store()
    board = store.get_board(board_id)
    if not board or board.get("user_id") != user_id:
        raise JiraImportError("Board not found")
    project_key = (board.get("project_key") or "").strip().upper()
    if not project_key:
        raise JiraImportError("Personal boards cannot import from Jira")

    client = jira or JiraClient(JiraConfig.from_env())
    try:
        issues = client.search_issues_for_projects([project_key], fields=_IMPORT_FIELDS)
    except JiraError as exc:
        raise JiraImportError(str(exc)) from exc

    service = TicketService()
    created = 0
    updated = 0
    skipped = 0
    scanned = 0

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        key = (issue.get("key") or "").strip().upper()
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        if not key:
            continue
        scanned += 1
        labels = fields.get("labels") or []
        existing = store.get_ticket_by_key(key)
        if existing:
            if existing.get("board_id") != board_id:
                skipped += 1
                continue
            # Existing tickets: label-only sync. Title, status, and description
            # stay as edited on the board unless the ticket is newly imported.
            merged = merge_labels(resolve_ticket_labels(existing), labels)
            if merged == resolve_ticket_labels(existing):
                continue
            service.update_ticket(
                existing["ticket_id"],
                user_id=user_id,
                previous_status=existing.get("status"),
                labels=merged,
            )
            updated += 1
            continue

        itype = _map_issue_type(((fields.get("issuetype") or {}).get("name") or ""))
        status_name = ((fields.get("status") or {}).get("name") or "").strip()
        service.create_ticket(
            board_id,
            user_id=user_id,
            title=(fields.get("summary") or key).strip() or key,
            description=adf_to_plain_text(fields.get("description")),
            status=_map_status(status_name, project_key),
            issue_type=itype,
            assignee=_issue_assignee(fields),
            fix_version=_issue_fix_version(fields),
            labels=labels,
            parent_key=_issue_parent_key(fields),
            key=key,
        )
        created += 1

    logger.info(
        "Jira import for %s on %s: scanned=%s created=%s updated=%s skipped=%s",
        project_key,
        board_id,
        scanned,
        created,
        updated,
        skipped,
    )
    return {
        "ok": True,
        "project_key": project_key,
        "scanned": scanned,
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }
