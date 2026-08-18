"""Automated MCP QA agent: diff-driven tool testing and qualitative evaluation."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from bigas.discord_webhook import post_long_to_discord, post_to_discord
from bigas.llm.factory import get_llm_client
from bigas.resources.cto.qa_agent.drafts import (
    DEFAULT_TTL_HOURS,
    GcsQADraftStore,
    InMemoryQADraftStore,
    QADraftStore,
    is_expired,
)
from bigas.resources.cto.qa_agent.prompts import (
    EVALUATE_SYSTEM_PROMPT,
    PLAN_SYSTEM_PROMPT,
    build_evaluate_user_prompt,
    build_plan_user_prompt,
)
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
)
from bigas.resources.product.x_posts.service import public_base_url
from bigas.resources.product.x_posts.signing import sign_draft_id, signing_secret
from bigas.utils.mcp_client import MCPClient, MCPClientError

logger = logging.getLogger(__name__)

BIGAS_QA_MARKER = "[bigas-qa]"


class QAAgentError(RuntimeError):
    pass


def _extract_json(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    if not t:
        return {}
    if "```" in t:
        if "```json" in t:
            t = t.split("```json", 1)[1].split("```", 1)[0].strip()
        else:
            t = t.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        parsed = json.loads(t)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        start = t.find("{")
        end = t.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(t[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.warning("Failed to parse QA agent LLM JSON")
            return {}


def _webhook(env_name: str) -> str:
    url = (os.environ.get(env_name) or "").strip()
    if not url or url.lower().startswith("placeholder"):
        return ""
    return url


def _cto_project_key() -> str:
    return (
        (os.environ.get("JIRA_CTO_PROJECT_KEY") or "").strip()
        or (os.environ.get("JIRA_PROJECT_KEY") or "").split(",")[0].strip()
        or "BIG"
    ).upper()


def _pm_project_key() -> str:
    return (
        (os.environ.get("JIRA_PM_PROJECT_KEY") or "").strip()
        or (os.environ.get("JIRA_PROJECT_KEY") or "").split(",")[0].strip()
        or "BIG"
    ).upper()


def _ttl_hours() -> int:
    raw = (os.environ.get("QA_PROPOSAL_TTL_HOURS") or "").strip()
    if not raw:
        return DEFAULT_TTL_HOURS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_TTL_HOURS


def review_url(proposal_id: str, *, base_url: str, token: str) -> str:
    query = urlencode({"token": token})
    return f"{base_url.rstrip('/')}/api/qa-proposals/{proposal_id}?{query}"


class QAAgentService:
    def __init__(
        self,
        *,
        draft_store: Optional[QADraftStore] = None,
        llm_model: Optional[str] = None,
    ) -> None:
        self._store = draft_store
        self._explicit_model = llm_model
        self._llm = None
        self._model = ""

    def _llm_client(self):
        if self._llm is None:
            self._llm, self._model = get_llm_client(
                feature="qa_agent",
                explicit_model=self._explicit_model,
            )
        return self._llm

    def _store_or_default(self) -> QADraftStore:
        if self._store is None:
            self._store = GcsQADraftStore()
        return self._store

    def run(
        self,
        *,
        diff: str,
        mcp_endpoint_url: str,
        mcp_auth_token: Optional[str] = None,
        pr_url: Optional[str] = None,
        public_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not (diff or "").strip():
            raise QAAgentError("diff is required")
        if not (mcp_endpoint_url or "").strip():
            raise QAAgentError("mcp_endpoint_url is required")

        client = MCPClient(
            mcp_endpoint_url.strip(),
            auth_token=mcp_auth_token,
            exclude_slow_tools=True,
        )
        try:
            tools = client.list_tools()
        except MCPClientError as e:
            raise QAAgentError(str(e)) from e

        plan = self._plan_tests(diff=diff, tools=tools, pr_url=pr_url or "")
        planned = plan.get("tools") or []
        if not isinstance(planned, list):
            planned = []

        results: List[Dict[str, Any]] = []
        for entry in planned[:5]:
            if not isinstance(entry, dict):
                continue
            tool_name = (entry.get("name") or "").strip()
            if not tool_name:
                continue
            arguments = entry.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            tool_result = self._execute_tool(client, tool_name, arguments)
            evaluation = self._evaluate_result(
                tool_name=tool_name,
                arguments=arguments,
                diff=diff,
                tool_result=tool_result,
                pr_url=pr_url or "",
            )
            routed = self._route_evaluation(
                evaluation=evaluation,
                tool_name=tool_name,
                pr_url=pr_url or "",
                public_url=public_url,
            )
            results.append(
                {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "rationale": entry.get("rationale"),
                    "execution": tool_result,
                    "evaluation": evaluation,
                    "routing": routed,
                }
            )

        summary = self._summarize_run(results)
        self._notify_qa_channel(summary, pr_url=pr_url or "")
        return {
            "ok": True,
            "model": self._model,
            "tools_available": len(tools),
            "tools_planned": len(planned),
            "tools_tested": len(results),
            "summary": summary,
            "results": results,
        }

    def _plan_tests(
        self, *, diff: str, tools: List[Dict[str, Any]], pr_url: str
    ) -> Dict[str, Any]:
        try:
            content = self._llm_client().complete(
                messages=[
                    {"role": "system", "content": PLAN_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": build_plan_user_prompt(
                            diff=diff, tools=tools, pr_url=pr_url
                        ),
                    },
                ],
                max_tokens=2500,
                temperature=0.2,
            )
        except Exception as e:
            raise QAAgentError(f"Test planning LLM request failed: {e}") from e
        return _extract_json(content)

    def _execute_tool(
        self, client: MCPClient, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            return client.call_tool(tool_name, arguments)
        except MCPClientError as e:
            return {
                "is_error": True,
                "text": str(e),
                "structured": None,
                "error": str(e),
            }

    def _evaluate_result(
        self,
        *,
        tool_name: str,
        arguments: Dict[str, Any],
        diff: str,
        tool_result: Dict[str, Any],
        pr_url: str,
    ) -> Dict[str, Any]:
        try:
            content = self._llm_client().complete(
                messages=[
                    {"role": "system", "content": EVALUATE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": build_evaluate_user_prompt(
                            tool_name=tool_name,
                            arguments=arguments,
                            diff=diff,
                            tool_result=tool_result,
                            pr_url=pr_url,
                        ),
                    },
                ],
                max_tokens=3000,
                temperature=0.2,
            )
        except Exception as e:
            logger.warning("QA evaluation LLM failed for %s", tool_name, exc_info=True)
            return {
                "status": "improvement",
                "title": f"QA evaluation failed for {tool_name}",
                "proposal": f"Automated evaluation could not run: {e}",
                "summary": "Evaluation error",
            }
        parsed = _extract_json(content)
        status = (parsed.get("status") or "improvement").strip().lower()
        if status not in {"excellent", "improvement", "new_feature"}:
            status = "improvement"
        return {
            "status": status,
            "title": str(parsed.get("title") or f"Improve {tool_name} output").strip(),
            "proposal": str(parsed.get("proposal") or "").strip(),
            "summary": str(parsed.get("summary") or "").strip(),
        }

    def _route_evaluation(
        self,
        *,
        evaluation: Dict[str, Any],
        tool_name: str,
        pr_url: str,
        public_url: Optional[str],
    ) -> Dict[str, Any]:
        status = evaluation.get("status")
        if status == "excellent":
            return {"action": "none", "status": status}

        if status == "new_feature":
            return self._create_pm_feature_issue(
                evaluation=evaluation,
                tool_name=tool_name,
                pr_url=pr_url,
            )

        return self._store_improvement_proposal(
            evaluation=evaluation,
            tool_name=tool_name,
            pr_url=pr_url,
            public_url=public_url,
        )

    def _create_pm_feature_issue(
        self,
        *,
        evaluation: Dict[str, Any],
        tool_name: str,
        pr_url: str,
    ) -> Dict[str, Any]:
        title = evaluation.get("title") or f"New feature suggested by QA ({tool_name})"
        proposal = evaluation.get("proposal") or evaluation.get("summary") or ""
        body = self._jira_body(proposal, tool_name=tool_name, pr_url=pr_url)
        issue = self._create_jira_issue(
            project_key=_pm_project_key(),
            summary=title,
            description_markdown=body,
        )
        issue_key = issue.get("key") or ""
        issue_url = issue.get("url") or ""
        msg = (
            f"**QA agent: new feature suggested**\n"
            f"Tool: `{tool_name}`\n"
            f"Jira: {issue_key} {issue_url}".strip()
        )
        if pr_url:
            msg += f"\nPR: {pr_url}"
        webhook = _webhook("DISCORD_WEBHOOK_URL_PRODUCT")
        if webhook:
            post_to_discord(webhook, msg)
            if proposal:
                post_long_to_discord(webhook, proposal)
        return {
            "action": "jira_pm",
            "status": "new_feature",
            "issue_key": issue_key,
            "issue_url": issue_url,
        }

    def _store_improvement_proposal(
        self,
        *,
        evaluation: Dict[str, Any],
        tool_name: str,
        pr_url: str,
        public_url: Optional[str],
    ) -> Dict[str, Any]:
        secret = signing_secret()
        if not secret:
            raise QAAgentError(
                "Set QA_PROPOSAL_SIGNING_SECRET (or JIRA_AUTOMATION_WEBHOOK_SECRET) "
                "so approval links can be signed."
            )
        proposal_id = str(uuid.uuid4())
        payload = {
            "id": proposal_id,
            "tool_name": tool_name,
            "title": evaluation.get("title"),
            "proposal": evaluation.get("proposal"),
            "summary": evaluation.get("summary"),
            "pr_url": pr_url,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }
        self._store_or_default().save(proposal_id, payload)
        token = sign_draft_id(proposal_id, secret=secret)
        base = public_base_url(public_url)
        link = review_url(proposal_id, base_url=base, token=token) if base else ""
        msg = format_cto_discord_message(payload, review_url=link)
        webhook = _webhook("DISCORD_WEBHOOK_URL_CTO")
        if webhook:
            post_to_discord(webhook, msg)
            proposal_text = (evaluation.get("proposal") or "").strip()
            if proposal_text:
                post_long_to_discord(webhook, proposal_text)
        return {
            "action": "discord_cto",
            "status": "improvement",
            "proposal_id": proposal_id,
            "review_url": link,
        }

    def _create_jira_issue(
        self,
        *,
        project_key: str,
        summary: str,
        description_markdown: str,
    ) -> Dict[str, Any]:
        try:
            client = JiraClient(JiraConfig.from_env())
            return client.create_issue(
                project_key=project_key,
                summary=summary,
                description_markdown=description_markdown,
            )
        except JiraError as e:
            logger.warning("Jira create_issue failed", exc_info=True)
            return {"ok": False, "error": str(e)}

    def _jira_body(self, proposal: str, *, tool_name: str, pr_url: str) -> str:
        lines = [
            f"{BIGAS_QA_MARKER} Automated QA agent proposal.",
            "",
            f"**Tool:** {tool_name}",
        ]
        if pr_url:
            lines.extend(["", f"**PR:** {pr_url}"])
        lines.extend(["", "## Proposal", proposal or "(no details)"])
        return "\n".join(lines)

    def _summarize_run(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        counts = {"excellent": 0, "improvement": 0, "new_feature": 0, "error": 0}
        for item in results:
            execution = item.get("execution") or {}
            if execution.get("is_error") or execution.get("error"):
                counts["error"] += 1
            status = ((item.get("evaluation") or {}).get("status") or "improvement")
            if status in counts:
                counts[status] += 1
        return {
            "total": len(results),
            "excellent": counts["excellent"],
            "improvement": counts["improvement"],
            "new_feature": counts["new_feature"],
            "errors": counts["error"],
            "all_excellent": len(results) > 0 and counts["improvement"] == 0 and counts["new_feature"] == 0 and counts["error"] == 0,
        }

    def _notify_qa_channel(self, summary: Dict[str, Any], *, pr_url: str) -> None:
        webhook = _webhook("DISCORD_WEBHOOK_URL_QA")
        if not webhook:
            return
        total = summary.get("total") or 0
        excellent = summary.get("excellent") or 0
        if total == 0:
            msg = "**QA run completed**\nNo MCP tools were selected for testing."
        elif summary.get("all_excellent"):
            msg = (
                f"**QA run passed** — {excellent}/{total} tool(s) rated excellent."
            )
        else:
            msg = (
                f"**QA run completed** — tested {total} tool(s): "
                f"{excellent} excellent, "
                f"{summary.get('improvement') or 0} need improvement, "
                f"{summary.get('new_feature') or 0} new feature(s), "
                f"{summary.get('errors') or 0} error(s)."
            )
        if pr_url:
            msg += f"\nPR: {pr_url}"
        post_to_discord(webhook, msg)

    def load_proposal(self, proposal_id: str) -> Dict[str, Any]:
        payload = self._store_or_default().load(proposal_id)
        if not payload:
            raise QAAgentError("Proposal not found")
        if is_expired(payload, ttl_hours=_ttl_hours()):
            self._store_or_default().delete(proposal_id)
            raise QAAgentError("Proposal expired")
        return payload

    def approve_proposal(self, proposal_id: str) -> Dict[str, Any]:
        payload = self.load_proposal(proposal_id)
        if (payload.get("status") or "") == "approved":
            return {"ok": True, "already_approved": True, **payload}
        title = payload.get("title") or f"QA improvement: {payload.get('tool_name')}"
        body = self._jira_body(
            str(payload.get("proposal") or ""),
            tool_name=str(payload.get("tool_name") or "?"),
            pr_url=str(payload.get("pr_url") or ""),
        )
        issue = self._create_jira_issue(
            project_key=_cto_project_key(),
            summary=str(title),
            description_markdown=body,
        )
        issue_key = issue.get("key") or ""
        issue_url = issue.get("url") or ""
        payload["status"] = "approved"
        payload["jira_issue_key"] = issue_key
        payload["jira_issue_url"] = issue_url
        self._store_or_default().delete(proposal_id)

        msg = (
            f"**QA improvement approved**\n"
            f"Tool: `{payload.get('tool_name')}`\n"
            f"Jira: {issue_key} {issue_url}".strip()
        )
        pr_url = (payload.get("pr_url") or "").strip()
        if pr_url:
            msg += f"\nPR: {pr_url}"
        webhook = _webhook("DISCORD_WEBHOOK_URL_CTO")
        if webhook:
            post_to_discord(webhook, msg)
        return {
            "ok": True,
            "approved": True,
            "issue_key": issue_key,
            "issue_url": issue_url,
            "tool_name": payload.get("tool_name"),
        }

    def decline_proposal(self, proposal_id: str) -> Dict[str, Any]:
        payload = self._store_or_default().load(proposal_id)
        if not payload:
            raise QAAgentError("Proposal not found")
        self._store_or_default().delete(proposal_id)
        msg = (
            f"**QA improvement declined**\n"
            f"Tool: `{payload.get('tool_name')}`\n"
            f"Title: {payload.get('title') or '(untitled)'}"
        )
        pr_url = (payload.get("pr_url") or "").strip()
        if pr_url:
            msg += f"\nPR: {pr_url}"
        webhook = _webhook("DISCORD_WEBHOOK_URL_CTO")
        if webhook:
            post_to_discord(webhook, msg)
        return {"ok": True, "declined": True, "proposal_id": proposal_id}


def format_cto_discord_message(payload: Dict[str, Any], *, review_url: str) -> str:
    lines = [
        "**QA agent: improvement suggested**",
        f"**Tool:** `{payload.get('tool_name') or '?'}`",
        f"**Title:** {payload.get('title') or '(untitled)'}",
    ]
    summary = (payload.get("summary") or "").strip()
    if summary:
        lines.extend(["", f"_{summary}_"])
    if review_url:
        lines.extend(
            [
                "",
                f"👉 [Review, then Approve or Decline]({review_url})",
                f"Proposal expires in {_ttl_hours()} hours.",
            ]
        )
    else:
        lines.append("")
        lines.append("_Approval links unavailable (SERVER_URL / BIGAS_PUBLIC_URL not set)._")
    return "\n".join(lines).strip()


def default_draft_store_for_tests() -> InMemoryQADraftStore:
    return InMemoryQADraftStore()
