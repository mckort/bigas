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
from bigas.chat.reply_style import (
    REPLY_STYLE,
    latest_user_text,
    looks_like_raw_tool_dump,
    tool_facts_from_messages,
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
CHAT_MAX_TOKENS = 16_384
CHAT_THINKING_BUDGET = 8_192
MARKETING_CHAT_MAX_TOKENS = CHAT_MAX_TOKENS
MARKETING_CHAT_THINKING_BUDGET = CHAT_THINKING_BUDGET

REASONING_APPROACH = """
Think step by step before acting:
1. What is the user actually trying to accomplish? (Not just what they asked, but why)
2. What information or actions are needed to help them?
3. Which tools would provide that? Call several if the answer spans GitHub, the board, Jira, analytics, or logs.
4. After getting results, what do they mean for the user's goal?

Be a thoughtful collaborator:
- Answer from knowledge when that's sufficient; use tools for live data or actions
- Synthesize tool results into useful insights — don't dump raw JSON
- Always use the default reply style (human-friendly markdown, never tool JSON)
- Reply in the user's language
- Prefer read-only lookups for questions. Do not run publishing pipelines
  (generate_weekly_x_post, progress_updates, weekly_* reports) unless the user asked
  to draft or post that artifact
- When your reasoning would help the user, share it briefly
- Take action rather than telling the user to do things you can do
""".strip()

ANALYTICS_GUIDANCE = """
When working with analytics data:
- Empty results are valid findings — they tell you something isn't tracked or configured
- Reason about what the absence of data means for the user's question
- Suggest concrete next steps for debugging (GTM Preview, event names, DebugView)
- Never treat missing data as a failure — it's information to act on
""".strip()

MARKETING_STRATEGY_RULES = """
Growth and strategy briefs (traffic, SEO, content, social, customers, conversion):
- You are a senior growth marketer. Reason from live evidence and established practice, not a generic checklist.
- Jira is context, not the answer. Looking up an Epic/goal is fine; never end with only ticket links or a Move button. The plan is the reply. File tickets only after the brief, and only for concrete follow-up work.
- Never send the user's whole strategy question to ask_analytics_question. That tool answers factual GA4 questions only (sessions last 28 days, sessions by source, landing pages, event counts). Ask several narrow questions if needed.
- Before a growth plan, gather: (1) current sessions and sources, (2) top landing pages, (3) conversions you can measure (store clicks, meeting bookings, key events), (4) get_latest_report and/or analyze_underperforming_pages when those exist. Then write the brief yourself.
- Structure: baseline vs the stated goal, the gap, 5–8 prioritized moves (impact × effort), what not to do given budget, and how to measure each move in GA4.
- Do not paste a tool's concise summary as the final answer. Synthesize.
""".strip()


CHIEF_PLAYBOOK = """
Coordination briefs:
- Answer yourself when you have the facts or can get them with a tool. Involve a specialist only when their domain judgment would change the answer.
- Jira and lookups are context. The reply is a clear recommendation and next step, not a ticket dump or a Move button.
- After a specialist returns, synthesize. Do not paste the handoff as the whole answer.
""".strip()

PRODUCT_PLAYBOOK = """
Product and backlog briefs:
- You are a senior PM. Investigate like Cursor: parse the question (product, date, shipped vs in progress), then gather evidence from every relevant source before answering.
- "What launched / shipped / is new after DATE" → call fetch_github_activity (commits and merged PRs; convert the date to since=YYYY-MM-DD) AND search_jira on the board (statusCategory = Done, project, updated/resolved >= that date). Synthesize user-facing changes; skip autofix and infra noise unless asked. Never use generate_weekly_x_post or progress_updates to answer that question.
- Jira/board is context, not the answer. Search or look up work to understand it; never end with only ticket links or a Move button.
- For planning or priority questions: gather open Epics/tasks, name the tradeoff, recommend now / next / later and why.
- File tickets only after the recommendation, and only for concrete work.
- Reply in the user's language. Do not paste a tool's JSON or concise summary as the final answer.
""".strip()

CTO_PLAYBOOK = """
Technical judgment briefs:
- You are a senior engineering lead. Reason from code, PRs, logs, and architecture — not a status dump.
- For review or incident questions: inspect the PR, logs, or failure first, then give a verdict (ship / fix first / blocked) with the why.
- Tradeoffs belong in the answer (risk, blast radius, effort). Raw review JSON or log dumps are not the reply.
- File tickets or trigger autofix only after the judgment, and only when follow-up work is clear.
""".strip()

CFO_PLAYBOOK = """
Cost briefs:
- You are a CFO. Numbers first, then a recommendation — never a generic savings list.
- Always call fetch_ai_usage (or the matching cost tool) before advising. Read totals by app, model tier, and feature.
- Structure: current spend vs the question, the drivers, 3–5 concrete moves with estimated impact, what not to cut.
- Do not move judgment work to a cheaper model without saying quality must not get worse.
- File a ticket only after the recommendation, for tracked cost work.
""".strip()

DEVOPS_PLAYBOOK = """
Ops briefs:
- Assess risk and current state before acting. Deploy is a decision, not the first tool call.
- For deploy, status, or incident: check risk or health, explain what you found, then act — or ask for confirm when risk is medium/high.
- The reply is status plus a recommendation (safe to ship / wait / hotfix). A workflow URL alone is not enough.
- After a failure: logs → likely cause → next action (hotfix PR or retry), not a raw log dump.
""".strip()


def _marketing_runtime_rules() -> str:
    return f"{ANALYTICS_GUIDANCE}\n\n{MARKETING_STRATEGY_RULES}"


def _agent_runtime_rules(agent_id: Optional[str] = None) -> str:
    aid = (agent_id or "").strip().lower()
    if aid == "marketing":
        return _marketing_runtime_rules()
    if aid == "product":
        return PRODUCT_PLAYBOOK
    if aid == "cto":
        return CTO_PLAYBOOK
    if aid == "cfo":
        return CFO_PLAYBOOK
    if aid == "devops":
        return DEVOPS_PLAYBOOK
    if aid == "chief":
        return CHIEF_PLAYBOOK
    return ""


def _chat_generation_kwargs(
    agent_id: Optional[str] = None, model: str = ""
) -> Dict[str, Any]:
    """Token/thinking budget so every chat agent can reason without being truncated."""
    _ = agent_id
    kwargs: Dict[str, Any] = {
        "temperature": 0.4,
        "max_tokens": CHAT_MAX_TOKENS,
    }
    if (model or "").lower().startswith("gemini"):
        kwargs["thinking_budget"] = CHAT_THINKING_BUDGET
    return kwargs

logger = logging.getLogger(__name__)

CONSULT_SPECIALIST_TOOL = {
    "type": "function",
    "function": {
        "name": "consult_specialist",
        "description": (
            "Involve a specialist when their domain expertise would genuinely improve the outcome. "
            "Think about why this specialist's knowledge matters for this specific task.\n\n"
            "Specialists:\n"
            "- marketing: GA4, ads, and organic growth/SEO/content strategy grounded in data\n"
            "- product: Product planning, Jira workflows, stakeholder communication\n"
            "- cto: Code review, architecture, deployment debugging\n"
            "- cfo: AI/infrastructure costs, usage analysis\n"
            "- devops: Deployments, site health, incident response"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "specialist": {
                    "type": "string",
                    "enum": ["marketing", "product", "cto", "cfo", "devops"],
                    "description": "Which specialist to involve.",
                },
                "task": {
                    "type": "string",
                    "description": "What you need the specialist to accomplish.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why this specialist's expertise is valuable for this task.",
                },
            },
            "required": ["specialist", "task", "reasoning"],
        },
    },
}

