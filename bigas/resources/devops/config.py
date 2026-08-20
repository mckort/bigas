"""Deployment target configuration for the DevOps specialist."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from bigas.portfolio import DEFAULT_SITE_TO_PROJECT, parse_csv_map, repo_map, resolve_project


RISKY_PATH_PATTERNS: tuple[str, ...] = (
    "migrations/",
    "migration/",
    "alembic/",
    "prisma/",
    "db/migrate",
    "schema.sql",
    "docker-compose",
    "deploy.sh",
    ".env.example",
    "requirements.txt",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Pipfile.lock",
    "poetry.lock",
)

# Example defaults when BIGAS_DEPLOY_WORKFLOW_MAP is unset (vcfieldassistant uses two workflows).
DEFAULT_WORKFLOW_MAP: Dict[str, List[str]] = {
    "VFA": ["deploy-backend.yml", "deploy-web.yml"],
}


@dataclass(frozen=True)
class DeployTarget:
    project_key: str
    repo: str
    workflows: List[str]
    site_urls: List[str]
    deploy_repo: str = ""
    workflow_inputs: Optional[Dict[str, str]] = None

    @property
    def dispatch_repo(self) -> str:
        return (self.deploy_repo or self.repo).strip()


def _workflow_map() -> Dict[str, List[str]]:
    raw = (os.environ.get("BIGAS_DEPLOY_WORKFLOW_MAP") or "").strip()
    if not raw:
        return dict(DEFAULT_WORKFLOW_MAP)
    out: Dict[str, List[str]] = {}
    for part in raw.split("|"):
        item = part.strip()
        if not item or ":" not in item:
            continue
        key, workflows = item.split(":", 1)
        key = key.strip().upper()
        names = [w.strip() for w in workflows.split(",") if w.strip()]
        if key and names:
            out[key] = names
    return out


def _deploy_repo_map() -> Dict[str, str]:
    parsed = parse_csv_map(os.environ.get("BIGAS_DEPLOY_REPO_MAP") or "")
    return {k.upper(): v.strip() for k, v in parsed.items() if (v or "").strip()}


def _site_urls_for_project(project_key: str) -> List[str]:
    key = (project_key or "").strip().upper()
    urls: List[str] = []
    for host, mapped in DEFAULT_SITE_TO_PROJECT.items():
        if mapped == key and not host.startswith("www."):
            urls.append(f"https://{host}")
    extra = (os.environ.get("MONITOR_URLS") or "").split(",")
    for url in extra:
        u = url.strip()
        if not u:
            continue
        host = u.lower().replace("https://", "").replace("http://", "").split("/")[0]
        if DEFAULT_SITE_TO_PROJECT.get(host) == key or DEFAULT_SITE_TO_PROJECT.get(f"www.{host}") == key:
            if u not in urls:
                urls.append(u if u.startswith("http") else f"https://{u}")
    return urls


def resolve_deploy_target(
    *,
    project_key: Optional[str] = None,
    repo: Optional[str] = None,
    site_or_text: Optional[str] = None,
) -> Optional[DeployTarget]:
    """Resolve a deployment target from project key, repo, or free text (site name)."""
    key = (project_key or "").strip().upper()
    if not key and site_or_text:
        key = resolve_project(site_or_text) or ""
    if not key and site_or_text:
        blob = site_or_text.lower()
        for host, mapped in DEFAULT_SITE_TO_PROJECT.items():
            if host.replace("www.", "") in blob or host in blob:
                key = mapped
                break

    repos = repo_map()
    resolved_repo = (repo or "").strip()
    if not resolved_repo and key:
        resolved_repo = repos.get(key) or ""
    if not key and resolved_repo:
        for k, r in repos.items():
            if r.lower() == resolved_repo.lower():
                key = k
                break

    if not key or not resolved_repo:
        return None

    workflows = _workflow_map().get(key) or []
    if not workflows:
        return None

    deploy_repo = _deploy_repo_map().get(key) or resolved_repo
    workflow_inputs: Optional[Dict[str, str]] = None
    if deploy_repo.lower() != resolved_repo.lower():
        site = resolved_repo.rsplit("/", 1)[-1].strip()
        if site:
            workflow_inputs = {"site": site}

    return DeployTarget(
        project_key=key,
        repo=resolved_repo,
        workflows=workflows,
        site_urls=_site_urls_for_project(key),
        deploy_repo=deploy_repo,
        workflow_inputs=workflow_inputs,
    )


def parse_repo(repo: str) -> tuple[str, str]:
    value = (repo or "").strip().strip("/")
    if "/" not in value:
        raise ValueError("repo must be in the form owner/repo")
    owner, name = value.split("/", 1)
    if not owner or not name:
        raise ValueError("repo must be in the form owner/repo")
    return owner, name
