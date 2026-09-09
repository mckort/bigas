"""Complete a candidate eval model (OpenAI, Gemini, Anthropic)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests

from bigas.eval.registry import _infer_provider
from bigas.llm.completion import LLMCompletion, ToolCall
from bigas.llm.limits import supports_temperature
from bigas.llm.usage import TokenUsage


def complete_eval_model(
    model_id: str,
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
) -> LLMCompletion:
    """Run one eval step against the candidate model id."""
    return complete_eval_chat(
        model_id,
        _messages(prompt),
        max_tokens=max_tokens,
        temperature=temperature,
    )


def complete_eval_chat(
    model_id: str,
    messages: List[dict],
    *,
    tools: Optional[List[dict]] = None,
    max_tokens: Optional[int] = None,
    temperature: float = 0.2,
) -> LLMCompletion:
    """Multi-turn eval complete. ``tools`` is the OpenAI function-calling shape."""
    budget = max_tokens if max_tokens is not None else _step_max_tokens()
    provider = _infer_provider(model_id)
    if provider == "anthropic":
        return _complete_anthropic_chat(
            model_id,
            messages,
            tools=tools,
            max_tokens=budget,
            temperature=temperature,
        )
    from bigas.llm.factory import get_llm_client

    client, _resolved = get_llm_client(feature="model_eval", explicit_model=model_id)
    kwargs: Dict[str, Any] = {}
    if tools:
        kwargs["tools"] = tools
    return client.complete_detailed(
        messages=messages,
        max_tokens=budget,
        temperature=temperature,
        **kwargs,
    )


class EvalChatClient:
    """``complete_detailed`` adapter so eval candidates can drive the goal loop."""

    def __init__(self, model_id: str):
        self.model_id = model_id

    def complete_detailed(self, messages, *, max_tokens=None, temperature=None, **kwargs):
        return complete_eval_chat(
            self.model_id,
            messages,
            tools=kwargs.get("tools"),
            max_tokens=max_tokens,
            temperature=0.2 if temperature is None else temperature,
        )


def _messages(prompt: str) -> List[dict]:
    return [{"role": "user", "content": prompt}]


def _step_max_tokens() -> int:
    raw = (os.environ.get("EVAL_STEP_MAX_TOKENS") or "8192").strip()
    try:
        return max(256, int(raw))
    except ValueError:
        return 8192


def _openai_tools_to_anthropic(tools: Optional[List[dict]]) -> List[dict]:
    out: List[dict] = []
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str((fn or {}).get("name") or "").strip()
        if not name:
            continue
        params = (fn or {}).get("parameters") or {"type": "object", "properties": {}}
        out.append(
            {
                "name": name,
                "description": str((fn or {}).get("description") or ""),
                "input_schema": params if isinstance(params, dict) else {"type": "object", "properties": {}},
            }
        )
    return out


def _parse_tool_call_raw(raw: Any) -> tuple[str, dict, str]:
    if isinstance(raw, ToolCall):
        name = str(raw.name or "").strip()
        args = raw.arguments
        tool_id = str(raw.id or f"tool_{name}")
    elif isinstance(raw, dict):
        fn = raw.get("function") if isinstance(raw.get("function"), dict) else raw
        name = str((fn or {}).get("name") or raw.get("name") or "").strip()
        args = (fn or {}).get("arguments") if isinstance(fn, dict) else raw.get("arguments")
        tool_id = str(raw.get("id") or f"tool_{name}")
    else:
        fn = getattr(raw, "function", None)
        fn_dict = fn if isinstance(fn, dict) else {}
        name = str(fn_dict.get("name") or getattr(raw, "name", "") or "").strip()
        args = fn_dict.get("arguments") if fn_dict else getattr(raw, "arguments", None)
        tool_id = str(getattr(raw, "id", None) or f"tool_{name}")
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    return name, args, tool_id


def _anthropic_messages(messages: List[dict]) -> tuple:
    system_parts: List[str] = []
    converted: List[dict] = []
    pending_tools: List[dict] = []

    def _flush_tools(*, user_text: str = "") -> None:
        content_blocks = list(pending_tools)
        pending_tools.clear()
        if user_text:
            content_blocks.append({"type": "text", "text": user_text})
        if content_blocks:
            converted.append({"role": "user", "content": content_blocks})

    for message in messages:
        role = (message.get("role") or "user").lower()
        content = message.get("content")
        text = content.strip() if isinstance(content, str) else (str(content).strip() if content else "")
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            pending_tools.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id") or message.get("id") or "",
                    "content": text or "{}",
                }
            )
            continue
        if role == "assistant":
            if pending_tools:
                _flush_tools()
            parts: List[dict] = []
            if text:
                parts.append({"type": "text", "text": text})
            for raw in message.get("tool_calls") or []:
                name, args, tool_id = _parse_tool_call_raw(raw)
                if name:
                    parts.append(
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": args,
                        }
                    )
            if parts:
                converted.append({"role": "assistant", "content": parts})
            continue
        _flush_tools(user_text=text)
    if pending_tools:
        _flush_tools()
    return "\n\n".join(system_parts), converted


def _complete_anthropic_chat(
    model_id: str,
    messages: List[dict],
    *,
    tools: Optional[List[dict]] = None,
    max_tokens: int,
    temperature: float,
) -> LLMCompletion:
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot eval Claude models")
    system, converted = _anthropic_messages(messages)
    payload = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": converted or [{"role": "user", "content": "Continue."}],
    }
    if system:
        payload["system"] = system
    anthropic_tools = _openai_tools_to_anthropic(tools)
    if anthropic_tools:
        payload["tools"] = anthropic_tools
    if supports_temperature(model_id):
        payload["temperature"] = temperature
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=180,
    )
    if resp.status_code >= 400:
        content_type = (resp.headers.get("content-type") or "").lower()
        if "json" in content_type or "text" in content_type:
            detail = resp.text[:400]
        else:
            detail = f"(non-text body, {len(resp.content)} bytes)"
        raise RuntimeError(f"Anthropic eval complete failed HTTP {resp.status_code}: {detail}")
    data = resp.json()
    parts = []
    calls = []
    for index, block in enumerate(data.get("content") or []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        if block.get("type") == "tool_use":
            name = str(block.get("name") or "").strip()
            if not name:
                continue
            args = block.get("input") if isinstance(block.get("input"), dict) else {}
            calls.append(
                ToolCall(
                    id=str(block.get("id") or f"call_{index}_{name}"),
                    name=name,
                    arguments=args,
                )
            )
    usage_raw = data.get("usage") or {}
    prompt_tokens = int(usage_raw.get("input_tokens") or 0)
    output_tokens = int(usage_raw.get("output_tokens") or 0)
    return LLMCompletion(
        text="".join(parts),
        finish_reason=str(data.get("stop_reason") or "") or None,
        usage=TokenUsage(
            prompt_tokens=prompt_tokens,
            candidates_tokens=output_tokens,
            total_tokens=prompt_tokens + output_tokens,
        ),
        tool_calls=tuple(calls),
    )
