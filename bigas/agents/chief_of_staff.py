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
from bigas.resources.product.create_jira_issue.lookup import parse_issue_keys
from bigas.utils.mcp_client import MCPClient, MCPClientError

MAX_AGENT_TOOL_ROUNDS = 10

COWORKER_RULES = """
You are a coworker, not a tool printer.
- Answer from what you know when that is enough. Call tools only for live data or a side effect.
- After you receive tool results, answer the question directly. Do not paste raw tool output as your reply.
- You may call several tools, or the same tool again with different arguments, before answering.
- lookup_jira accepts several issue keys or a range (BIG-15 to BIG-18). Use that for named-key status questions.
- search_jira takes JQL. Use it when the user described a filter (status, type, text) without keys. Do not invent issue keys.
""".strip()

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
        "weekly_okr",
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
    "cfo": (
        "fetch_ai_usage",
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
            "description": "Delegate an engineering/CTO task (PR review, QA, failed-deploy hotfix, monitoring).",
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
            "name": "delegate_to_cfo",
            "description": "Delegate an AI/GCP cost analysis task to the CFO (usage, Gemini spend, savings).",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Clear task description for the CFO agent."},
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
    "delegate_to_cfo": "cfo",
    "delegate_to_devops": "devops",
}

SPECIALIST_IDS = ("marketing", "product", "cto", "cfo", "devops")

SPECIALIST_CAPABILITIES = (
    "Specialists (always delegate domain work to them — they have the tools and will reply here):\n"
    "- marketing: GA4, ads (Google/Meta/LinkedIn/Reddit), trends, weekly/portfolio reports.\n"
    "- product: Jira release notes, progress updates, social drafts, board automation.\n"
    "- cto: GitHub PR review, autofix, QA, failed-deploy hotfix, monitoring.\n"
    "- cfo: AI/GCP cost, Gemini spend (Bigas + VC Field Assistant), fetch_ai_usage, savings.\n"
    "- devops: production deploy via GitHub Actions (including manual trigger_deployment), "
    "deploy status, site health, CI logs, hotfix PRs. vcfieldassistant/VFA is a DevOps deploy.\n"
    "Every specialist and Chief of Staff can call lookup_jira, search_jira (JQL), and create_jira_issue (Task/Bug). "
    "Look up Epics when needed, then decide whether the new work belongs under one or should be standalone. "
    "Never tell the user to create the Jira issue themselves.\n"
)

# Shared with every specialist; COS may also call these without a handoff.
SHARED_AGENT_TOOLS = frozenset({"create_jira_issue", "lookup_jira", "search_jira"})

