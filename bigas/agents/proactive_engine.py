"""Proactive Goal Engine — scheduled Epic evaluation and backlog generation (BIG-12)."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from bigas.agents.proactive_prompts import (
    EXPERT_DELEGATION_PROMPT_TEMPLATE,
    IN_PROGRESS_EPIC_SYSTEM_PROMPT,
    PLAN_EPIC_SYSTEM_PROMPT,
    RESEARCH_EPIC_SYSTEM_PROMPT,
)
from bigas.chat.db import get_chat_store
from bigas.discord_webhook import post_long_to_discord
from bigas.llm.factory import get_llm_client
from bigas.portfolio import ga4_property_for_project, repo_map
from bigas.resources.product.create_jira_issue.service import (
    CreateJiraIssueError,
    CreateJiraIssueService,
)
from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    adf_to_plain_text,
)
from bigas.resources.product.jira_automation.config import JiraAutomationConfig
from bigas.resources.product.progress_updates.github_commits import (
    fetch_commits_for_projects,
    format_commits_for_prompt,
)

logger = logging.getLogger(__name__)

GOAL_PHASE_RESEARCH = "research"
GOAL_PHASE_PLAN = "plan"
GOAL_PHASE_IN_PROGRESS = "in_progress"

DEFAULT_GOAL_STATUSES = (
    "Research",
    "Plan",
    "In Progress",
    "Research and describe (AI)",
    "Design and plan (AI)",
    "In Progress (AI)",
)
DEFAULT_IN_PROGRESS_TASK_STATUSES = ("In Progress", "In Progress (AI)")
_EXPLICIT_STATUS_PHASES = {
    "research": GOAL_PHASE_RESEARCH,
    "research and describe (ai)": GOAL_PHASE_RESEARCH,
    "plan": GOAL_PHASE_PLAN,
    "design and plan (ai)": GOAL_PHASE_PLAN,
    "in progress": GOAL_PHASE_IN_PROGRESS,
    "in progress (ai)": GOAL_PHASE_IN_PROGRESS,
}
_EXPERT_AGENTS = (
    ("product", "Product management, Jira backlog, release cadence"),
    ("marketing", "GA4, ads, SEO, growth metrics"),
    ("cto", "Engineering quality, PRs, technical debt, monitoring"),
    ("devops", "Deployments, CI/CD, infrastructure"),
)


class ProactiveGoalEngineError(RuntimeError):
    pass


def goal_epic_statuses() -> Tuple[str, ...]:
    raw = (os.environ.get("BIGAS_GOAL_EPIC_STATUSES") or "").strip()
    if not raw:
        return DEFAULT_GOAL_STATUSES
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(parts) if parts else DEFAULT_GOAL_STATUSES


def goal_phase_for_status(status: str) -> Optional[str]:
    """Map a Jira status name to research / plan / in_progress."""
    key = (status or "").strip().lower()
    if not key:
        return None
    if key in _EXPLICIT_STATUS_PHASES:
        return _EXPLICIT_STATUS_PHASES[key]
    if "research" in key:
        return GOAL_PHASE_RESEARCH
    if "plan" in key or "design" in key:
        return GOAL_PHASE_PLAN
    if "in progress" in key:
        return GOAL_PHASE_IN_PROGRESS
    return None


def in_progress_task_statuses() -> Tuple[str, ...]:
    raw = (os.environ.get("BIGAS_GOAL_IN_PROGRESS_TASK_STATUSES") or "").strip()
    if not raw:
        return DEFAULT_IN_PROGRESS_TASK_STATUSES
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(parts) if parts else DEFAULT_IN_PROGRESS_TASK_STATUSES


def default_goal_timeframe_days() -> int:
    """Default lookback when Jira drag-triggered evaluation has no scheduler body."""
    raw = (os.environ.get("BIGAS_GOAL_DEFAULT_TIMEFRAME_DAYS") or "7").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 7
    return max(1, min(365, value))


def in_progress_status_clause() -> str:
    quoted = ", ".join(f'"{s}"' for s in in_progress_task_statuses())
    return f"AND status in ({quoted})"


def issue_is_epic(issue: Dict[str, Any]) -> bool:
    fields = issue.get("fields") or {}
    name = ((fields.get("issuetype") or {}).get("name") or "").strip()
    return name.lower() == "epic"


def chief_discord_webhook_url() -> Optional[str]:
    for key in (
        "DISCORD_WEBHOOK_URL_CHIEF",
        "DISCORD_WEBHOOK_URL_PRODUCT",
        "DISCORD_WEBHOOK_URL",
    ):
        url = (os.environ.get(key) or "").strip()
        if url and not url.lower().startswith("placeholder"):
            return url
    return None


def _project_key_from_issue(issue: Dict[str, Any]) -> str:
    key = (issue.get("key") or "").strip()
    if "-" in key:
        return key.split("-", 1)[0].upper()
    fields = issue.get("fields") or {}
    project = fields.get("project") or {}
    return (project.get("key") or "").strip().upper() or "UNKNOWN"


def _issue_status(issue: Dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    return ((fields.get("status") or {}).get("name") or "").strip()


def _issue_summary(issue: Dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    return (fields.get("summary") or "").strip()


def _issue_description(issue: Dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    desc = fields.get("description")
    if isinstance(desc, dict):
        return adf_to_plain_text(desc)
    return str(desc or "").strip()


def _normalize_issue_line(issue: Dict[str, Any]) -> Dict[str, str]:
    return {
        "key": (issue.get("key") or "").strip(),
        "summary": _issue_summary(issue),
        "status": _issue_status(issue),
    }


def _format_issue_list(issues: Sequence[Dict[str, Any]], *, empty: str) -> str:
    if not issues:
        return empty
    lines = []
    for issue in issues:
        row = _normalize_issue_line(issue)
        lines.append(f"- [{row['key']}] {row['status']}: {row['summary']}")
    return "\n".join(lines)


def _parse_llm_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    if "```" in raw:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if match:
            raw = match.group(1)
    else:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw = match.group(0)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("Failed to parse proactive engine LLM JSON")
        return {}


def _is_duplicate_task(summary: str, open_issues: Sequence[Dict[str, str]]) -> bool:
    candidate = re.sub(r"\s+", " ", (summary or "").strip().lower())
    if not candidate:
        return True
    for item in open_issues:
        existing = re.sub(r"\s+", " ", (item.get("summary") or "").strip().lower())
        if not existing:
            continue
        if candidate == existing or candidate in existing or existing in candidate:
            return True
    return False


def fetch_merged_pull_requests(
    *,
    project_key: str,
    days: int,
    token: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Return merged PRs for the project's GitHub repo within the last ``days``."""
    repo = (repo_map().get(project_key.upper()) or "").strip()
    if not repo or "/" not in repo:
        cfg = JiraAutomationConfig.from_env()
        repo = (cfg.project_repos.get(project_key.upper()) or "").strip()
    if not repo or "/" not in repo:
        return []

    gh_token = (token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not gh_token:
        return []

    owner, name = repo.split("/", 1)
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    url = f"https://api.github.com/repos/{owner}/{name}/pulls"
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    merged: List[Dict[str, str]] = []
    page = 1
    while page <= 5:
        try:
            resp = requests.get(
                url,
                headers=headers,
                params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": 30, "page": page},
                timeout=30,
            )
            if resp.status_code >= 400:
                logger.warning("GitHub PR fetch failed: %s", resp.status_code)
                break
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            stop = False
            for pr in batch:
                if not isinstance(pr, dict):
                    continue
                updated_at = (pr.get("updated_at") or "").strip()
                if updated_at:
                    try:
                        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        if updated_dt < since:
                            stop = True
                            break
                    except ValueError:
                        pass
                merged_at = (pr.get("merged_at") or "").strip()
                if not merged_at:
                    continue
                try:
                    merged_dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if merged_dt < since:
                    continue
                merged.append(
                    {
                        "number": str(pr.get("number") or ""),
                        "title": (pr.get("title") or "").strip(),
                        "url": (pr.get("html_url") or "").strip(),
                        "merged_at": merged_at[:10],
                    }
                )
            if stop:
                break
            page += 1
        except Exception:
            logger.exception("GitHub merged PR fetch error for %s", repo)
            break
    return merged


def fetch_marketing_snapshot(*, project_key: str, days: int) -> str:
    """Best-effort GA4 overview for the project; empty string when unavailable."""
    prop = ga4_property_for_project(project_key)
    if not prop:
        return "(No GA4 property mapped for this project.)"
    try:
        from bigas.resources.marketing.utils import get_date_range_strings

        start_date, end_date = get_date_range_strings(days)
        from bigas.providers.analytics.ga4 import GA4AnalyticsProvider

        if not GA4AnalyticsProvider.is_configured(property_id=prop):
            return "(GA4 not configured.)"
        overview = GA4AnalyticsProvider(property_id=prop).get_overview(start_date, end_date)
        return (
            f"GA4 property {prop} ({start_date} → {end_date}): "
            f"sessions={overview.get('sessions', 0)}, "
            f"users={overview.get('users', 0)}, "
            f"pageviews={overview.get('pageviews', 0)}"
        )
    except Exception as e:
        logger.warning("Marketing snapshot failed for %s: %s", project_key, e)
        return f"(Marketing metrics unavailable: {e})"


def _create_tasks_from_proposals(
    *,
    project_key: str,
    epic_key: str,
    proposals: Sequence[Dict[str, Any]],
    open_issues: Sequence[Dict[str, str]],
) -> List[Dict[str, Any]]:
    service = CreateJiraIssueService()
    created: List[Dict[str, Any]] = []
    for item in proposals:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "").strip()
        description = str(item.get("description") or "").strip()
        if not summary or not description:
            continue
        if _is_duplicate_task(summary, open_issues):
            logger.info("Skipping duplicate task proposal: %s", summary)
            continue
        issue_type = str(item.get("issue_type") or "Task").strip().title() or "Task"
        marketing = bool(item.get("marketing"))
        try:
            result = service.create(
                project_key=project_key,
                summary=summary,
                description=description,
                issue_type=issue_type,
                marketing=marketing,
                parent_epic_key=epic_key,
            )
            created.append({**result, "summary": summary})
            open_issues = list(open_issues) + [{"summary": summary, "key": result.get("key", ""), "status": "To Do"}]
        except CreateJiraIssueError as e:
            logger.warning("Failed to create Jira task %r: %s", summary, e)
    return created


