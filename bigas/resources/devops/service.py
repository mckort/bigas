"""DevOps deployment services: risk assessment, workflow triggers, health checks."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from bigas.providers.monitoring.service import _check_http_status
from bigas.resources.devops.config import RISKY_PATH_PATTERNS, DeployTarget, parse_repo, resolve_deploy_target
from bigas.resources.devops.github_actions import GitHubActionsClient, GitHubActionsError

logger = logging.getLogger(__name__)


class DevOpsError(RuntimeError):
    pass


def _github_client(token: Optional[str] = None) -> GitHubActionsClient:
    key = (token or "").strip() or (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not key:
        raise DevOpsError("GITHUB_TOKEN is required for DevOps deployment tools")
    return GitHubActionsClient(key)


def _classify_file(path: str) -> Optional[str]:
    lower = (path or "").lower()
    for pattern in RISKY_PATH_PATTERNS:
        if pattern.lower() in lower:
            if "migration" in pattern or "alembic" in pattern or "prisma" in pattern or "db/migrate" in pattern:
                return "database_migration"
            if pattern.endswith(".lock") or pattern in ("requirements.txt",):
                return "dependency_change"
            if "deploy" in pattern or "docker-compose" in pattern or ".env" in pattern:
                return "infrastructure_config"
    return None


def check_deployment_risk(
    *,
    repo: Optional[str] = None,
    project_key: Optional[str] = None,
    base_ref: Optional[str] = None,
    head_ref: Optional[str] = None,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    target = resolve_deploy_target(project_key=project_key, repo=repo)
    if not target and repo:
        owner, name = parse_repo(repo)
        target = DeployTarget(
            project_key=project_key or "",
            repo=f"{owner}/{name}",
            workflows=[],
            site_urls=[],
        )
    if not target:
        raise DevOpsError(
            "Could not resolve deployment target. Provide repo (owner/repo) or project_key, "
            "and configure BIGAS_DEPLOY_WORKFLOW_MAP."
        )

    owner, name = parse_repo(target.repo)
    client = _github_client(github_token)
    default_branch = client.get_default_branch(owner, name)
    head = (head_ref or default_branch).strip()
    base = (base_ref or "").strip()
    if not base:
        base = client.get_latest_release_tag(owner, name) or default_branch

    compare = client.compare_refs(owner, name, base, head)
    files = compare.get("files") or []
    if not isinstance(files, list):
        files = []

    findings: Dict[str, List[str]] = {
        "database_migration": [],
        "dependency_change": [],
        "infrastructure_config": [],
        "other_risky": [],
    }
    for item in files:
        if not isinstance(item, dict):
            continue
        filename = (item.get("filename") or "").strip()
        if not filename:
            continue
        category = _classify_file(filename)
        if category:
            if category not in findings:
                findings[category] = []
            if filename not in findings[category]:
                findings[category].append(filename)
        elif any(p in filename.lower() for p in ("secret", "credential", "prod", "production")):
            if filename not in findings["other_risky"]:
                findings["other_risky"].append(filename)

    migration_count = len(findings["database_migration"])
    warnings: List[str] = []
    if migration_count:
        warnings.append(f"{migration_count} database migration file(s) changed")
    if findings["dependency_change"]:
        warnings.append(f"{len(findings['dependency_change'])} dependency/lockfile change(s)")
    if findings["infrastructure_config"]:
        warnings.append(f"{len(findings['infrastructure_config'])} deploy/infrastructure config change(s)")
    if findings["other_risky"]:
        warnings.append(f"{len(findings['other_risky'])} other potentially risky file(s)")

    risk_level = "low"
    if migration_count or findings["infrastructure_config"]:
        risk_level = "high"
    elif findings["dependency_change"] or findings["other_risky"]:
        risk_level = "medium"

    summary_parts = [
        f"Compared {base} → {head} on {target.repo}.",
        f"{len(files)} file(s) changed.",
    ]
    if warnings:
        summary_parts.append("Warnings: " + "; ".join(warnings) + ".")
    else:
        summary_parts.append("No migration or critical config changes detected.")
    if risk_level != "low":
        summary_parts.append("Confirm with the user before deploying.")

    return {
        "status": "ok",
        "summary": " ".join(summary_parts),
        "repo": target.repo,
        "project_key": target.project_key or None,
        "base_ref": base,
        "head_ref": head,
        "total_files_changed": len(files),
        "risk_level": risk_level,
        "warnings": warnings,
        "findings": findings,
        "workflows_configured": target.workflows,
        "site_urls": target.site_urls,
    }


def trigger_deployment(
    *,
    repo: Optional[str] = None,
    project_key: Optional[str] = None,
    workflows: Optional[List[str]] = None,
    ref: Optional[str] = None,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    target = resolve_deploy_target(project_key=project_key, repo=repo)
    if not target:
        raise DevOpsError(
            "Could not resolve deployment target. Set BIGAS_JIRA_PROJECT_REPO_MAP and "
            "BIGAS_DEPLOY_WORKFLOW_MAP (e.g. VFA:deploy-backend.yml,deploy-web.yml)."
        )

    owner, name = parse_repo(target.repo)
    client = _github_client(github_token)
    branch = (ref or client.get_default_branch(owner, name)).strip()
    workflow_names = [w.strip() for w in (workflows or target.workflows) if w and w.strip()]
    if not workflow_names:
        raise DevOpsError("No deployment workflows configured for this target.")

    triggered: List[Dict[str, Any]] = []
    errors: List[str] = []
    for wf in workflow_names:
        try:
            client.trigger_workflow(owner, name, wf, branch)
            time.sleep(1.5)
            runs = client.list_workflow_runs(owner, name, wf, branch=branch, limit=1)
            run = runs[0] if runs else {}
            triggered.append(
                {
                    "workflow": wf,
                    "ref": branch,
                    "run_id": run.get("id"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "html_url": run.get("html_url"),
                }
            )
        except GitHubActionsError as e:
            errors.append(f"{wf}: {e}")

    if not triggered and errors:
        raise DevOpsError("; ".join(errors))

    lines = [
        f"Triggered {len(triggered)} workflow(s) on {target.repo} @ {branch}.",
    ]
    for item in triggered:
        url = item.get("html_url") or "(pending — check Actions tab)"
        lines.append(f"- {item['workflow']}: run #{item.get('run_id') or '?'} — {url}")
    if errors:
        lines.append("Errors: " + "; ".join(errors))
    lines.append(
        "GitHub Actions may take several minutes. Ask for a status update with the run ID, "
        "or check the Actions tab in GitHub."
    )

    return {
        "status": "ok" if triggered else "error",
        "summary": "\n".join(lines),
        "repo": target.repo,
        "project_key": target.project_key,
        "ref": branch,
        "triggered": triggered,
        "errors": errors,
        "site_urls": target.site_urls,
    }


def get_deployment_status(
    *,
    repo: str,
    run_id: int,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    owner, name = parse_repo(repo)
    client = _github_client(github_token)
    run = client.get_workflow_run(owner, name, int(run_id))
    status = (run.get("status") or "unknown").lower()
    conclusion = (run.get("conclusion") or "").lower()
    wf_name = run.get("name") or run.get("path") or "workflow"
    html_url = run.get("html_url") or ""

    if status == "completed":
        if conclusion == "success":
            msg = f"Run #{run_id} ({wf_name}) completed successfully."
        else:
            msg = f"Run #{run_id} ({wf_name}) completed with conclusion: {conclusion or 'unknown'}."
    else:
        msg = f"Run #{run_id} ({wf_name}) is still {status}."

    return {
        "status": "ok",
        "summary": msg,
        "run_id": run_id,
        "workflow_status": status,
        "conclusion": conclusion or None,
        "html_url": html_url,
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
    }


def check_website_health(url: str) -> Dict[str, Any]:
    target = (url or "").strip()
    if not target:
        raise DevOpsError("url is required")
    if not target.startswith("http://") and not target.startswith("https://"):
        target = f"https://{target}"

    started = time.perf_counter()
    status_code, error, connection_failure = _check_http_status(target)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    healthy = status_code is not None and status_code < 400 and not error
    if healthy:
        summary = f"{target} returned HTTP {status_code} in {elapsed_ms}ms."
    elif connection_failure:
        summary = f"{target} is unreachable ({error})."
    else:
        summary = f"{target} check failed: {error or 'unknown error'}."

    return {
        "status": "ok",
        "summary": summary,
        "url": target,
        "http_status": status_code,
        "response_time_ms": elapsed_ms,
        "is_healthy": healthy,
        "error": error,
    }