# Writes and heavy pipelines: if COS names these, rewrite to a specialist handoff.
# Everything else in the MCP catalog is Chief-callable (read-only + create_jira_issue).
MUST_DELEGATE_TOOLS = {
    "trigger_deployment": "devops",
    "create_github_pr": "devops",
    "github_workflow_run": "devops",
    "autofix_pr": "cto",
    "autofix_followup": "cto",
    "fix_failed_deployment": "cto",
    "review_and_comment_pr": "cto",
    "run_qa": "cto",
    "notify_pr_merged": "cto",
    "weekly_cto_ai_report": "cfo",
    "fetch_ai_usage": "cfo",
    "create_release_notes": "product",
    "progress_updates": "product",
    "generate_weekly_x_post": "product",
    "jira_status_automation": "product",
    "jira_status_automation_job": "product",
    "weekly_analytics_report": "marketing",
    "weekly_analytics_report_async": "marketing",
    "run_reddit_portfolio_report": "marketing",
    "run_reddit_portfolio_report_async": "marketing",
    "run_linkedin_portfolio_report": "marketing",
    "run_linkedin_portfolio_report_async": "marketing",
    "run_google_ads_portfolio_report": "marketing",
    "run_google_ads_portfolio_report_async": "marketing",
    "run_meta_portfolio_report": "marketing",
    "run_meta_portfolio_report_async": "marketing",
    "run_cross_platform_marketing_analysis": "marketing",
    "run_cross_platform_marketing_analysis_async": "marketing",
    "cleanup_old_reports": "marketing",
    "cleanup_old_activity": "marketing",
    "linkedin_exchange_code": "marketing",
    "reddit_exchange_code": "marketing",
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


def _must_delegate_names() -> set:
    return {name.lower() for name in MUST_DELEGATE_TOOLS}


def _chief_callable_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Chief may call any catalog tool that is not a write/pipeline handoff."""
    blocked = _must_delegate_names()
    shared = {name.lower() for name in SHARED_AGENT_TOOLS}
    shared_out: List[Dict[str, Any]] = []
    rest: List[Dict[str, Any]] = []
    seen = set()
    for tool in tools:
        name = (tool.get("name") or "").lower()
        if not name or name in seen or name in blocked:
            continue
        seen.add(name)
        if name in shared:
            shared_out.append(tool)
        else:
            rest.append(tool)
    return shared_out + rest


def _chief_direct_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _chief_callable_tools(tools)


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


def _chief_native_extra() -> str:
    return (
        "You are the Chief of Staff in the Bigas chat UI. "
        "Answer general questions directly from knowledge when that is enough. "
        "Call tools only when you need live data or a side effect.\n"
        f"{SPECIALIST_CAPABILITIES}"
        "Never say a specialist cannot do something listed above. Never 'virtually' delegate "
        "or offer to build a tool that already exists — call delegate_to_* so the specialist "
        "receives the task and replies in this thread.\n"
        "For named Jira keys or ranges, call lookup_jira. "
        "For filters without keys, write JQL and call search_jira. Do not invent issue keys. "
        "You may file a Task/Bug with create_jira_issue. "
        "After tools return, answer the user yourself. "
        "Do NOT trigger deploys, autofix, weekly reports, or other specialist pipelines yourself.\n"
        "Include project_key when the user named a product, site, or Jira key. "
        "For GitHub PRs, pass repo as owner/repo and pr_number, or pass pr_url."
    )


def _specialist_native_extra() -> str:
    return (
        "You are responding in the Bigas chat interface. "
        "Call a tool when you need facts or a side effect; otherwise answer in your own words. "
        "Never use a raw lookup dump as the final reply. "
        "Include project_key when the user named a product, site, or Jira key. "
        "For GitHub PRs, pass repo as owner/repo and pr_number, or pass pr_url "
        "(a github.com/.../pull/N link is enough). "
        "For Cursor autofix follow-up, include agent_id from a cursor.com/agents/bc-... URL. "
        "Named Jira keys → lookup_jira. Filters without keys → search_jira with JQL. "
        "To file work in Jira, call lookup_jira if you need Epic/parent context, then create_jira_issue. "
        "Do not ask the user for an Epic key or to create the ticket. "
        "Only set parent_epic_key when the new work belongs under that Epic; otherwise create it standalone."
    )


def _chief_routing_extra(tool_summary: str) -> str:
    return (
        f"{_chief_native_extra()}\n\n"
        "If you should delegate, respond with ONLY:\n"
        '{"action":"delegate","agent_id":"marketing|product|cto|cfo|devops","task":"<clear task>"}\n'
        "If you should call a tool, respond with ONLY:\n"
        '{"action":"tool","tool_name":"<name>","arguments":{...}}\n'
        "Otherwise respond with ONLY:\n"
        '{"action":"answer","text":"<your reply>"}\n\n'
        f"Tools you may call directly:\n{tool_summary or '(none — delegate instead)'}"
    )


def _specialist_json_extra(tool_summary: str) -> str:
    return (
        "You are responding in the Bigas chat interface. "
        "If you need facts from a backend tool, respond with ONLY a JSON object:\n"
        '{"action":"tool","tool_name":"<name>","arguments":{...}}\n'
        "You will see the tool result and then must answer (or call another tool). "
        "Never use a raw lookup dump as the final reply. "
        "Include project_key when the user named a product, site, or Jira key. "
        "For GitHub PRs, pass repo as owner/repo and pr_number, or pass pr_url "
        "(a github.com/.../pull/N link is enough). "
        "For Cursor autofix follow-up, include agent_id from a cursor.com/agents/bc-... URL. "
        "Named Jira keys → lookup_jira. Filters without keys → search_jira with JQL. "
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
    user_id: Optional[str] = None,
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
    if mcp_client:
        return _run_tool_call(
            mcp_client,
            tool_name,
            _enrich_tool_args(
                tool_name,
                tool_args,
                user_message,
                caller_agent_id="chief",
                user_id=user_id,
            ),
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


def _tools_summary(tools: List[Dict[str, Any]], limit: int = 40) -> str:
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


def _agent_system_prompt(
    agent_config: Dict[str, Any],
    extra: str = "",
    *,
    user_id: Optional[str] = None,
) -> str:
    parts = [
        agent_config.get("system_prompt_goals") or "",
        _catalog_prompt(),
    ]
    agent_id = (agent_config.get("agent_id") or "").strip()
    if not agent_id or agent_id in JIRA_AWARE_AGENT_IDS:
        parts.append(JIRA_FORMATTING_RULES)
    parts.append(COWORKER_RULES)
    parts.append(extra)
    if agent_id:
        try:
            from bigas.okr.priming import okr_priming_block_for_agent

            parts.append(okr_priming_block_for_agent(agent_id, user_id=user_id))
        except Exception:
            logger.exception("OKR priming skipped for %s", agent_id)
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _enrich_tool_args(
    tool_name: str,
    arguments: Dict[str, Any],
    user_message: str,
    *,
    caller_agent_id: Optional[str] = None,
    user_id: Optional[str] = None,
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
    if (tool_name or "").lower() == "create_jira_issue" and user_id:
        args.setdefault("user_id", user_id)
    if (tool_name or "").lower() == "lookup_jira":
        raw_key = str(
            args.get("issue_key") or args.get("issue") or args.get("key") or ""
        ).strip()
        keys = parse_issue_keys(raw_key, args.get("issue_keys"), haystack)
        if keys and not raw_key:
            args["issue_key"] = ", ".join(keys)
    return args


def _select_tool_via_llm(
    agent_id: str,
    agent_config: Dict[str, Any],
    user_message: str,
    tools: List[Dict[str, Any]],
    history: List[Dict[str, str]],
    *,
    user_id: Optional[str] = None,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Returns (response_text, tool_name, tool_args) — tool fields set if a tool should run."""
    llm, _model = get_llm_client(feature="chat")
    prompt_tools = _chief_callable_tools(tools) if agent_id == "chief" else tools
    tool_summary = _tools_summary(prompt_tools)
    extra = (
        _chief_routing_extra(tool_summary)
        if agent_id == "chief"
        else _specialist_json_extra(tool_summary)
    )
    system = _agent_system_prompt(agent_config, extra, user_id=user_id)
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_message})

    try:
        raw = llm.complete(messages, temperature=0.2)
    except Exception as exc:
        from bigas.llm.gemini_client import is_malformed_function_call

        if is_malformed_function_call(exc):
            logger.exception("Chat LLM MALFORMED_FUNCTION_CALL during JSON tool select")
            return (
                "I hit a model error while calling tools. Try that question again, "
                "or ask the Marketing Analyst specialist directly for GA4 work.",
                None,
                None,
            )
        raise
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