DELEGATE_TOOL_DEFS = [CONSULT_SPECIALIST_TOOL]

DELEGATE_MAP = {
    "consult_specialist": None,
    "delegate_to_marketing": "marketing",
    "delegate_to_product": "product",
    "delegate_to_cto": "cto",
    "delegate_to_cfo": "cfo",
    "delegate_to_devops": "devops",
}

SPECIALIST_IDS = ("marketing", "product", "cto", "cfo", "devops")

SPECIALIST_CAPABILITIES = (
    "Specialist expertise (involve them when their domain knowledge would improve the outcome):\n"
    "- marketing: Deep expertise in GA4, ads (Google/Meta/LinkedIn/Reddit), and organic "
    "growth/SEO/content strategy grounded in that data.\n"
    "- product: Expertise in product planning, Jira workflows, release notes, and stakeholder communication.\n"
    "- cto: Technical expertise in code review, architecture, QA, deployment debugging, and engineering operations.\n"
    "- cfo: Expertise in AI/infrastructure costs, usage analysis, and efficiency optimization.\n"
    "- devops: Expertise in deployments (GitHub Actions), site health, incident response, and CI/CD.\n\n"
    "All agents can use any tool. Choose to involve a specialist based on whether their expertise "
    "would genuinely help, not based on rigid ownership rules.\n"
)