class ProactiveGoalEngine:
    def __init__(self, *, jira_client: Optional[JiraClient] = None):
        if jira_client is None:
            from bigas.tickets.config import use_internal_board

            if use_internal_board():
                from bigas.tickets.jira_adapter import TicketJiraAdapter

                jira_client = TicketJiraAdapter()
            else:
                jira_client = JiraClient(JiraConfig.from_env())
        self._jira = jira_client
        self._llm, self._model = get_llm_client(feature="proactive_goals")

    def run(
        self,
        *,
        timeframe_days: int = 7,
        epic_key: Optional[str] = None,
        status_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        if timeframe_days < 1 or timeframe_days > 365:
            raise ProactiveGoalEngineError("timeframe_days must be between 1 and 365")

        key = (epic_key or "").strip()
        if key:
            try:
                epic = self._jira.get_issue(
                    key,
                    fields=["summary", "status", "description", "project", "issuetype"],
                )
            except JiraError as e:
                raise ProactiveGoalEngineError(str(e)) from e
            if not (epic.get("key") or "").strip():
                epic["key"] = key
            if not issue_is_epic(epic):
                raise ProactiveGoalEngineError(f"{key} is not an Epic")
            epics = [epic]
        else:
            statuses = goal_epic_statuses()
            try:
                epics = self._jira.get_epics_by_statuses(statuses=statuses)
            except JiraError as e:
                raise ProactiveGoalEngineError(str(e)) from e

        results: List[Dict[str, Any]] = []
        for epic in epics:
            status = _issue_status(epic)
            if key:
                status = (status_hint or "").strip() or status
            row_key = (epic.get("key") or "").strip()
            if not row_key:
                continue
            try:
                results.append(
                    self._evaluate_one(epic, timeframe_days=timeframe_days, status=status)
                )
            except Exception as e:
                logger.exception("Epic evaluation failed for %s", row_key)
                results.append({"epic_key": row_key, "status": status, "ok": False, "error": str(e)})

        return {
            "ok": True,
            "timeframe_days": timeframe_days,
            "epics_found": len(epics),
            "results": results,
        }

    def _evaluate_one(
        self,
        epic: Dict[str, Any],
        *,
        timeframe_days: int,
        status: str,
    ) -> Dict[str, Any]:
        phase = goal_phase_for_status(status)
        if phase == GOAL_PHASE_RESEARCH:
            return self._evaluate_research_epic(epic)
        if phase == GOAL_PHASE_PLAN:
            return self._evaluate_plan_epic(epic)
        if phase == GOAL_PHASE_IN_PROGRESS:
            return self._evaluate_in_progress_epic(epic, timeframe_days=timeframe_days)
        return {
            "epic_key": (epic.get("key") or "").strip(),
            "status": status,
            "skipped": True,
            "reason": f"Unhandled goal status {status!r}",
        }

    def _evaluate_research_epic(self, epic: Dict[str, Any]) -> Dict[str, Any]:
        epic_key = epic.get("key", "")
        project_key = _project_key_from_issue(epic)
        open_raw = self._jira.get_issues_for_epic(
            epic_key,
            status_clause="AND status != Done",
        )
        open_issues = [_normalize_issue_line(i) for i in open_raw]
        user_prompt = self._build_planning_user_prompt(epic, open_issues)
        parsed = self._llm_json_completion(RESEARCH_EPIC_SYSTEM_PROMPT, user_prompt)
        created = _create_tasks_from_proposals(
            project_key=project_key,
            epic_key=epic_key,
            proposals=parsed.get("tasks_to_create") or [],
            open_issues=open_issues,
        )
        analysis = (parsed.get("analysis") or "").strip()
        self._post_planning_update(
            epic_key=epic_key,
            phase=GOAL_PHASE_RESEARCH,
            summary=analysis,
            created=created,
        )
        return {
            "epic_key": epic_key,
            "status": _issue_status(epic) or "Research",
            "phase": GOAL_PHASE_RESEARCH,
            "ok": True,
            "analysis": analysis,
            "tasks_created": created,
        }

    def _evaluate_plan_epic(self, epic: Dict[str, Any]) -> Dict[str, Any]:
        epic_key = epic.get("key", "")
        project_key = _project_key_from_issue(epic)
        open_raw = self._jira.get_issues_for_epic(
            epic_key,
            status_clause="AND status != Done",
        )
        open_issues = [_normalize_issue_line(i) for i in open_raw]
        user_prompt = self._build_planning_user_prompt(epic, open_issues)
        parsed = self._llm_json_completion(PLAN_EPIC_SYSTEM_PROMPT, user_prompt)
        created = _create_tasks_from_proposals(
            project_key=project_key,
            epic_key=epic_key,
            proposals=parsed.get("tasks_to_create") or [],
            open_issues=open_issues,
        )
        plan_summary = (parsed.get("plan_summary") or "").strip()
        self._post_planning_update(
            epic_key=epic_key,
            phase=GOAL_PHASE_PLAN,
            summary=plan_summary,
            created=created,
        )
        return {
            "epic_key": epic_key,
            "status": _issue_status(epic) or "Plan",
            "phase": GOAL_PHASE_PLAN,
            "ok": True,
            "plan_summary": plan_summary,
            "tasks_created": created,
        }

    def _evaluate_in_progress_epic(self, epic: Dict[str, Any], *, timeframe_days: int) -> Dict[str, Any]:
        epic_key = epic.get("key", "")
        epic_summary = _issue_summary(epic)
        project_key = _project_key_from_issue(epic)

        completed = self._jira.get_issues_for_epic(
            epic_key,
            status_clause="AND status = Done",
            updated_since_days=timeframe_days,
        )
        in_progress = self._jira.get_issues_for_epic(
            epic_key,
            status_clause=in_progress_status_clause(),
        )
        open_backlog = self._jira.get_issues_for_epic(
            epic_key,
            status_clause="AND status != Done",
        )
        open_issues = [_normalize_issue_line(i) for i in open_backlog]

        git_payload = fetch_commits_for_projects(
            project_keys=[project_key],
            days=timeframe_days,
        )
        git_text = format_commits_for_prompt(
            git_payload.get("by_project") or {},
            stats=git_payload.get("stats") or {},
        )
        merged_prs = fetch_merged_pull_requests(project_key=project_key, days=timeframe_days)
        pr_lines = (
            "\n".join(f"- [#{p['number']}] {p['title']} ({p['merged_at']}) {p['url']}" for p in merged_prs)
            or "(No merged PRs in this period.)"
        )
        marketing_text = fetch_marketing_snapshot(project_key=project_key, days=timeframe_days)

        context_block = "\n\n".join(
            [
                f"## Completed Jira work (last {timeframe_days} days)\n"
                + _format_issue_list(completed, empty="(None)"),
                f"## Currently In Progress tasks\n"
                + _format_issue_list(in_progress, empty="(None — move tasks to In Progress when ready.)"),
                f"## Open backlog (do not duplicate)\n"
                + _format_issue_list(open_backlog, empty="(None)"),
                f"## Git commits\n{git_text}",
                f"## Merged pull requests\n{pr_lines}",
                f"## Marketing / analytics\n{marketing_text}",
            ]
        )

        expert_notes: Dict[str, str] = {}
        for agent_id, domain in _EXPERT_AGENTS:
            prompt = EXPERT_DELEGATION_PROMPT_TEMPLATE.format(
                epic_key=epic_key,
                epic_summary=epic_summary,
                timeframe_days=timeframe_days,
                domain=domain,
                context=context_block,
            )
            expert_notes[agent_id] = self._delegate_to_expert(agent_id, prompt)

        user_prompt = self._build_in_progress_user_prompt(
            epic=epic,
            timeframe_days=timeframe_days,
            context_block=context_block,
            expert_notes=expert_notes,
            open_issues=open_issues,
        )
        parsed = self._llm_json_completion(IN_PROGRESS_EPIC_SYSTEM_PROMPT, user_prompt)
        progress_report = (parsed.get("progress_report") or "").strip()
        if progress_report:
            self._post_progress_report(progress_report, epic_key=epic_key)

        created = _create_tasks_from_proposals(
            project_key=project_key,
            epic_key=epic_key,
            proposals=parsed.get("tasks_to_create") or [],
            open_issues=open_issues,
        )

        return {
            "epic_key": epic_key,
            "status": _issue_status(epic) or "In Progress",
            "phase": GOAL_PHASE_IN_PROGRESS,
            "ok": True,
            "progress_report_posted": bool(progress_report),
            "expert_notes": expert_notes,
            "tasks_created": created,
        }

    def _build_planning_user_prompt(
        self,
        epic: Dict[str, Any],
        open_issues: Sequence[Dict[str, str]],
    ) -> str:
        epic_key = epic.get("key", "")
        return "\n\n".join(
            [
                f"Epic: {epic_key}",
                f"Summary: {_issue_summary(epic)}",
                f"Description:\n{_issue_description(epic) or '(empty)'}",
                "open_issues:\n" + _format_issue_list(
                    [{"key": i["key"], "fields": {"summary": i["summary"], "status": {"name": i["status"]}}} for i in open_issues],
                    empty="(none)",
                ),
            ]
        )

    def _build_in_progress_user_prompt(
        self,
        *,
        epic: Dict[str, Any],
        timeframe_days: int,
        context_block: str,
        expert_notes: Dict[str, str],
        open_issues: Sequence[Dict[str, str]],
    ) -> str:
        experts = "\n\n".join(
            f"### {agent_id.title()} agent\n{note}" for agent_id, note in expert_notes.items() if note
        )
        return "\n\n".join(
            [
                f"Epic: {epic.get('key')} — {_issue_summary(epic)}",
                f"Lookback window: last {timeframe_days} days. Plan tasks for the **next** {timeframe_days} days.",
                context_block,
                f"Expert suggestions:\n{experts or '(none)'}",
                "open_issues (never duplicate):\n"
                + "\n".join(f"- {i['key']}: {i['summary']}" for i in open_issues)
                or "(none)",
            ]
        )

    def _llm_json_completion(self, system: str, user: str) -> Dict[str, Any]:
        try:
            text = self._llm.complete(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=4000,
                temperature=0.4,
            )
        except Exception as e:
            raise ProactiveGoalEngineError(f"LLM request failed: {e}") from e
        return _parse_llm_json(text)

    def _delegate_to_expert(self, agent_id: str, task: str) -> str:
        from bigas.agents.chief_of_staff import run_specialist_task

        try:
            store = get_chat_store()
            agent = store.get_agent(agent_id) or {}
            if not (agent.get("system_prompt_goals") or "").strip():
                return ""
            return (run_specialist_task(agent_id, task, async_mode=False) or "").strip()
        except Exception as e:
            logger.warning("Expert delegation to %s failed: %s", agent_id, e)
            return f"(Delegation failed: {e})"

    def _post_to_chief(self, message: str, *, epic_key: str) -> None:
        webhook = chief_discord_webhook_url()
        post_long_to_discord(
            webhook,
            message,
            chat_agent_id="chief",
            chat_metadata={"source": "proactive_goal_engine", "epic_key": epic_key},
        )

    def _post_progress_report(self, report: str, *, epic_key: str) -> None:
        self._post_to_chief(
            f"🎯 **Goal progress — {epic_key}**\n\n{report}",
            epic_key=epic_key,
        )

    def _post_planning_update(
        self,
        *,
        epic_key: str,
        phase: str,
        summary: str,
        created: Sequence[Dict[str, Any]],
    ) -> None:
        lines = [f"🎯 **Goal {phase} — {epic_key}**", ""]
        if summary:
            lines.extend([summary.strip(), ""])
        if created:
            lines.append("Created tasks:")
            for item in created:
                key = (item.get("key") or "").strip()
                url = (item.get("url") or "").strip()
                title = (item.get("summary") or key).strip()
                if url and key:
                    lines.append(f"- [{key}]({url}): {title}")
                elif key:
                    lines.append(f"- {key}: {title}")
        else:
            lines.append("No new tasks (open backlog already covers this).")
        self._post_to_chief("\n".join(lines).strip(), epic_key=epic_key)


def run_evaluation_loop(
    *,
    timeframe_days: int = 7,
    epic_key: Optional[str] = None,
    status_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Entry point for the scheduled evaluate-goals webhook (or a single Epic)."""
    engine = ProactiveGoalEngine()
    return engine.run(
        timeframe_days=timeframe_days,
        epic_key=epic_key,
        status_hint=status_hint,
    )
