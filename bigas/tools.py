"""
@register_tool decorator — annotates a handler function with its MCP tool metadata.

The manifest can be built from _TOOL_REGISTRY at startup so it never drifts
from the actual handlers.

Usage:
    from bigas.tools import register_tool

    @register_tool(
        name="get_revenue",
        description="Return total revenue for a date range.",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        }
    )
    def get_revenue_handler(start_date: str, end_date: str):
        ...
"""
from __future__ import annotations

from typing import Callable, List

_TOOL_REGISTRY: List[dict] = []


def register_tool(name: str, description: str, parameters: dict):
    """Decorator that registers a handler as an MCP tool."""

    def decorator(fn: Callable) -> Callable:
        entry = {
            "name": name,
            "description": description,
            "path": f"/mcp/tools/{name}",
            "method": "POST",
            "parameters": parameters,
            "_handler": fn,
        }
        setattr(fn, "_mcp_tool", entry)
        _TOOL_REGISTRY.append(entry)
        return fn

    return decorator


def get_registered_tools() -> List[dict]:
    """Return all tool manifest entries (without the _handler key)."""
    return [{k: v for k, v in tool.items() if k != "_handler"} for tool in _TOOL_REGISTRY]