# DEPRECATED: In the reasoning-based approach, the model decides when to involve
# specialists rather than forcing delegation by tool name. Kept for backwards
# compatibility with tests and documentation references.
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
    "cherry_pick_hotfix": "product",
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


def _dedupe_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return unique MCP tools by name (order preserved)."""
    seen = set()
    result: List[Dict[str, Any]] = []
    for tool in tools:
        name = (tool.get("name") or "").lower()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(tool)
    return result


def _chief_callable_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Chief has access to all tools - model reasons about when to delegate."""
    return _dedupe_tools(tools)


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
        "You are the Chief of Staff in the Bigas chat UI.\n\n"
        "How to approach requests:\n"
        "1. First understand what the user wants to accomplish\n"
        "2. Reason about whether you can help directly or if a specialist's expertise would add value\n"
        "3. Use tools when you need live data or to take action\n"
        "4. Synthesize results into a human-friendly reply — never paste tool JSON\n\n"
        f"{SPECIALIST_CAPABILITIES}\n"
        "When involving specialists, use consult_specialist with clear reasoning about why their "
        "expertise matters for this task.\n\n"
        "Tool tips:\n"
        "- lookup_jira: for specific issue keys or ranges\n"
        "- search_jira: for JQL queries (works on the internal board and Jira Cloud)\n"
        "- fetch_github_activity: commits and merged PRs since a date — use for what shipped\n"
        "- create_jira_issue: to file Tasks/Bugs — take action rather than asking the user to do it\n"
        "- Include project_key when the user mentions a product or site\n"
        "- For GitHub PRs, include repo and pr_number or pr_url\n\n"
        f"{CHIEF_PLAYBOOK}"
    )


def _specialist_native_extra(agent_id: Optional[str] = None) -> str:
    extra = (
        "You are responding in the Bigas chat interface as a specialist.\n\n"
        "How to approach requests:\n"
        "1. Understand what the user is trying to accomplish in your domain\n"
        "2. Reason about what information or actions would help\n"
        "3. Use tools to gather data or take action\n"
        "4. Synthesize results into a human-friendly reply — don't dump raw output\n"
        "5. Take action (create Jira issues, etc.) rather than asking the user to do it\n\n"
        "Tool tips:\n"
        "- Include project_key when the user mentions a product or site\n"
        "- For GitHub PRs, include repo and pr_number or pr_url\n"
        "- For Cursor autofix, include agent_id from cursor.com/agents/bc-... URLs\n"
        "- lookup_jira: for specific issue keys; search_jira: for JQL filters (internal board or Jira Cloud)\n"
        "- fetch_github_activity: what shipped since a date (commits + merged PRs)\n"
        "- create_jira_issue: look up Epic context first if needed, then create"
    )
    rules = _agent_runtime_rules(agent_id)
    if rules:
        extra = f"{extra}\n\n{rules}"
    return extra


def _chief_routing_extra(tool_summary: str) -> str:
    return (
        f"{_chief_native_extra()}\n\n"
        "Response format (JSON):\n"
        "To involve a specialist:\n"
        '{"action":"consult","specialist":"marketing|product|cto|cfo|devops","task":"...","reasoning":"why their expertise helps"}\n'
        "To call a tool:\n"
        '{"action":"tool","tool_name":"<name>","arguments":{...}}\n'
        "To answer directly:\n"
        '{"action":"answer","text":"<your reply>"}\n\n'
        f"Available tools:\n{tool_summary or '(none)'}"
    )