def _is_terminal_handoff(tool_name: Optional[str]) -> bool:
    name = (tool_name or "").strip()
    if name.startswith("__delegate__"):
        return True
    if _resolve_delegate_target(name):
        return True
    return name.lower() in MUST_DELEGATE_TOOLS


def _run_json_agent_loop(
    *,
    agent_id: str,
    agent_config: Dict[str, Any],
    user_message: str,
    tools: List[Dict[str, Any]],
    history: Optional[List[Dict[str, str]]],
    run_tool,
    fallback_complete=None,
    user_id: Optional[str] = None,
) -> str:
    """Think → tool → observe → answer. User sees only the final answer."""
    observations: List[str] = []
    last_tool_text = ""
    history = history or []

    for _round in range(MAX_AGENT_TOOL_ROUNDS):
        prompt_message = user_message
        if observations:
            prompt_message = (
                f"{user_message}\n\n"
                "Tool results (facts for you). Answer the user's question now, "
                "or call another tool if you still need data. "
                "Do not paste this dump as your reply.\n\n"
                + "\n\n".join(observations)
            )
        text, tool_name, tool_args = _select_tool_via_llm(
            agent_id, agent_config, prompt_message, tools, history, user_id=user_id
        )
        if tool_name and _is_terminal_handoff(tool_name):
            return run_tool(tool_name, tool_args or {}) or (text or "").strip() or "Done."
        if not tool_name:
            if (text or "").strip():
                return text.strip()
            if observations:
                break
            if callable(fallback_complete):
                return fallback_complete() or "I couldn't process that request."
            return last_tool_text or "I couldn't process that request."
        result = run_tool(tool_name, tool_args or {}) or "Done."
        last_tool_text = result
        observations.append(f"### {tool_name}\n{result}")

    if observations:
        text, tool_name, _tool_args = _select_tool_via_llm(
            agent_id,
            agent_config,
            (
                f"{user_message}\n\n"
                "You must answer now (action=answer). Use these tool results. "
                "Do not call another tool.\n\n"
                + "\n\n".join(observations)
            ),
            tools,
            history,
            user_id=user_id,
        )
        if (text or "").strip():
            return text.strip()
        if callable(fallback_complete):
            forced = fallback_complete()
            if (forced or "").strip():
                return forced.strip()
    return last_tool_text or "I couldn't process that request."


