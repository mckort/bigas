"""Agent chat orchestration — Chief of Staff routing and specialist agents."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from bigas.chat.db import get_chat_store
from bigas.llm.factory import get_llm_client
from bigas.utils.mcp_client import MCPClient, MCPClientError

logger = logging.getLogger(__name__)

AGENT_TOOL_PREFIXES = {
    "marketing": (
        "fetch_analytics",
        "fetch_custom",
        "ask_analytics",
        "analyze_trends",
        "weekly_analytics",
        "get_stored",
        "get_latest",
        "analyze_underperforming",
        "cleanup_old",
        "linkedin_ads",
        "fetch_linkedin",
        "reddit_",
        "fetch_reddit",
        "summarize_reddit",
        "run_reddit",
        "run_linkedin",
        "run_google_ads",
        "run_meta",
        "run_cross_platform",
        "get_job_",
    ),
    "product": (
        "product_resource",
        "create_release",
        "progress_updates",
        "generate_weekly_x",
        "jira_status",
    ),
    "cto": (
        "review_and_comment",
        "autofix",
        "fetch_ai_usage",
        "weekly_cto",
        "website_monitor",
        "run_qa",
    ),
}

DELEGATE_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "delegate_to_marketing",
            "description": "Delegate a marketing/analytics task to the Marketing agent (GA4, ads, trends).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Clear task description for the marketing agent."},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_product",
            "description": "Delegate a product management task (Jira, release notes, progress updates).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Clear task description for the product agent."},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_cto",
            "description": "Delegate an engineering/CTO task (PR review, QA, monitoring, AI usage).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Clear task description for the CTO agent."},
                },
                "required": ["task"],
            },
        },
    },
]

DELEGATE_MAP = {
    "delegate_to_marketing": "marketing",
    "delegate_to_product": "product",
    "delegate_to_cto": "cto",
}


def _mcp_client() -> MCPClient:
    base_url = (os.environ.get("SERVER_URL") or "http://127.0.0.1:8080").strip().rstrip("/")
    keys = (os.environ.get("BIGAS_ACCESS_KEYS") or "").split(",")
    token = keys[0].strip() if keys and keys[0].strip() else None
    return MCPClient(base_url, auth_token=token, timeout_s=300, exclude_slow_tools=False)


def _filter_tools_for_agent(tools: List[Dict[str, Any]], agent_id: str) -> List[Dict[str, Any]]:
    prefixes = AGENT_TOOL_PREFIXES.get(agent_id, ())
    out = []
    for tool in tools:
        name = (tool.get("name") or "").lower()
        if any(name.startswith(p.lower()) or p.lower() in name for p in prefixes):
            out.append(tool)
    return out


def _tools_summary(tools: List[Dict[str, Any]], limit: int = 20) -> str:
    lines = []
    for tool in tools[:limit]:
        name = tool.get("name", "")
        desc = (tool.get("description") or "")[:120]
        lines.append(f"- {name}: {desc}")
    if len(tools) > limit:
        lines.append(f"... and {len(tools) - limit} more tools")
    return "\n".join(lines)


def _parse_json_action(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    # Try fenced JSON block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    elif text.startswith("{"):
        pass
    else:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
        else:
            return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _run_tool_call(client: MCPClient, tool_name: str, arguments: Dict[str, Any]) -> str:
    try:
        result = client.call_tool(tool_name, arguments)
        if result.get("is_error"):
            return f"Tool {tool_name} failed:\n{result.get('text', 'Unknown error')}"
        return result.get("text") or json.dumps(result.get("structured") or result.get("raw"), ensure_ascii=False)
    except MCPClientError as e:
        return f"Tool call error ({tool_name}): {e}"
    except Exception as e:
        logger.exception("Unexpected tool call failure")
        return f"Unexpected error calling {tool_name}: {e}"


def _select_tool_via_llm(
    agent_id: str,
    agent_config: Dict[str, Any],
    user_message: str,
    tools: List[Dict[str, Any]],
    history: List[Dict[str, str]],
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Returns (response_text, tool_name, tool_args) — tool fields set if a tool should run."""
    llm, model = get_llm_client(feature="chat")
    tool_summary = _tools_summary(tools)
    system = (
        f"{agent_config.get('system_prompt_goals', '')}\n\n"
        "You are responding in the Bigas chat interface. "
        "If the user request requires calling a backend tool, respond with ONLY a JSON object:\n"
        '{"action":"tool","tool_name":"<name>","arguments":{...}}\n'
        'Otherwise respond with ONLY:\n'
        '{"action":"answer","text":"<your reply>"}\n\n'
        f"Available tools:\n{tool_summary}"
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_message})

    raw = llm.complete(messages, temperature=0.2)
    action = _parse_json_action(raw)
    if not action:
        return raw.strip() or "I couldn't process that request.", None, None

    if action.get("action") == "answer":
        return str(action.get("text") or "").strip() or raw, None, None

    if action.get("action") == "tool":
        return "", str(action.get("tool_name") or ""), action.get("arguments") or {}

    if action.get("action") == "delegate" and agent_id == "chief":
        target = action.get("agent_id") or action.get("agent")
        task = action.get("task") or user_message
        return "", f"__delegate__:{target}", {"task": task}

    return raw.strip(), None, None


