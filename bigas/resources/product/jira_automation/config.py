"""Config for Jira AI column automation (env-driven, multi-project ready)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from bigas.resources.product.release_workflow import (
    project_branch_mapping_from_env,
    resolve_automerge_branch,
)


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
    "FYDA": "mckort/fulfillyourdreamadventure",
    "MYL": "mckort/mylifesdeed",
}

# owner/repo → default branch for Cursor implement startingRef
DEFAULT_REPO_BASE_BRANCHES: Dict[str, str] = {
    "mckort/vcfieldassistant": "main",
    "mckort/roadpal": "main",
    "mckort/bigas": "main",
    "mckort/remotebrief": "main",
    "Green-Promo-Wear-Global/greenpromowear-website": "main",
    "mckort/fulfillyourdreamadventure": "master",
    "mckort/mylifesdeed": "main",
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


def _parse_repo_base_branch_map(raw: Optional[str]) -> Dict[str, str]:
    """Parse `mckort/fulfillyourdreamadventure:master,mckort/vcfieldassistant:main`."""
    out = dict(DEFAULT_REPO_BASE_BRANCHES)
    if not (raw or "").strip():
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        # owner/repo:branch — split on the last colon so org/repo stays intact
        repo, branch = part.rsplit(":", 1)
        r = repo.strip()
        b = branch.strip()
        if r and b and "/" in r:
            out[r] = b
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
    repo_base_branches: Dict[str, str]
    project_branch_map: Dict[str, str]
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
            repo_base_branches=_parse_repo_base_branch_map(
                os.environ.get("BIGAS_JIRA_REPO_BASE_BRANCH_MAP")
            ),
            project_branch_map=project_branch_mapping_from_env(),
            discord_pm_env="DISCORD_WEBHOOK_URL_PRODUCT",
            discord_cto_env="DISCORD_WEBHOOK_URL_CTO",
        )

    def handler_for_status(self, status_name: str) -> Optional[str]:
        return self.status_handlers.get((status_name or "").strip().lower())

    def repo_for_project(self, project_key: str) -> Optional[str]:
        return self.project_repos.get((project_key or "").strip().upper())

    def base_branch_for_repo(self, repo: str) -> str:
        """Return Cursor startingRef for owner/repo, else default_base_branch."""
        r = (repo or "").strip()
        if r and r in self.repo_base_branches:
            return self.repo_base_branches[r]
        return self.default_base_branch

    def automerge_branch_for_project(
        self,
        project_key: str,
        repo: str,
        *,
        labels: Optional[Iterable[str]] = None,
    ) -> str:
        """PR / implement base branch from project mapping (staging vs main)."""
        return resolve_automerge_branch(
            project_key=project_key,
            repo=repo,
            labels=labels,
            project_branch_map=self.project_branch_map,
            repo_base_branches=self.repo_base_branches,
            default_base_branch=self.default_base_branch,
        )

    def is_project_allowed(self, project_key: str) -> bool:
        return (project_key or "").strip().upper() in self.allowed_projects