def _run_native_tool_loop(
    llm,
    messages: List[Dict[str, Any]],
    openai_tools: List[Dict[str, Any]],
    *,
    run_tool,
) -> Optional[str]:
    """Provider-native function calling. Returns None if the first turn cannot start."""
    if not hasattr(llm, "complete_detailed"):
        return None
    first = True
    last_tool_text = ""
    for _round in range(MAX_AGENT_TOOL_ROUNDS):
        try:
            kwargs: Dict[str, Any] = {"temperature": 0.3}
            if openai_tools:
                kwargs["tools"] = openai_tools
                kwargs["tool_choice"] = "auto"
            completion = llm.complete_detailed(messages, **kwargs)
        except Exception:
            logger.exception("Native tool-calling turn failed")
            if first:
                return None
            break
        first_turn = first
        first = False
        if completion.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": completion.text or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments or {}),
                            },
                        }
                        for tc in completion.tool_calls
                    ],
                }
            )
            for tc in completion.tool_calls:
                if _is_terminal_handoff(tc.name):
                    return run_tool(tc.name, tc.arguments or {}) or last_tool_text or "Done."
                result = run_tool(tc.name, tc.arguments or {}) or "Done."
                last_tool_text = result
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    }
                )
            continue
        if (completion.text or "").strip():
            return completion.text.strip()
        # Empty native turn (including Gemini MALFORMED_FUNCTION_CALL) → JSON fallback.
        if first_turn:
            return None
        break
    return last_tool_text or "I wasn't able to complete that request."