def _run_openai_tool_loop(
    llm,
    messages: List[Dict[str, Any]],
    thread_id: str,
) -> str:
    """OpenAI function-calling loop for chief of staff."""
    if not hasattr(llm, "_client"):
        return "Tool delegation requires an OpenAI model."

    for _ in range(5):
        resp = llm._client.chat.completions.create(
            model=llm.model_name,
            messages=messages,
            tools=DELEGATE_TOOL_DEFS,
            tool_choice="auto",
            temperature=0.3,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                target = DELEGATE_MAP.get(fn)
                if target:
                    result = run_specialist_task(
                        target,
                        args.get("task") or "",
                        thread_id=thread_id,
                        async_mode=True,
                    )
                else:
                    result = "Unknown delegation target."
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
            continue
        return (msg.content or "").strip()
    return "I wasn't able to complete that request."


def run_specialist_task(
    agent_id: str,
    task: str,
    *,
    thread_id: Optional[str] = None,
    async_mode: bool = False,
) -> str:
    """Run a task on a specialist agent, optionally writing results to a thread."""
    store = get_chat_store()
    agent_config = store.get_agent(agent_id) or {"agent_id": agent_id, "system_prompt_goals": ""}

    def _work() -> str:
        client = _mcp_client()
        tools = _filter_tools_for_agent(client.list_tools(), agent_id)
        _, tool_name, tool_args = _select_tool_via_llm(agent_id, agent_config, task, tools, [])
        if tool_name and tool_name.startswith("__delegate__"):
            return "Specialist agents cannot delegate further."
        if tool_name:
            result = _run_tool_call(client, tool_name, tool_args or {})
        else:
            llm, _ = get_llm_client(feature="chat")
            system = agent_config.get("system_prompt_goals", "")
            result = llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": task},
                ],
                temperature=0.4,
            )
        if thread_id:
            store.add_message(
                thread_id,
                role="assistant",
                content=result,
                metadata={"agent_id": agent_id, "delegated": True},
            )
        return result

    if async_mode and thread_id:
        store.add_message(
            thread_id,
            role="system",
            content=f"⏳ {agent_id.title()} agent is working on your request…",
            metadata={"status": "in_progress", "agent_id": agent_id},
        )

        def _bg():
            try:
                _work()
            except Exception as e:
                logger.exception("Async specialist task failed")
                store.add_message(
                    thread_id,
                    role="assistant",
                    content=f"Task failed: {e}",
                    metadata={"agent_id": agent_id, "error": True},
                )

        threading.Thread(target=_bg, daemon=True).start()
        return f"Delegated to {agent_id} agent. Results will appear in this thread when ready."

    return _work()


def handle_chat_message(
    *,
    thread_id: str,
    user_id: str,
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Process a user message and return response metadata."""
    store = get_chat_store()
    thread = store.get_thread(thread_id)
    if not thread or thread.get("user_id") != user_id:
        raise ValueError("Thread not found")

    agent_id = thread.get("agent_id") or "chief"
    agent_config = store.get_agent(agent_id) or {}
    history = history or []

    store.add_message(thread_id, role="user", content=user_message)

    if agent_id == "chief":
        llm, model = get_llm_client(feature="chat")
        system = (
            f"{agent_config.get('system_prompt_goals', '')}\n\n"
            "You are the Chief of Staff in the Bigas chat UI. Answer general questions directly. "
            "For marketing, product, or engineering tasks, delegate to the appropriate specialist."
        )
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history[-10:])
        messages.append({"role": "user", "content": user_message})

        # Try OpenAI tool calling when available
        if model.lower().startswith("gpt") and hasattr(llm, "_client"):
            response_text = _run_openai_tool_loop(llm, messages, thread_id)
        else:
            client = _mcp_client()
            all_tools = client.list_tools()
            response_text, tool_name, tool_args = _select_tool_via_llm(
                "chief", agent_config, user_message, all_tools, history
            )
            if tool_name and tool_name.startswith("__delegate__:"):
                target = tool_name.split(":", 1)[1]
                response_text = run_specialist_task(
                    target,
                    (tool_args or {}).get("task") or user_message,
                    thread_id=thread_id,
                    async_mode=True,
                )
            elif tool_name:
                response_text = _run_tool_call(client, tool_name, tool_args or {})

        if response_text:
            msg = store.add_message(
                thread_id,
                role="assistant",
                content=response_text,
                metadata={"agent_id": "chief"},
            )
            return {"status": "complete", "message": msg}

        return {"status": "in_progress"}

    # Direct specialist chat
    client = _mcp_client()
    tools = _filter_tools_for_agent(client.list_tools(), agent_id)
    response_text, tool_name, tool_args = _select_tool_via_llm(
        agent_id, agent_config, user_message, tools, history
    )

    if tool_name:
        response_text = _run_tool_call(client, tool_name, tool_args or {})

    msg = store.add_message(
        thread_id,
        role="assistant",
        content=response_text or "Done.",
        metadata={"agent_id": agent_id},
    )
    return {"status": "complete", "message": msg}


def post_agent_callback(thread_id: str, content: str, *, agent_id: str = "system") -> Dict[str, Any]:
    """Allow sub-agents to report completion to a thread."""
    store = get_chat_store()
    return store.add_message(
        thread_id,
        role="assistant",
        content=content,
        metadata={"agent_id": agent_id, "callback": True},
    )
