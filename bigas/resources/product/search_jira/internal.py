"""Subset JQL matching for the internal Bigas board (no Jira Cloud required)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from bigas.tickets.labels import resolve_ticket_labels
from bigas.tickets.service import ticket_url
from bigas.tickets.store import get_ticket_store

from .service import SearchJiraError, extract_jql_project_keys

_ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_TEXT_RE = re.compile(
    r"""\b(?:text|summary|description)\s*~\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_TYPE_RE = re.compile(
    r"""\b(?:type|issuetype)\s*=\s*["']?([A-Za-z]+)["']?""",
    re.IGNORECASE,
)
_STATUS_QUOTED_RE = re.compile(
    r"""\bstatus\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_STATUS_BARE_RE = re.compile(
    r"""\bstatus\s*=\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)(?=\s+(?:AND|OR|ORDER)\b|$)""",
    re.IGNORECASE,
)
_STATUS_CAT_RE = re.compile(
    r"""\bstatusCategory\s*(=|!=)\s*["']?([A-Za-z]+(?:\s+[A-Za-z]+)?)["']?(?=\s+(?:AND|OR|ORDER)\b|$)""",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"""\b(created|updated|resolved|resolutiondate)\s*>=\s*["']?(-?\d+d|\d{4}-\d{2}-\d{2})["']?""",
    re.IGNORECASE,
)
_LABEL_EQ_RE = re.compile(
    r"""\blabels\s*=\s*["']?([A-Za-z0-9._-]+)["']?""",
    re.IGNORECASE,
)
_LABEL_IN_RE = re.compile(
    r"""\blabels\s+in\s*\(([^)]+)\)""",
    re.IGNORECASE,
)

_DONE_STATUSES = frozenset({"done"})
_TODO_STATUSES = frozenset({"to do", "todo", "backlog"})


def _strip_order_by(jql: str) -> str:
    match = _ORDER_BY_RE.search(jql or "")
    if not match:
        return (jql or "").strip()
    return (jql or "")[: match.start()].strip()


def _parse_date_token(raw: str) -> datetime:
    token = (raw or "").strip()
    now = datetime.now(timezone.utc)
    if re.fullmatch(r"-?\d+d", token, re.IGNORECASE):
        days = abs(int(token[:-1]))
        return now - timedelta(days=max(1, days))
    try:
        parsed = datetime.strptime(token[:10], "%Y-%m-%d")
    except ValueError as exc:
        raise SearchJiraError(f"Invalid date in JQL: {token}") from exc
    return parsed.replace(tzinfo=timezone.utc)


def parse_internal_filters(jql: str) -> Dict[str, Any]:
    """Extract a conjunctive subset of JQL that the internal board can apply."""
    body = _strip_order_by(jql)
    filters: Dict[str, Any] = {
        "text": None,
        "issue_type": None,
        "status": None,
        "status_category": None,
        "status_category_not": False,
        "since": {},
        "labels": [],
        "project_keys": extract_jql_project_keys(body),
    }
    text = _TEXT_RE.search(body)
    if text:
        filters["text"] = text.group(1).strip().lower()
    itype = _TYPE_RE.search(body)
    if itype:
        filters["issue_type"] = itype.group(1).strip()
    status = _STATUS_QUOTED_RE.search(body) or _STATUS_BARE_RE.search(body)
    if status:
        filters["status"] = status.group(1).strip()
    cat = _STATUS_CAT_RE.search(body)
    if cat:
        filters["status_category_not"] = cat.group(1) == "!="
        filters["status_category"] = cat.group(2).strip()
    for match in _DATE_RE.finditer(body):
        field = match.group(1).lower()
        if field == "resolutiondate":
            field = "resolved"
        filters["since"][field] = _parse_date_token(match.group(2))
    labels: List[str] = []
    eq = _LABEL_EQ_RE.search(body)
    if eq:
        labels.append(eq.group(1).strip().lower())
    inn = _LABEL_IN_RE.search(body)
    if inn:
        for part in inn.group(1).split(","):
            token = part.strip().strip("\"'").lower()
            if token:
                labels.append(token)
    filters["labels"] = labels
    return filters


