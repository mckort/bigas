"""Config for Jira AI column automation (env-driven, multi-project ready)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional


# Handler names used by the router
HANDLER_RESEARCH = "research_describe"
HANDLER_DESIGN = "design_plan"
HANDLER_IMPLEMENT = "implement"

BIGAS_COMMENT_MARKER = "[bigas-jira-ai]"

DEFAULT_PROJECT_REPOS: Dict[str, str] = {
    "VFA": "mckort/vcfieldassistant",
    "WAYW": "mckort/roadpal",
    "BIG": "mckort/bigas",
    "REM": "mckort/remotebrief",
    "GPWW": "Green-Promo-Wear-Global/greenpromowear-website",
    "FYDA": "mckort/fulfillourdreamadventure",
    "MYL": "mckort/mylifesdeed",
}

DEFAULT_STATUS_HANDLERS: Dict[str, str] = {
    "research and describe (ai)": HANDLER_RESEARCH,
    "design and plan (ai)": HANDLER_DESIGN,
    "in progress (ai)": HANDLER_IMPLEMENT,
}


def _parse_csv_upper(raw: Optional[str]) -> tuple[str, ...]:
    if not raw:
        return ()
    parts = []
    seen = set()
    for part in raw.replace(";", ",").split(","):
        key = part.strip().upper()
        if key and key not in seen:
            seen.add(key)
            parts.append(key)
    return tuple(parts)


def _parse_project_repo_map(raw: Optional[str]) -> Dict[str, str]:
    """Parse `VFA:mckort/vcfieldassistant,WAYW:mckort/roadpal`."""
    out = dict(DEFAULT_PROJECT_REPOS)
    if not (raw or "").strip():
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, repo = part.split(":", 1)
        k = key.strip().upper()
        r = repo.strip()
        if k and r:
            out[k] = r
    return out


def _parse_status_handlers(raw: Optional[str]) -> Dict[str, str]:
    """
    Optional override: `Research and describe (AI)=research_describe;Design and plan (AI)=design_plan`
    Keys are lowercased for matching.
    """
    out = dict(DEFAULT_STATUS_HANDLERS)
    if not (raw or "").strip():
        return out
    for part in raw.replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        status, handler = part.split("=", 1)
        s = status.strip().lower()
        h = handler.strip()
        if s and h:
            out[s] = h
    return out


@dataclass(frozen=True)
class JiraAutomationConfig:
    webhook_secret: str
    allowed_projects: tuple[str, ...]
    project_repos: Dict[str, str]
    status_handlers: Dict[str, str]
    status_description_approval: str
    status_design_approval: str
    status_final_approval: str
    daily_quota: int
    default_base_branch: str
    discord_pm_env: str
    discord_cto_env: str

    @staticmethod
    def from_env() -> "JiraAutomationConfig":
        secret = (os.environ.get("JIRA_AUTOMATION_WEBHOOK_SECRET") or "").strip()
        allowed = _parse_csv_upper(
            os.environ.get("BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS")
            or "VFA,WAYW,BIG,REM,GPWW,FYDA,MYL"
        )
        daily_raw = (os.environ.get("BIGAS_JIRA_AI_DAILY_QUOTA") or "20").strip()
        try:
            daily_quota = max(1, int(daily_raw))
        except ValueError:
            daily_quota = 20

        return JiraAutomationConfig(
            webhook_secret=secret,
            allowed_projects=allowed or ("VFA",),
            project_repos=_parse_project_repo_map(
                os.environ.get("BIGAS_JIRA_PROJECT_REPO_MAP")
            ),
            status_handlers=_parse_status_handlers(
                os.environ.get("BIGAS_JIRA_STATUS_HANDLERS")
            ),
            status_description_approval=(
                os.environ.get("BIGAS_JIRA_STATUS_DESCRIPTION_APPROVAL")
                or "Description approval (manual)"
            ).strip(),
            status_design_approval=(
                os.environ.get("BIGAS_JIRA_STATUS_DESIGN_APPROVAL")
                or "Design approval (manual)"
            ).strip(),
            status_final_approval=(
                os.environ.get("BIGAS_JIRA_STATUS_FINAL_APPROVAL")
                or "Final approval (manual)"
            ).strip(),
            daily_quota=daily_quota,
            default_base_branch=(
                os.environ.get("BIGAS_JIRA_DEFAULT_BASE_BRANCH") or "main"
            ).strip()
            or "main",
            discord_pm_env="DISCORD_WEBHOOK_URL_PRODUCT",
            discord_cto_env="DISCORD_WEBHOOK_URL_CTO",
        )

    def handler_for_status(self, status_name: str) -> Optional[str]:
        return self.status_handlers.get((status_name or "").strip().lower())

    def repo_for_project(self, project_key: str) -> Optional[str]:
        return self.project_repos.get((project_key or "").strip().upper())

    def is_project_allowed(self, project_key: str) -> bool:
        return (project_key or "").strip().upper() in self.allowed_projects