def _run_agent_with_tools(
    *,
    agent_id: str,
    agent_config: Dict[str, Any],
    user_message: str,
    tools: List[Dict[str, Any]],
    history: Optional[List[Dict[str, str]]],
    run_tool,
    extra_openai_tools: Optional[List[Dict[str, Any]]] = None,
    fallback_complete=None,
    user_id: Optional[str] = None,
) -> str:
    """Native tool loop first; JSON action loop if the provider rejects tools."""
    llm, _model = get_llm_client(feature="chat")
    extra = _chief_native_extra() if agent_id == "chief" else _specialist_native_extra()
    system = _agent_system_prompt(agent_config, extra, user_id=user_id)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend((history or [])[-10:])
    messages.append({"role": "user", "content": user_message})
    openai_tools = list(extra_openai_tools or [])
    openai_tools.extend(_mcp_tool_to_openai_def(tool) for tool in tools)
    try:
        native = _run_native_tool_loop(llm, messages, openai_tools, run_tool=run_tool)
        if native is not None:
            return native
        return _run_json_agent_loop(
            agent_id=agent_id,
            agent_config=agent_config,
            user_message=user_message,
            tools=tools,
            history=history,
            run_tool=run_tool,
            fallback_complete=fallback_complete,
            user_id=user_id,
        )
    except Exception as exc:
        from bigas.llm.gemini_client import is_malformed_function_call

        if is_malformed_function_call(exc):
            logger.exception("Chat LLM MALFORMED_FUNCTION_CALL")
            return (
                "I hit a model error while calling tools. Try that question again, "
                "or ask the Marketing Analyst specialist directly for GA4 work."
            )
        raise


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
    chat_user_id = None
    if thread_id:
        source_thread = store.get_thread(thread_id)
        chat_user_id = (source_thread or {}).get("user_id")

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

        def _run_specialist_tool(tool_name: str, tool_args: Dict[str, Any]) -> str:
            if tool_name.startswith("__delegate__"):
                return "Specialist agents cannot delegate further."
            return _run_tool_call(
                client,
                tool_name,
                _enrich_tool_args(
                    tool_name,
                    tool_args or {},
                    task,
                    caller_agent_id=agent_id,
                    user_id=chat_user_id,
                ),
            )

        def _fallback_complete() -> str:
            llm, _ = get_llm_client(feature="chat")
            system = _agent_system_prompt(agent_config, user_id=chat_user_id)
            return llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": task},
                ],
                temperature=0.4,
            )

        result = _run_agent_with_tools(
            agent_id=agent_id,
            agent_config=agent_config,
            user_message=task,
            tools=tools,
            history=[],
            run_tool=_run_specialist_tool,
            fallback_complete=_fallback_complete,
            user_id=chat_user_id,
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
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Process a user message and return response metadata."""
    store = get_chat_store()
    thread = store.get_thread(thread_id)
    if not thread or thread.get("user_id") != user_id:
        raise ValueError("Thread not found")

    agent_id = thread.get("agent_id") or "chief"
    agent_config = store.get_agent(agent_id) or {}
    history = history or []
    from bigas.tickets.attachments import message_text_for_llm

    user_metadata: Dict[str, Any] = {}
    if client_id:
        user_metadata["client_id"] = client_id
    if attachments:
        user_metadata["attachments"] = list(attachments)
    store.add_message(
        thread_id,
        role="user",
        content=user_message,
        metadata=user_metadata or None,
    )
    llm_user_message = message_text_for_llm(
        {"content": user_message, "metadata": {"attachments": list(attachments or [])}}
    )

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

        mcp_client, all_tools = _list_chief_mcp_tools()
        callable_tools = _chief_callable_tools(all_tools)
        response_text = _run_agent_with_tools(
            agent_id="chief",
            agent_config=agent_config,
            user_message=llm_user_message,
            tools=callable_tools,
            history=history,
            extra_openai_tools=list(DELEGATE_TOOL_DEFS),
            user_id=user_id,
            run_tool=lambda name, args: _dispatch_chief_tool(
                name,
                args,
                user_message=llm_user_message,
                thread_id=thread_id,
                mcp_client=mcp_client,
                user_id=user_id,
            )
            or "Done.",
        )

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
    response_text = _run_agent_with_tools(
        agent_id=agent_id,
        agent_config=agent_config,
        user_message=llm_user_message,
        tools=tools,
        history=history,
        user_id=user_id,
        run_tool=lambda name, args: _run_tool_call(
            client,
            name,
            _enrich_tool_args(
                name,
                args or {},
                llm_user_message,
                caller_agent_id=agent_id,
                user_id=user_id,
            ),
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