def _status_category(status: str) -> str:
    lower = (status or "").strip().lower()
    if lower in _DONE_STATUSES:
        return "done"
    if lower in _TODO_STATUSES:
        return "to do"
    return "in progress"


def _ticket_stamp(ticket: Dict[str, Any], field: str) -> Optional[datetime]:
    raw = None
    if field == "created":
        raw = ticket.get("created_at")
    elif field == "updated":
        raw = ticket.get("updated_at") or ticket.get("created_at")
    elif field == "resolved":
        raw = ticket.get("done_at") or ticket.get("updated_at")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def ticket_matches_filters(ticket: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    status = str(ticket.get("status") or "").strip()
    if filters.get("status") and status.lower() != str(filters["status"]).strip().lower():
        return False
    cat = filters.get("status_category")
    if cat:
        wanted = _status_category(cat)
        actual = _status_category(status)
        matches = actual == wanted
        if filters.get("status_category_not"):
            matches = not matches
        if not matches:
            return False
    itype = filters.get("issue_type")
    if itype and str(ticket.get("issue_type") or "").strip().lower() != itype.lower():
        return False
    needle = filters.get("text")
    if needle:
        hay = f"{ticket.get('title') or ''} {ticket.get('description') or ''}".lower()
        if needle not in hay:
            return False
    wanted_labels = filters.get("labels") or []
    if wanted_labels:
        have = {str(label).strip().lower() for label in resolve_ticket_labels(ticket)}
        if not have.intersection(wanted_labels):
            return False
    for field, cutoff in (filters.get("since") or {}).items():
        stamp = _ticket_stamp(ticket, field)
        if stamp is None or stamp < cutoff:
            return False
    return True


def compact_internal_ticket(ticket: Dict[str, Any], *, project_key: str = "") -> Dict[str, Any]:
    key = str(ticket.get("key") or "").strip().upper()
    proj = (project_key or ticket.get("project_key") or "").strip().upper()
    return {
        "key": key,
        "summary": str(ticket.get("title") or key).strip(),
        "issue_type": str(ticket.get("issue_type") or "Task").strip() or "Task",
        "status": str(ticket.get("status") or "").strip(),
        "project_key": proj,
        "url": ticket_url(key) if key else "",
        "labels": resolve_ticket_labels(ticket),
        "created": ticket.get("created_at"),
        "updated": ticket.get("updated_at"),
        "done_at": ticket.get("done_at"),
    }


def search_internal_board(
    *,
    jql: str,
    allowed_keys: Sequence[str],
    max_results: int,
) -> Dict[str, Any]:
    filters = parse_internal_filters(jql)
    mentioned = filters.get("project_keys") or []
    allowed = [k.strip().upper() for k in allowed_keys if str(k).strip()]
    if mentioned:
        unknown = [k for k in mentioned if k not in set(allowed)]
        if unknown:
            raise SearchJiraError(
                "JQL references projects outside the portfolio: " + ", ".join(unknown)
            )
        projects = mentioned
    else:
        projects = allowed
    store = get_ticket_store()
    rows: List[Dict[str, Any]] = []
    for key in projects:
        for ticket in store.list_tickets_by_project(key):
            if not ticket_matches_filters(ticket, filters):
                continue
            compact = compact_internal_ticket(ticket, project_key=key)
            if compact.get("key"):
                rows.append(compact)
    rows.sort(key=lambda row: str(row.get("updated") or row.get("created") or ""), reverse=True)
    issues = rows[: max(1, int(max_results))]
    return {
        "ok": True,
        "jql": jql,
        "source": "internal_board",
        "issues": issues,
        "count": len(issues),
    }
