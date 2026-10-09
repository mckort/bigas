"""Read-only Accounted MCP client for the Bigas CFO chat.

Accounted's HTTP MCP is stateless JSON-RPC. A Bearer API key
(``ACCOUNTED_API_KEY``) is enough; there is no Cursor session to reuse.
Writes are refused here even if the key could stage them.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = (
    "https://app.accounted.se/api/extensions/ext/mcp-server/mcp"
    "?tool_namespace=accounted&client=bigas"
)
MAX_TOOL_TEXT = 12_000
_LIVE_TOOLS_TTL_S = 600
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

NOT_CONFIGURED = (
    "Bookkeeping is not connected. Add ACCOUNTED_API_KEY "
    "(a read-scoped Accounted key in Secret Manager) and ask again."
)
WRITE_REFUSED = (
    "That Accounted tool is not available in CFO chat. "
    "This connection is read-only and does not book, categorise, approve, or lock a period."
)

# Fixed read list. tools/list may offer 150 tools; only these are shown or called.
READ_TOOL_SPECS: tuple[Dict[str, str], ...] = (
    {
        "name": "accounted_get_agent_briefing",
        "description": "Company identity, accounting method, and what needs attention.",
    },
    {
        "name": "accounted_list_companies",
        "description": "Companies this key can read.",
    },
    {
        "name": "accounted_get_income_statement",
        "description": "Income statement (resultaträkning) for a period.",
    },
    {
        "name": "accounted_get_balance_sheet",
        "description": "Balance sheet (balansräkning) for a period.",
    },
    {
        "name": "accounted_get_trial_balance",
        "description": "Trial balance (saldobalans) for a period.",
    },
    {
        "name": "accounted_get_kpi_report",
        "description": "KPI report for a period.",
    },
    {
        "name": "accounted_get_vat_report",
        "description": "VAT report (momsdeklaration) for a period. Say if figures are preliminary.",
    },
    {
        "name": "accounted_vat_close_check",
        "description": "Whether the VAT period is ready to close, and what blocks it.",
    },
    {
        "name": "accounted_list_invoices",
        "description": "Customer invoices.",
    },
    {
        "name": "accounted_list_supplier_invoices",
        "description": "Supplier invoices.",
    },
    {
        "name": "accounted_list_uncategorized_transactions",
        "description": "Uncategorised bank transactions.",
    },
    {
        "name": "accounted_get_general_ledger",
        "description": "General ledger for an account or period.",
    },
    {
        "name": "accounted_query_journal",
        "description": "Search journal lines by text, amount, or date.",
    },
)
READ_TOOL_NAMES = frozenset(spec["name"] for spec in READ_TOOL_SPECS)

_LOOSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_id": {
            "type": "string",
            "description": "Company UUID. Omit for the key's default company.",
        },
    },
    "additionalProperties": True,
}

_cache_lock = threading.Lock()
_live_cache: Dict[str, Any] = {}
_rpc_lock = threading.Lock()
_rpc_id = 0


class AccountedError(RuntimeError):
    pass


def is_accounted_tool(name: Optional[str]) -> bool:
    return (name or "").strip().startswith("accounted_")


def _api_key() -> str:
    return (os.environ.get("ACCOUNTED_API_KEY") or "").strip()


def _endpoint() -> str:
    raw = (os.environ.get("ACCOUNTED_MCP_URL") or DEFAULT_MCP_URL).strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AccountedError("ACCOUNTED_MCP_URL must be an HTTP(S) URL")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("tool_namespace", "accounted")
    query.setdefault("client", "bigas")
    company = (os.environ.get("ACCOUNTED_COMPANY") or "").strip().lower()
    if company:
        if _UUID_RE.match(company):
            query["company"] = company
        else:
            logger.warning("Ignoring ACCOUNTED_COMPANY because it is not a UUID")
    return urlunparse(parsed._replace(query=urlencode(query)))


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {api_key}",
        "X-Accounted-Client": "bigas",
    }


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int) -> tuple[int, str]:
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.Timeout as exc:
        raise AccountedError(f"Accounted timed out after {timeout}s") from exc
    except requests.RequestException as exc:
        raise AccountedError(f"Accounted request failed: {exc}") from exc
    return resp.status_code, resp.text or ""


def _parse_sse(text: str) -> Dict[str, Any]:
    last: Any = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped[5:].strip()
        if not data or data == "[DONE]":
            continue
        last = json.loads(data)
    if not isinstance(last, dict):
        raise AccountedError("Accounted returned an empty response")
    return last


def _parse_body(status: int, text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if status >= 400:
        message = f"HTTP {status}"
        if raw.startswith("{"):
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = None
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict) and err.get("message"):
                    message = str(err["message"])
                elif isinstance(err, str) and err:
                    message = err
        raise AccountedError(message)
    if not raw:
        raise AccountedError("Accounted returned an empty response")
    try:
        body = json.loads(raw) if raw.startswith("{") else _parse_sse(raw)
    except json.JSONDecodeError as exc:
        raise AccountedError("Accounted returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise AccountedError("Accounted returned an unexpected response")
    if body.get("error"):
        err = body["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise AccountedError(message or "Accounted tool call failed")
    return body


def _next_id() -> int:
    global _rpc_id
    with _rpc_lock:
        _rpc_id += 1
        return _rpc_id


def _rpc(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    key = _api_key()
    if not key:
        raise AccountedError(NOT_CONFIGURED)
    payload = {
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": method,
        "params": params,
    }
    status, text = _post_json(_endpoint(), _headers(key), payload, timeout=60)
    body = _parse_body(status, text)
    result = body.get("result")
    if not isinstance(result, dict):
        raise AccountedError("Accounted returned an unexpected response")
    return result


def _static_tool(spec: Dict[str, str]) -> Dict[str, Any]:
    return {
        "name": spec["name"],
        "description": spec["description"],
        "parameters": dict(_LOOSE_SCHEMA),
    }


def _normalize_live(tool: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    schema = tool.get("inputSchema") or tool.get("parameters") or fallback["parameters"]
    if not isinstance(schema, dict):
        schema = fallback["parameters"]
    description = (tool.get("description") or fallback["description"] or "").strip()
    return {
        "name": fallback["name"],
        "description": description or fallback["description"],
        "parameters": schema,
    }


def clear_live_tool_cache() -> None:
    with _cache_lock:
        _live_cache.clear()


def _fetch_live_tools() -> List[Dict[str, Any]]:
    key = _api_key()
    now = time.monotonic()
    with _cache_lock:
        cached = _live_cache.get("entry")
        if (
            isinstance(cached, dict)
            and cached.get("key") == key
            and now - float(cached.get("at") or 0) < _LIVE_TOOLS_TTL_S
        ):
            return list(cached.get("tools") or [])

    tools: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    for _ in range(5):
        params: Dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        result = _rpc("tools/list", params)
        batch = result.get("tools") or []
        if isinstance(batch, list):
            tools.extend(item for item in batch if isinstance(item, dict))
        cursor = result.get("nextCursor") or None
        if not cursor:
            break
    with _cache_lock:
        _live_cache["entry"] = {"key": key, "at": time.monotonic(), "tools": tools}
    return tools


def cfo_accounted_tools() -> List[Dict[str, Any]]:
    """Tool defs for the CFO prompt. Live schemas replace the static ones when the key works."""
    catalog = {spec["name"]: _static_tool(spec) for spec in READ_TOOL_SPECS}
    if not _api_key():
        return [catalog[spec["name"]] for spec in READ_TOOL_SPECS]
    try:
        live = _fetch_live_tools()
    except Exception:
        logger.exception("Accounted tools/list failed; using the static read catalog")
        return [catalog[spec["name"]] for spec in READ_TOOL_SPECS]
    by_name = {str(tool.get("name") or ""): tool for tool in live}
    merged: List[Dict[str, Any]] = []
    for spec in READ_TOOL_SPECS:
        fallback = catalog[spec["name"]]
        live_tool = by_name.get(spec["name"])
        merged.append(_normalize_live(live_tool, fallback) if live_tool else fallback)
    return merged


def _format_tool_result(result: Dict[str, Any]) -> str:
    parts: List[str] = []
    content = result.get("content") or []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
    text = "\n".join(part for part in parts if part).strip()
    if not text and result.get("structuredContent") is not None:
        text = json.dumps(result["structuredContent"], ensure_ascii=False)
    if not text:
        text = "Accounted returned no content."
    if len(text) > MAX_TOOL_TEXT:
        text = text[:MAX_TOOL_TEXT] + "\n…(truncated)"
    return text


def dispatch_accounted_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Run one allowlisted read, or refuse. Never calls Accounted for other names."""
    tool_name = (name or "").strip()
    if tool_name not in READ_TOOL_NAMES:
        return WRITE_REFUSED
    if not _api_key():
        return NOT_CONFIGURED
    try:
        result = _rpc("tools/call", {"name": tool_name, "arguments": arguments or {}})
    except AccountedError as exc:
        return f"I couldn't read that from Accounted ({tool_name}): {exc}"
    return _format_tool_result(result)
