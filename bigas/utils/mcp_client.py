"""Programmatic MCP client for listing and invoking tools on a Bigas (or MCP) server."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_SLOW_TOOLS = (
    "weekly_analytics_report",
    "weekly_cto_ai_report",
    "create_release_notes",
    "generate_weekly_x_post",
    "run_cross_platform_marketing_analysis",
)


class MCPClientError(RuntimeError):
    pass


class MCPClient:
    """Fetch manifests and call MCP tools via JSON-RPC ``tools/call``."""

    def __init__(
        self,
        base_url: str,
        *,
        auth_token: Optional[str] = None,
        access_header: Optional[str] = None,
        timeout_s: int = 120,
        exclude_slow_tools: bool = True,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        if not self.base_url:
            raise MCPClientError("base_url is required")
        self.auth_token = (auth_token or "").strip() or None
        self.access_header = (
            (access_header or os.environ.get("BIGAS_ACCESS_HEADER") or "X-Bigas-Access-Key").strip()
            or "X-Bigas-Access-Key"
        )
        self.timeout_s = max(5, int(timeout_s))
        self.exclude_slow_tools = exclude_slow_tools
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "BigasMCPClient/1.0",
            }
        )
        if self.auth_token:
            self._session.headers["Authorization"] = f"Bearer {self.auth_token}"
            self._session.headers[self.access_header] = self.auth_token

    def _auth_headers(self) -> Dict[str, str]:
        if not self.auth_token:
            return {}
        return {
            "Authorization": f"Bearer {self.auth_token}",
            self.access_header: self.auth_token,
        }

    def fetch_manifest(self) -> Dict[str, Any]:
        url = f"{self.base_url}/mcp/manifest"
        try:
            resp = self._session.get(url, timeout=min(self.timeout_s, 30))
        except requests.RequestException as e:
            raise MCPClientError(f"Failed to fetch MCP manifest: {e}") from e
        if resp.status_code >= 400:
            raise MCPClientError(
                f"MCP manifest HTTP {resp.status_code}: {(resp.text or '')[:500]}"
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise MCPClientError("MCP manifest response is not valid JSON") from e
        if not isinstance(data, dict):
            raise MCPClientError("MCP manifest response must be a JSON object")
        return data

    def list_tools(self) -> List[Dict[str, Any]]:
        manifest = self.fetch_manifest()
        tools = manifest.get("tools") or []
        if not isinstance(tools, list):
            return []
        out: List[Dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = (tool.get("name") or "").strip()
            if not name:
                continue
            if self.exclude_slow_tools and self._is_slow_tool(tool):
                continue
            out.append(tool)
        return out

    def _is_slow_tool(self, tool: Dict[str, Any]) -> bool:
        name = (tool.get("name") or "").lower()
        path = (tool.get("path") or "").lower()
        for token in DEFAULT_SLOW_TOOLS:
            token_l = token.lower()
            if token_l in name or token_l in path:
                return True
        return False

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        tool_name = (name or "").strip()
        if not tool_name:
            raise MCPClientError("tool name is required")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }
        url = f"{self.base_url}/mcp"
        try:
            resp = self._session.post(
                url,
                json=payload,
                headers=self._auth_headers(),
                timeout=self.timeout_s,
            )
        except requests.Timeout as e:
            raise MCPClientError(f"Tool call timed out after {self.timeout_s}s") from e
        except requests.RequestException as e:
            raise MCPClientError(f"Tool call failed: {e}") from e

        if resp.status_code >= 400:
            raise MCPClientError(
                f"Tool call HTTP {resp.status_code}: {(resp.text or '')[:500]}"
            )

        try:
            body = resp.json()
        except json.JSONDecodeError as e:
            raise MCPClientError("Tool call response is not valid JSON") from e

        if not isinstance(body, dict):
            raise MCPClientError("Tool call response must be a JSON object")

        if body.get("error"):
            err = body["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise MCPClientError(message or "MCP tool call failed")

        result = body.get("result") or {}
        if not isinstance(result, dict):
            return {"raw": result}

        content = result.get("content") or []
        text_parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text") or ""))
        output_text = "\n".join(part for part in text_parts if part).strip()
        structured = result.get("structuredContent")
        return {
            "is_error": bool(result.get("isError")),
            "text": output_text,
            "structured": structured,
            "raw": result,
        }