def _specialist_json_extra(tool_summary: str, agent_id: Optional[str] = None) -> str:
    return (
        f"{_specialist_native_extra(agent_id)}\n\n"
        "Response format (JSON):\n"
        "To call a tool:\n"
        '{"action":"tool","tool_name":"<name>","arguments":{...}}\n'
        "To answer directly:\n"
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
    """Run a COS tool call, handling specialist consultations when requested.
    
    In the reasoning-based approach, Chief can run any tool directly.
    Specialist consultation happens when the model explicitly chooses to involve them
    via consult_specialist or delegate_to_* tools.
    """
    if not tool_name:
        return None
    if not isinstance(tool_args, dict):
        tool_args = {}
    
    target = None
    if tool_name.startswith("__delegate__:"):
        target = tool_name.split(":", 1)[1]
    elif tool_name == "consult_specialist":
        target = _resolve_delegate_target(tool_args.get("specialist"))
    else:
        target = DELEGATE_MAP.get(tool_name) or _resolve_delegate_target(tool_name)
    
    if target:
        base_task = tool_args.get("task") or user_message
        reasoning = tool_args.get("reasoning") or ""
        extra = {k: v for k, v in tool_args.items() if k not in ("task", "specialist", "reasoning")}
        task_parts = [base_task]
        if reasoning:
            task_parts.append(f"Reasoning: {reasoning}")
        if extra:
            task_parts.append(f"Context: {json.dumps(extra)}")
        task = "\n\n".join(task_parts)
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
    """Return all tools for the agent - no longer filters by domain."""
    return _dedupe_tools(tools)


def _tools_summary(tools: List[Dict[str, Any]], limit: int = 80) -> str:
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


_RAW_DUMP_FALLBACK = (
    "I gathered the data but couldn't format it. Ask me to summarize it."
)


def _synthesize_human_reply(
    user_message: str,
    facts: str,
    *,
    llm=None,
    generation_kwargs: Optional[Dict[str, Any]] = None,
) -> str:
    """Rewrite internal tool JSON into the user-facing chat reply."""
    client = llm if llm is not None and hasattr(llm, "complete") else None
    if client is None:
        try:
            client, _model = get_llm_client(feature="chat")
        except Exception:
            logger.exception("No LLM client available to humanize tool dump")
            return ""
    kwargs = {
        "temperature": (generation_kwargs or {}).get("temperature", 0.4),
        "max_tokens": (generation_kwargs or {}).get("max_tokens", CHAT_MAX_TOKENS),
    }
    messages = [
        {
            "role": "system",
            "content": (
                f"{REPLY_STYLE}\n\n"
                "You are rewriting internal tool data into the user-facing chat reply. "
                "Reply in the user's language. Never output JSON."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{user_message}\n\n"
                "Internal tool data (do not paste this):\n\n"
                f"{facts}"
            ),
        },
    ]
    try:
        raw = client.complete(messages, **kwargs)
    except Exception:
        logger.exception("Failed to humanize tool dump")
        return ""
    text = (raw or "").strip() if isinstance(raw, str) else str(raw or "").strip()
    if not text or looks_like_raw_tool_dump(text):
        return ""
    return text


def _finalize_chat_reply(
    text: Optional[str],
    *,
    user_message: str,
    facts: str = "",
    llm=None,
    generation_kwargs: Optional[Dict[str, Any]] = None,
) -> str:
    """Guarantee the user never receives a raw tool dump as the reply."""
    candidate = text.strip() if isinstance(text, str) else str(text or "").strip()
    if not looks_like_raw_tool_dump(candidate):
        return candidate
    rewritten = _synthesize_human_reply(
        user_message,
        facts.strip() or candidate,
        llm=llm,
        generation_kwargs=generation_kwargs,
    )
    return rewritten or _RAW_DUMP_FALLBACK


_ANALYTICS_EMPTY_RE = re.compile(
    r"No GA4 data (returned|remaining after filtering)|Cannot provide analysis without real data",
    re.I,
)
_ANALYTICS_FAILED_RE = re.compile(r"Failed to process analytics question", re.I)


def _friendly_analytics_tool_failure(text: str) -> Optional[str]:
    """Rewrite GA4 empty/crash tool errors into a finding the agent can use."""
    blob = text or ""
    if _ANALYTICS_EMPTY_RE.search(blob):
        return (
            "GA4 returned no matching rows for that query. "
            "That is a valid finding (the event or metric is missing), not a crash. "
            "Tell the user what was missing and continue troubleshooting tracking/GTM if that was the question."
        )
    if _ANALYTICS_FAILED_RE.search(blob):
        return (
            "The GA4 query could not be completed. "
            "Do not paste this as the reply — summarize that the analytics lookup failed "
            "and keep helping with tracking (event name, GTM Preview, GA4 DebugView, key-event marking)."
        )
    return None


def _run_tool_call(client: MCPClient, tool_name: str, arguments: Dict[str, Any]) -> str:
    try:
        result = client.call_tool(tool_name, arguments)
        raw_text = result.get("text") or ""
        human = humanize_tool_result(raw_text) or humanize_tool_result(result.get("structured"))
        text = human or raw_text.strip()
        rewritten = _friendly_analytics_tool_failure(text)
        if rewritten:
            return rewritten
        if result.get("is_error"):
            return text or f"Something went wrong while running {tool_name}."
        if human:
            return human
        if raw_text.strip():
            return raw_text.strip()
        leftover = result.get("structured") or result.get("raw")
        if leftover:
            return json.dumps(leftover, ensure_ascii=False)
        return "Done."
    except MCPClientError as e:
        rewritten = _friendly_analytics_tool_failure(str(e))
        return rewritten or f"I couldn't complete that request ({tool_name}): {e}"
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
    agent_id: Optional[str] = None,
) -> str:
    parts = [
        agent_config.get("system_prompt_goals") or "",
        _catalog_prompt(),
    ]
    agent_id = (agent_id or agent_config.get("agent_id") or "").strip()
    if not agent_id or agent_id in JIRA_AWARE_AGENT_IDS:
        parts.append(JIRA_FORMATTING_RULES)
    parts.append(REASONING_APPROACH)
    parts.append(REPLY_STYLE)
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
        caller_agent_id or ""
    ).strip().lower() == "marketing":
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
    if (tool_name or "").lower() in {"generate_weekly_x_post", "progress_updates"}:
        args.setdefault("post_to_discord", False)
        args.setdefault("post_to_chat", False)
    return args


