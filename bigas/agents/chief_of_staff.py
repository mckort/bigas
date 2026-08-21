"""Agent chat orchestration — Chief of Staff routing and specialist agents."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from bigas.chat.db import get_chat_store
from bigas.chat.jira_formatting import (
    JIRA_AWARE_AGENT_IDS,
    JIRA_FORMATTING_RULES,
    humanize_jira_tool_result,
)
from bigas.github_refs import is_owner_repo, parse_cursor_agent_id, resolve_repo_and_pr
from bigas.llm.factory import get_llm_client
from bigas.portfolio import (
    normalize_project_key,
    prompt_block,
    repo_map,
    resolve_project,
    scrub_analytics_question,
)
from bigas.resources.devops.pipeline import (
    clear_stale_pending_deploy,
    is_deploy_start,
    run_chat_deploy_pipeline,
    should_run_deploy_pipeline,
)
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
        "fix_failed",
        "fetch_ai_usage",
        "weekly_cto",
        "website_monitor",
        "run_qa",
    ),
    "devops": (
        "check_deployment",
        "trigger_deployment",
        "get_deployment_status",
        "check_website_health",
        "fetch_github_action_logs",
        "create_github_pr",
        "fix_failed",
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
            "description": "Delegate an engineering/CTO task (PR review, QA, failed-deploy hotfix, monitoring, AI usage).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Clear task description for the CTO agent."},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_devops",
            "description": "Delegate a DevOps task (deployment risk check, trigger GitHub Actions deploy, health check).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Clear task description for the DevOps agent."},
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
    "delegate_to_devops": "devops",
}

SPECIALIST_IDS = ("marketing", "product", "cto", "devops")

SPECIALIST_CAPABILITIES = (
    "Specialists (always delegate domain work to them — they have the tools and will reply here):\n"
    "- marketing: GA4, ads (Google/Meta/LinkedIn/Reddit), trends, weekly/portfolio reports.\n"
    "- product: Jira release notes, progress updates, social drafts, board automation.\n"
    "- cto: GitHub PR review, autofix, QA, failed-deploy hotfix, AI usage, monitoring.\n"
    "- devops: production deploy via GitHub Actions (including manual trigger_deployment), "
    "deploy status, site health, CI logs, hotfix PRs. vcfieldassistant/VFA is a DevOps deploy.\n"
    "Every specialist and Chief of Staff can call lookup_jira and create_jira_issue (Task/Bug). "
    "Look up Epics when needed, then decide whether the new work belongs under one or should be standalone. "
    "Never tell the user to create the Jira issue themselves.\n"
)

# Shared with every specialist; COS may also call these without a handoff.
SHARED_AGENT_TOOLS = frozenset({"create_jira_issue", "lookup_jira"})

# Read-only / quick lookups COS may run without a specialist handoff,
# plus shared write tools such as create_jira_issue.
CHIEF_DIRECT_TOOL_NAMES = {
    "get_deployment_status",
    "check_website_health",
    "ask_analytics_question",
    "get_latest_report",
    "get_stored_reports",
    "fetch_ai_usage",
    "website_monitor",
    "get_job_status",
    "get_job_result",
} | set(SHARED_AGENT_TOOLS)

# If COS tries to invoke these itself, rewrite to a real specialist handoff.
MUST_DELEGATE_TOOLS = {
    "trigger_deployment": "devops",
    "check_deployment_risk": "devops",
    "create_github_pr": "devops",
    "github_workflow_run": "devops",
    "fetch_github_action_logs": "devops",
    "autofix_pr": "cto",
    "autofix_followup": "cto",
    "fix_failed_deployment": "cto",
    "review_and_comment_pr": "cto",
    "run_qa": "cto",
    "create_release_notes": "product",
    "progress_updates": "product",
    "generate_weekly_x_post": "product",
    "jira_status_automation": "product",
}

def _resolve_delegate_target(raw: Optional[str]) -> Optional[str]:
    compact = re.sub(r"[\s\-]+", "_", (raw or "").strip().lower())
    if not compact:
        return None
    if compact in DELEGATE_MAP:
        return DELEGATE_MAP[compact]
    if compact.startswith("delegate_to_"):
        compact = compact[len("delegate_to_") :]
    if compact in SPECIALIST_IDS:
        return compact
    return None


def _chief_direct_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wanted = {name.lower() for name in CHIEF_DIRECT_TOOL_NAMES}
    return [tool for tool in tools if (tool.get("name") or "").lower() in wanted]


def _mcp_tool_to_openai_def(tool: Dict[str, Any]) -> Dict[str, Any]:
    params = tool.get("parameters") or {"type": "object", "properties": {}}
    if not isinstance(params, dict):
        params = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": tool.get("name") or "tool",
            "description": (tool.get("description") or "")[:1024],
            "parameters": params,
        },
    }


def _chief_routing_extra(tool_summary: str) -> str:
    return (
        "You are the Chief of Staff in the Bigas chat UI. Answer general questions directly. "
        "For marketing, product, engineering, or deployment tasks, delegate to the specialist "
        "who owns that work.\n"
        f"{SPECIALIST_CAPABILITIES}"
        "Never say a specialist cannot do something listed above. Never 'virtually' delegate "
        "or offer to build a tool that already exists — emit JSON so the specialist actually "
        "receives the task and replies in this thread.\n"
        "You MAY call a tool yourself for a simple/quick lookup (live status, health, "
        "a short analytics question, lookup_jira) and to file a Jira Task/Bug with create_jira_issue. "
        "Do NOT trigger deploys, autofix, weekly reports, or other specialist pipelines yourself.\n\n"
        "If you should delegate, respond with ONLY:\n"
        '{"action":"delegate","agent_id":"marketing|product|cto|devops","task":"<clear task>"}\n'
        "If you should call a simple tool, respond with ONLY:\n"
        '{"action":"tool","tool_name":"<name>","arguments":{...}}\n'
        "Include project_key when the user named a product, site, or Jira key. "
        "For GitHub PRs, pass repo as owner/repo and pr_number, or pass pr_url.\n"
        "Otherwise respond with ONLY:\n"
        '{"action":"answer","text":"<your reply>"}\n\n'
        f"Tools you may call directly:\n{tool_summary or '(none — delegate instead)'}"
    )


def _specialist_json_extra(tool_summary: str) -> str:
    return (
        "You are responding in the Bigas chat interface. "
        "If the user request requires calling a backend tool, respond with ONLY a JSON object:\n"
        '{"action":"tool","tool_name":"<name>","arguments":{...}}\n'
        "Include project_key when the user named a product, site, or Jira key. "
        "For GitHub PRs, pass repo as owner/repo and pr_number, or pass pr_url "
        "(a github.com/.../pull/N link is enough). "
        "For Cursor autofix follow-up, include agent_id from a cursor.com/agents/bc-... URL. "
        "To file work in Jira, call lookup_jira if you need Epic/parent context, then create_jira_issue. "
        "Do not ask the user for an Epic key or to create the ticket. "
        "Only set parent_epic_key when the new work belongs under that Epic; otherwise create it standalone.\n"
        "Otherwise respond with ONLY:\n"
        '{"action":"answer","text":"<your reply>"}\n\n'
        f"Available tools:\n{tool_summary}"
    )


def _list_chief_mcp_tools() -> Tuple[Optional[MCPClient], List[Dict[str, Any]]]:
    try:
        client = _mcp_client()
        return client, client.list_tools()
    except Exception:
        logger.exception("Failed to list MCP tools for Chief of Staff")
        return None, []


def _dispatch_chief_tool(
    tool_name: Optional[str],
    tool_args: Optional[Dict[str, Any]],
    *,
    user_message: str,
    thread_id: str,
    mcp_client: Optional[MCPClient],
) -> Optional[str]:
    """Run a COS tool call, rewriting specialist pipelines into a real handoff."""
    if not tool_name:
        return None
    if not isinstance(tool_args, dict):
        tool_args = {}
    target = None
    if tool_name.startswith("__delegate__:"):
        target = tool_name.split(":", 1)[1]
    else:
        target = DELEGATE_MAP.get(tool_name) or _resolve_delegate_target(tool_name)
        if not target:
            target = MUST_DELEGATE_TOOLS.get(tool_name) or MUST_DELEGATE_TOOLS.get(
                (tool_name or "").strip().lower()
            )
    if target:
        base_task = tool_args.get("task") or user_message
        extra = {k: v for k, v in tool_args.items() if k != "task"}
        if extra:
            task = f"{base_task}\n\nContext: {json.dumps(extra)}"
        else:
            task = base_task
        return run_specialist_task(target, task, thread_id=thread_id, async_mode=True)
    if tool_name.lower() not in {n.lower() for n in CHIEF_DIRECT_TOOL_NAMES}:
        return f"Tool {tool_name} is not allowed for Chief of Staff."
    if mcp_client:
        return _run_tool_call(
            mcp_client,
            tool_name,
            _enrich_tool_args(tool_name, tool_args, user_message, caller_agent_id="chief"),
        )
    return f"I couldn't run {tool_name} (tools unavailable)."


def _mcp_client() -> MCPClient:
    base_url = (os.environ.get("SERVER_URL") or "http://127.0.0.1:8080").strip().rstrip("/")
    keys = (os.environ.get("BIGAS_ACCESS_KEYS") or "").split(",")
    token = keys[0].strip() if keys and keys[0].strip() else None
    return MCPClient(base_url, auth_token=token, timeout_s=300, exclude_slow_tools=False)


def _filter_tools_for_agent(tools: List[Dict[str, Any]], agent_id: str) -> List[Dict[str, Any]]:
    prefixes = AGENT_TOOL_PREFIXES.get(agent_id, ())
    shared = {n.lower() for n in SHARED_AGENT_TOOLS}
    shared_out: List[Dict[str, Any]] = []
    domain_out: List[Dict[str, Any]] = []
    seen = set()
    for tool in tools:
        name = (tool.get("name") or "").lower()
        if not name or name in seen:
            continue
        if name in shared:
            shared_out.append(tool)
            seen.add(name)
            continue
        if any(name.startswith(p.lower()) or p.lower() in name for p in prefixes):
            domain_out.append(tool)
            seen.add(name)
    return shared_out + domain_out


def _tools_summary(tools: List[Dict[str, Any]], limit: int = 20) -> str:
    lines = []
    for tool in tools[:limit]:
        name = tool.get("name", "")
        desc = (tool.get("description") or "")[:120]
        params = tool.get("parameters") or {}
        required = params.get("required") or []
        props = params.get("properties") or {}
        hints = []
        if required:
            hints.append("required: " + ", ".join(required))
        extras = [
            key
            for key in ("pr_url", "agent_id", "project_key")
            if key in props and key not in required
        ]
        if extras:
            hints.append("also: " + ", ".join(extras))
        suffix = f" ({'; '.join(hints)})" if hints else ""
        lines.append(f"- {name}: {desc}{suffix}")
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


_HUMAN_TEXT_KEYS = ("answer", "text", "message", "summary", "content", "report")


def humanize_tool_result(payload: Any) -> Optional[str]:
    """Unwrap tool JSON envelopes like {\"answer\": \"...\"} into plain chat text."""
    if payload is None:
        return None
    if isinstance(payload, dict):
        jira_text = humanize_jira_tool_result(payload)
        if jira_text:
            return jira_text
        for key in _HUMAN_TEXT_KEYS:
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        err = payload.get("error")
        if isinstance(err, str) and err.strip():
            return err.strip()
        nested = payload.get("structured") or payload.get("raw")
        if nested is not None and nested is not payload:
            return humanize_tool_result(nested)
        return None
    if not isinstance(payload, str):
        return None
    text = payload.strip()
    if not text:
        return None
    json_blob = text
    if not text.startswith("{") and not text.startswith("["):
        brace = text.find("{")
        if brace == -1:
            return None
        json_blob = text[brace:].strip()
    try:
        parsed = json.loads(json_blob)
    except json.JSONDecodeError:
        return None
    return humanize_tool_result(parsed)


def _run_tool_call(client: MCPClient, tool_name: str, arguments: Dict[str, Any]) -> str:
    try:
        result = client.call_tool(tool_name, arguments)
        raw_text = result.get("text") or ""
        human = humanize_tool_result(raw_text) or humanize_tool_result(result.get("structured"))
        if result.get("is_error"):
            return human or raw_text.strip() or f"Something went wrong while running {tool_name}."
        if human:
            return human
        if raw_text.strip():
            return raw_text.strip()
        leftover = result.get("structured") or result.get("raw")
        if leftover:
            return json.dumps(leftover, ensure_ascii=False)
        return "Done."
    except MCPClientError as e:
        return f"I couldn't complete that request ({tool_name}): {e}"
    except Exception as e:
        logger.exception("Unexpected tool call failure")
        return f"Unexpected error calling {tool_name}: {e}"


def _catalog_prompt() -> str:
    try:
        return prompt_block()
    except Exception:
        logger.exception("Failed to build portfolio prompt")
        return ""


def _agent_system_prompt(agent_config: Dict[str, Any], extra: str = "") -> str:
    parts = [
        agent_config.get("system_prompt_goals") or "",
        _catalog_prompt(),
    ]
    agent_id = (agent_config.get("agent_id") or "").strip()
    if not agent_id or agent_id in JIRA_AWARE_AGENT_IDS:
        parts.append(JIRA_FORMATTING_RULES)
    parts.append(extra)
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _enrich_tool_args(
    tool_name: str,
    arguments: Dict[str, Any],
    user_message: str,
    *,
    caller_agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    args = dict(arguments or {})
    haystack = " ".join(
        [
            user_message or "",
            str(args.get("question") or ""),
            str(args.get("task") or ""),
            str(args.get("pr_url") or ""),
            str(args.get("repo") or ""),
            str(args.get("agent_id") or ""),
            str(args.get("agent_url") or ""),
        ]
    )
    repo, pr_number = resolve_repo_and_pr(
        repo=args.get("repo"),
        pr_number=args.get("pr_number"),
        text=haystack,
    )
    project = (
        normalize_project_key(args.get("project_key") or args.get("project_keys"))
        or resolve_project(haystack)
        or ""
    )
    if not is_owner_repo(repo) and project:
        mapped = (repo_map().get(project) or "").strip()
        if is_owner_repo(mapped):
            repo = mapped
    if is_owner_repo(repo):
        args["repo"] = repo
    if pr_number is not None:
        args["pr_number"] = pr_number
        if is_owner_repo(repo):
            args.setdefault("pr_url", f"https://github.com/{repo}/pull/{pr_number}")
    agent_id = parse_cursor_agent_id(haystack)
    if agent_id and not str(args.get("agent_id") or "").strip():
        args["agent_id"] = agent_id
    if project:
        args.setdefault("project_key", project)
        if "ask_analytics" in (tool_name or "").lower():
            args["question"] = scrub_analytics_question(args.get("question") or user_message, project)
    if (tool_name or "").lower() == "create_jira_issue" and (
        (caller_agent_id or "").strip().lower() == "marketing"
    ):
        args.setdefault("marketing", True)
    return args


def _select_tool_via_llm(
    agent_id: str,
    agent_config: Dict[str, Any],
    user_message: str,
    tools: List[Dict[str, Any]],
    history: List[Dict[str, str]],
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Returns (response_text, tool_name, tool_args) — tool fields set if a tool should run."""
    llm, _model = get_llm_client(feature="chat")
    prompt_tools = _chief_direct_tools(tools) if agent_id == "chief" else tools
    tool_summary = _tools_summary(prompt_tools)
    extra = (
        _chief_routing_extra(tool_summary)
        if agent_id == "chief"
        else _specialist_json_extra(tool_summary)
    )
    system = _agent_system_prompt(agent_config, extra)
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
        target = _resolve_delegate_target(action.get("agent_id") or action.get("agent"))
        task = action.get("task") or user_message
        if not target:
            return (
                "I need a specialist to handle that (marketing, product, CTO, or DevOps).",
                None,
                None,
            )
        return "", f"__delegate__:{target}", {"task": task}

    return raw.strip(), None, None


def _run_openai_tool_loop(
    llm,
    messages: List[Dict[str, Any]],
    thread_id: str,
    *,
    mcp_client: Optional[MCPClient] = None,
    extra_tools: Optional[List[Dict[str, Any]]] = None,
    user_message: str = "",
) -> str:
    """OpenAI function-calling loop for chief of staff."""
    if not hasattr(llm, "_client"):
        return "Tool delegation requires an OpenAI model."

    openai_tools = list(DELEGATE_TOOL_DEFS)
    if extra_tools:
        openai_tools.extend(extra_tools)

    for _ in range(5):
        resp = llm._client.chat.completions.create(
            model=llm.model_name,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
            temperature=0.3,
        )
        record = getattr(llm, "record_openai_response", None)
        if callable(record):
            record(resp)
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
                result = _dispatch_chief_tool(
                    fn,
                    args,
                    user_message=user_message,
                    thread_id=thread_id,
                    mcp_client=mcp_client,
                ) or "Unknown tool."
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


def _agent_display_name(store, agent_id: Optional[str]) -> str:
    aid = (agent_id or "").strip() or "agent"
    agent = store.get_agent(aid) or {}
    name = str(agent.get("name") or "").strip()
    if name:
        return name
    return aid.replace("_", " ").title()


def _resolve_delegation_threads(
    store,
    *,
    source_thread_id: Optional[str],
    specialist_id: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (source_thread_id, specialist_thread_id, source_agent_id).

    specialist_thread_id is only set when the handoff should be mirrored into
    the receiving agent's own chat (i.e. source is a different agent).
    """
    if not source_thread_id:
        return None, None, None
    source = store.get_thread(source_thread_id)
    if not source:
        return source_thread_id, None, None
    source_agent_id = source.get("agent_id") or "chief"
    user_id = source.get("user_id")
    if not user_id or source_agent_id == specialist_id:
        return source_thread_id, None, source_agent_id
    specialist = store.get_or_create_agent_thread(user_id, specialist_id)
    specialist_thread_id = specialist.get("thread_id")
    if not specialist_thread_id or specialist_thread_id == source_thread_id:
        return source_thread_id, None, source_agent_id
    return source_thread_id, specialist_thread_id, source_agent_id


def _add_message_to_threads(
    store,
    thread_ids: List[Optional[str]],
    *,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    seen = set()
    for tid in thread_ids:
        if not tid or tid in seen:
            continue
        seen.add(tid)
        store.add_message(
            tid,
            role=role,
            content=content,
            metadata=dict(metadata) if metadata else None,
        )


def _post_specialist_handoff(
    store,
    *,
    specialist_thread_id: str,
    specialist_id: str,
    source_thread_id: Optional[str],
    source_agent_id: Optional[str],
    task: str,
) -> None:
    from_name = _agent_display_name(store, source_agent_id or "chief")
    store.add_message(
        specialist_thread_id,
        role="system",
        content=f"📥 Delegated from {from_name}:\n\n{task}",
        metadata={
            "type": "handoff",
            "from_agent_id": source_agent_id or "chief",
            "source_thread_id": source_thread_id,
            "agent_id": specialist_id,
        },
    )


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
    source_tid, specialist_tid, source_agent_id = _resolve_delegation_threads(
        store, source_thread_id=thread_id, specialist_id=agent_id
    )
    result_threads = [tid for tid in (source_tid, specialist_tid) if tid]

    def _work() -> str:
        if agent_id == "devops":
            clear_stale_pending_deploy(task, thread_id)
            if should_run_deploy_pipeline(task, thread_id):
                result = run_chat_deploy_pipeline(thread_id=thread_id, user_message=task)
                summary = result.get("summary") or "Done."
                if specialist_tid:
                    store.add_message(
                        specialist_tid,
                        role="assistant",
                        content=summary,
                        metadata={"agent_id": agent_id, "delegated": True},
                    )
                return summary
        client = _mcp_client()
        tools = _filter_tools_for_agent(client.list_tools(), agent_id)
        _, tool_name, tool_args = _select_tool_via_llm(agent_id, agent_config, task, tools, [])
        if tool_name and tool_name.startswith("__delegate__"):
            return "Specialist agents cannot delegate further."
        if tool_name:
            result = _run_tool_call(
                client,
                tool_name,
                _enrich_tool_args(tool_name, tool_args or {}, task, caller_agent_id=agent_id),
            )
        else:
            llm, _ = get_llm_client(feature="chat")
            system = _agent_system_prompt(agent_config)
            result = llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": task},
                ],
                temperature=0.4,
            )
        if result_threads:
            _add_message_to_threads(
                store,
                result_threads,
                role="assistant",
                content=result,
                metadata={"agent_id": agent_id, "delegated": True},
            )
        return result

    if specialist_tid:
        _post_specialist_handoff(
            store,
            specialist_thread_id=specialist_tid,
            specialist_id=agent_id,
            source_thread_id=source_tid,
            source_agent_id=source_agent_id,
            task=task,
        )

    if async_mode and thread_id:
        if source_tid:
            store.add_message(
                source_tid,
                role="system",
                content=f"⏳ {_agent_display_name(store, agent_id)} is working on your request…",
                metadata={"status": "in_progress", "agent_id": agent_id},
            )
        if specialist_tid:
            store.add_message(
                specialist_tid,
                role="system",
                content="⏳ Working on this request…",
                metadata={"status": "in_progress", "agent_id": agent_id},
            )

        def _bg():
            try:
                _work()
            except Exception as e:
                logger.exception("Async specialist task failed")
                _add_message_to_threads(
                    store,
                    result_threads,
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
    client_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Process a user message and return response metadata."""
    store = get_chat_store()
    thread = store.get_thread(thread_id)
    if not thread or thread.get("user_id") != user_id:
        raise ValueError("Thread not found")

    agent_id = thread.get("agent_id") or "chief"
    agent_config = store.get_agent(agent_id) or {}
    history = history or []

    user_metadata = {"client_id": client_id} if client_id else None
    store.add_message(thread_id, role="user", content=user_message, metadata=user_metadata)

    if agent_id == "chief":
        if is_deploy_start(user_message):
            response_text = run_specialist_task(
                "devops",
                user_message,
                thread_id=thread_id,
                async_mode=True,
            )
            msg = store.add_message(
                thread_id,
                role="assistant",
                content=response_text,
                metadata={"agent_id": "chief", "delegated": True, "delegated_to": "devops"},
            )
            return {"status": "complete", "message": msg}

        llm, model = get_llm_client(feature="chat")
        system = _agent_system_prompt(
            agent_config,
            "You are the Chief of Staff in the Bigas chat UI. Answer general questions directly. "
            f"{SPECIALIST_CAPABILITIES}"
            "Never say a specialist cannot do something listed above. Never 'virtually' delegate — "
            "call the specialist so they receive the task and reply in this thread. "
            "You may run a simple lookup yourself (status, health, a short analytics question) "
            "or look up Jira with lookup_jira / file a Task/Bug with create_jira_issue, "
            "but never trigger a production deploy — that is DevOps. "
            "Use the portfolio catalog: this team covers every listed Jira project and repo, not only one website.",
        )
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history[-10:])
        messages.append({"role": "user", "content": user_message})

        mcp_client, all_tools = _list_chief_mcp_tools()
        extra_openai = [_mcp_tool_to_openai_def(t) for t in _chief_direct_tools(all_tools)]

        if model.lower().startswith("gpt") and hasattr(llm, "_client"):
            response_text = _run_openai_tool_loop(
                llm,
                messages,
                thread_id,
                mcp_client=mcp_client,
                extra_tools=extra_openai,
                user_message=user_message,
            )
        else:
            response_text, tool_name, tool_args = _select_tool_via_llm(
                "chief", agent_config, user_message, all_tools, history
            )
            dispatched = _dispatch_chief_tool(
                tool_name,
                tool_args,
                user_message=user_message,
                thread_id=thread_id,
                mcp_client=mcp_client,
            )
            if dispatched is not None:
                response_text = dispatched

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
    if agent_id == "devops":
        clear_stale_pending_deploy(user_message, thread_id)
        if should_run_deploy_pipeline(user_message, thread_id):
            result = run_chat_deploy_pipeline(thread_id=thread_id, user_message=user_message)
            status = result.get("status") or "complete"
            all_msgs = store.list_messages(thread_id)
            if status == "in_progress":
                payload = {"status": "in_progress", "messages": all_msgs}
                if result.get("deploy_poll_active"):
                    payload["deploy_poll_active"] = True
                return payload
            last = store.list_messages(thread_id)
            assistant = next((m for m in reversed(last) if m.get("role") == "assistant"), None)
            return {"status": "complete", "message": assistant, "messages": all_msgs}

    client = _mcp_client()
    tools = _filter_tools_for_agent(client.list_tools(), agent_id)
    response_text, tool_name, tool_args = _select_tool_via_llm(
        agent_id, agent_config, user_message, tools, history
    )

    if tool_name:
        response_text = _run_tool_call(
            client,
            tool_name,
            _enrich_tool_args(
                tool_name, tool_args or {}, user_message, caller_agent_id=agent_id
            ),
        )

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