def _select_tool_via_llm(
    agent_id: str,
    agent_config: Dict[str, Any],
    user_message: str,
    tools: List[Dict[str, Any]],
    history: List[Dict[str, str]],
    *,
    user_id: Optional[str] = None,
    generation_kwargs: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Returns (response_text, tool_name, tool_args) — tool fields set if a tool should run."""
    llm, model = get_llm_client(feature="chat")
    prompt_tools = _chief_callable_tools(tools) if agent_id == "chief" else tools
    tool_summary = _tools_summary(prompt_tools)
    extra = (
        _chief_routing_extra(tool_summary)
        if agent_id == "chief"
        else _specialist_json_extra(tool_summary, agent_id=agent_id)
    )
    system = _agent_system_prompt(
        agent_config, extra, user_id=user_id, agent_id=agent_id
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_message})

    chat_kwargs = (
        generation_kwargs
        if generation_kwargs is not None
        else _chat_generation_kwargs(agent_id, model)
    )
    try:
        raw = llm.complete(messages, **chat_kwargs)
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

    if action.get("action") in ("delegate", "consult") and agent_id == "chief":
        target = _resolve_delegate_target(
            action.get("specialist") or action.get("agent_id") or action.get("agent")
        )
        task = action.get("task") or user_message
        reasoning = action.get("reasoning") or ""
        if not target:
            return (
                "I need to specify which specialist to involve (marketing, product, cto, cfo, or devops).",
                None,
                None,
            )
        return "", f"__delegate__:{target}", {"task": task, "reasoning": reasoning}

    return raw.strip(), None, None


def _is_terminal_handoff(tool_name: Optional[str]) -> bool:
    """Check if this tool call triggers a specialist handoff.
    
    In the reasoning-based approach, only explicit consultation requests
    (consult_specialist, delegate_to_*, or __delegate__:) cause handoffs.
    """
    name = (tool_name or "").strip()
    if name.startswith("__delegate__"):
        return True
    if name == "consult_specialist":
        return True
    if _resolve_delegate_target(name):
        return True
    return False


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
    generation_kwargs: Optional[Dict[str, Any]] = None,
) -> str:
    """Think → tool → observe → answer. User sees only the final answer."""
    observations: List[str] = []
    last_tool_text = ""
    history = history or []

    def finish(candidate: Optional[str]) -> str:
        return _finalize_chat_reply(
            candidate,
            user_message=user_message,
            facts="\n\n".join(observations),
            generation_kwargs=generation_kwargs,
        )

    for _round in range(MAX_AGENT_TOOL_ROUNDS):
        prompt_message = user_message
        if observations:
            prompt_message = (
                f"{user_message}\n\n"
                "Tool results (facts for you). Answer the user's question now in the "
                "default human-friendly reply style, or call another tool if you still "
                "need data. Never paste JSON, commits, or this dump as your reply.\n\n"
                + "\n\n".join(observations)
            )
        text, tool_name, tool_args = _select_tool_via_llm(
            agent_id,
            agent_config,
            prompt_message,
            tools,
            history,
            user_id=user_id,
            generation_kwargs=generation_kwargs,
        )
        if tool_name and _is_terminal_handoff(tool_name):
            return finish(
                run_tool(tool_name, tool_args or {}) or (text or "").strip() or "Done."
            )
        if not tool_name:
            if (text or "").strip():
                return finish(text)
            if observations:
                break
            if callable(fallback_complete):
                return finish(fallback_complete() or "I couldn't process that request.")
            return finish(last_tool_text or "I couldn't process that request.")
        result = run_tool(tool_name, tool_args or {}) or "Done."
        last_tool_text = result
        observations.append(f"### {tool_name}\n{result}")

    if observations:
        text, tool_name, _tool_args = _select_tool_via_llm(
            agent_id,
            agent_config,
            (
                f"{user_message}\n\n"
                "You must answer now (action=answer) in the default human-friendly "
                "reply style. Use these tool results. Do not call another tool. "
                "Never paste JSON.\n\n"
                + "\n\n".join(observations)
            ),
            tools,
            history,
            user_id=user_id,
            generation_kwargs=generation_kwargs,
        )
        if (text or "").strip():
            return finish(text)
        if callable(fallback_complete):
            forced = fallback_complete()
            if (forced or "").strip():
                return finish(forced)
    return finish(last_tool_text or "I couldn't process that request.")


def _run_native_tool_loop(
    llm,
    messages: List[Dict[str, Any]],
    openai_tools: List[Dict[str, Any]],
    *,
    run_tool,
    generation_kwargs: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Provider-native function calling. Returns None if the first turn cannot start."""
    if not hasattr(llm, "complete_detailed"):
        return None
    first = True
    last_tool_text = ""
    question = latest_user_text(messages)

    def finish(candidate: Optional[str]) -> str:
        return _finalize_chat_reply(
            candidate,
            user_message=question,
            facts=tool_facts_from_messages(messages),
            llm=llm,
            generation_kwargs=generation_kwargs,
        )

    for _round in range(MAX_AGENT_TOOL_ROUNDS):
        try:
            kwargs: Dict[str, Any] = {"temperature": 0.3}
            if generation_kwargs:
                kwargs.update(generation_kwargs)
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
                    return finish(
                        run_tool(tc.name, tc.arguments or {}) or last_tool_text or "Done."
                    )
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
            return finish(completion.text)
        # Empty native turn (including Gemini MALFORMED_FUNCTION_CALL) → JSON fallback.
        if first_turn:
            return None
        break
    if not last_tool_text:
        return "I wasn't able to complete that request."
    return finish(last_tool_text)


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
    llm, model = get_llm_client(feature="chat")
    extra = _chief_native_extra() if agent_id == "chief" else _specialist_native_extra(agent_id)
    system = _agent_system_prompt(
        agent_config, extra, user_id=user_id, agent_id=agent_id
    )
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend((history or [])[-10:])
    messages.append({"role": "user", "content": user_message})
    openai_tools = list(extra_openai_tools or [])
    openai_tools.extend(_mcp_tool_to_openai_def(tool) for tool in tools)
    try:
        native = _run_native_tool_loop(
            llm,
            messages,
            openai_tools,
            run_tool=run_tool,
            generation_kwargs=_chat_generation_kwargs(agent_id, model),
        )
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
            generation_kwargs=_chat_generation_kwargs(agent_id, model),
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
            llm, model = get_llm_client(feature="chat")
            system = _agent_system_prompt(
                agent_config, user_id=chat_user_id, agent_id=agent_id
            )
            return llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": task},
                ],
                **_chat_generation_kwargs(agent_id, model),
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
    agent_config = store.get_agent(agent_id) or {
        "agent_id": agent_id,
        "system_prompt_goals": "",
    }
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
