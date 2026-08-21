"""DevOps deployment services: risk assessment, workflow triggers, health checks."""
from __future__ import annotations

import logging
import os
import tempfile
import time
import zipfile
from typing import Any, Dict, List, Optional

from bigas.providers.monitoring.service import _check_http_status
from bigas.resources.devops.config import RISKY_PATH_PATTERNS, DeployTarget, parse_repo, resolve_deploy_target
from bigas.resources.devops.github_actions import (
    DEFAULT_LOG_TAIL_LINES,
    DEFAULT_LOG_ZIP_MAX_BYTES,
    HOTFIX_BRANCH_PREFIX,
    GitHubActionsClient,
    GitHubActionsError,
    extract_job_logs_from_zip,
    excerpt_gha_logs,
    find_failed_jobs,
    format_commit_diff,
    truncate_log_text,
)

logger = logging.getLogger(__name__)

_RUN_POLL_INTERVAL_SEC = 1.0
_RUN_POLL_TIMEOUT_SEC = 30.0


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
            if (
                "migration" in pattern
                or "alembic" in pattern
                or "prisma" in pattern
                or "db/migrate" in pattern
                or "schema" in pattern
            ):
                return "database_migration"
            if "lock" in pattern or pattern in ("requirements.txt",):
                return "dependency_change"
            if "deploy" in pattern or "docker-compose" in pattern or ".env" in pattern:
                return "infrastructure_config"
    return None


def _wait_for_new_workflow_run(
    client: GitHubActionsClient,
    owner: str,
    name: str,
    workflow_id: str,
    branch: str,
    previous_run_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Poll until a workflow run newer than previous_run_id appears, or timeout."""
    deadline = time.monotonic() + _RUN_POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        time.sleep(_RUN_POLL_INTERVAL_SEC)
        try:
            runs = client.list_workflow_runs(owner, name, workflow_id, branch=branch, limit=1)
        except GitHubActionsError as e:
            logger.warning(
                "Could not list workflow runs for %s after trigger: %s",
                workflow_id,
                e,
            )
            return None
        run = runs[0] if runs else {}
        run_id = run.get("id")
        if run_id and run_id != previous_run_id:
            return run
    logger.warning(
        "Timed out waiting for new workflow run for %s (previous run_id=%s)",
        workflow_id,
        previous_run_id,
    )
    return None


def _collect_compare_files(compare: Dict[str, Any]) -> List[Dict[str, Any]]:
    files = compare.get("files") or []
    return files if isinstance(files, list) else []


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
    explicit_base = (base_ref or "").strip()

    backend_rel = client.latest_release_with_prefix(owner, name, "deploy-backend-")
    web_rel = client.latest_release_with_prefix(owner, name, "deploy-web-")
    prod_backend_tag = (backend_rel or {}).get("tag_name") if backend_rel else None
    prod_web_tag = (web_rel or {}).get("tag_name") if web_rel else None

    bases: List[tuple[str, str]] = []
    if explicit_base:
        bases.append(("explicit", explicit_base))
    else:
        if prod_backend_tag:
            bases.append(("backend", str(prod_backend_tag)))
        if prod_web_tag:
            bases.append(("web", str(prod_web_tag)))
        if not bases:
            fallback = client.get_latest_release_tag(owner, name)
            if fallback and fallback != head:
                bases.append(("release", fallback))

    files_by_name: Dict[str, Dict[str, Any]] = {}
    compared: List[str] = []
    compare_errors: List[str] = []
    for _label, tag in bases:
        if tag == head:
            continue
        try:
            compare = client.compare_refs(owner, name, tag, head)
        except GitHubActionsError as e:
            compare_errors.append(f"{tag} → {head}: {e}")
            continue
        compared.append(f"{tag} → {head}")
        for item in _collect_compare_files(compare):
            if not isinstance(item, dict):
                continue
            filename = (item.get("filename") or "").strip()
            if filename:
                files_by_name[filename] = item

    files = list(files_by_name.values())
    if compared:
        base_display = " + ".join(compared)
    elif bases and all(tag == head for _label, tag in bases):
        base_display = f"{head} → {head}"
    else:
        base_display = f"(no production version) → {head}"

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

    if compare_errors:
        warnings.extend(compare_errors)

    risk_level = "low"
    if migration_count or findings["infrastructure_config"]:
        risk_level = "high"
    elif findings["dependency_change"] or findings["other_risky"]:
        risk_level = "medium"

    no_prod_version = not bases
    version_bits = []
    if prod_backend_tag:
        version_bits.append(f"prod backend {prod_backend_tag}")
    if prod_web_tag:
        version_bits.append(f"prod web {prod_web_tag}")

    summary_parts: List[str] = []
    if version_bits:
        summary_parts.append("Currently deployed: " + "; ".join(version_bits) + ".")
    if no_prod_version:
        summary_parts.append(
            f"No production deploy version recorded yet for {target.repo}. "
            f"Cannot compare live prod against {head}."
        )
        summary_parts.append("This would be the first versioned deploy.")
    else:
        summary_parts.append(f"Compared {base_display} on {target.repo}.")
        summary_parts.append(f"{len(files)} file(s) changed.")
    if warnings:
        summary_parts.append("Warnings: " + "; ".join(warnings) + ".")
    elif not no_prod_version:
        summary_parts.append("No migration or critical config changes detected.")
    if risk_level != "low":
        summary_parts.append("Confirm with the user before deploying.")

    base_ref_out = explicit_base or prod_backend_tag or prod_web_tag or (bases[0][1] if bases else None)

    return {
        "status": "ok",
        "summary": " ".join(summary_parts),
        "repo": target.repo,
        "project_key": target.project_key or None,
        "base_ref": base_ref_out,
        "head_ref": head,
        "prod_backend_tag": prod_backend_tag,
        "prod_web_tag": prod_web_tag,
        "total_files_changed": len(files),
        "risk_level": risk_level,
        "warnings": warnings,
        "findings": findings,
        "no_prod_version": no_prod_version,
        "workflows_configured": target.workflows,
        "site_urls": target.site_urls,
        "deploy_repo": target.dispatch_repo,
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

    product_owner, product_name = parse_repo(target.repo)
    dispatch_owner, dispatch_name = parse_repo(target.dispatch_repo)
    client = _github_client(github_token)
    product_ref = (ref or client.get_default_branch(product_owner, product_name)).strip()
    cross_repo = target.dispatch_repo.lower() != target.repo.lower()
    if cross_repo:
        dispatch_branch = client.get_default_branch(dispatch_owner, dispatch_name).strip()
    else:
        dispatch_branch = product_ref
    workflow_names = [w.strip() for w in (workflows or target.workflows) if w and w.strip()]
    if not workflow_names:
        raise DevOpsError("No deployment workflows configured for this target.")

    inputs = dict(target.workflow_inputs or {})
    if cross_repo:
        inputs["ref"] = product_ref
    triggered: List[Dict[str, Any]] = []
    errors: List[str] = []
    for wf in workflow_names:
        previous_run_id: Optional[int] = None
        try:
            prior_runs = client.list_workflow_runs(
                dispatch_owner, dispatch_name, wf, branch=dispatch_branch, limit=1
            )
            if prior_runs:
                previous_run_id = prior_runs[0].get("id")
        except GitHubActionsError as e:
            logger.warning("Could not fetch prior workflow run for %s: %s", wf, e)

        try:
            client.trigger_workflow(
                dispatch_owner,
                dispatch_name,
                wf,
                dispatch_branch,
                inputs=inputs or None,
            )
        except GitHubActionsError as e:
            errors.append(f"{wf}: {e}")
            continue

        run = _wait_for_new_workflow_run(
            client, dispatch_owner, dispatch_name, wf, dispatch_branch, previous_run_id
        )
        triggered.append(
            {
                "workflow": wf,
                "ref": dispatch_branch,
                "run_id": run.get("id") if run else None,
                "status": run.get("status") if run else None,
                "conclusion": run.get("conclusion") if run else None,
                "html_url": run.get("html_url") if run else None,
            }
        )

    if not triggered and errors:
        raise DevOpsError("; ".join(errors))

    lines = [
        f"Triggered {len(triggered)} workflow(s) on {target.dispatch_repo} @ {dispatch_branch}.",
    ]
    if cross_repo:
        site = (inputs or {}).get("site")
        extra = f"site={site}, ref={product_ref}" if site else f"ref={product_ref}"
        lines.append(f"Product repo {target.repo} ({extra}).")
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
        "deploy_repo": target.dispatch_repo,
        "project_key": target.project_key,
        "ref": product_ref,
        "dispatch_ref": dispatch_branch,
        "triggered": triggered,
        "errors": errors,
        "site_urls": target.site_urls,
        "workflow_inputs": inputs or None,
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


def _log_zip_max_bytes() -> int:
    raw = (os.environ.get("BIGAS_CI_LOG_ZIP_MAX_BYTES") or "").strip()
    if raw.isdigit():
        return max(1024, int(raw))
    return DEFAULT_LOG_ZIP_MAX_BYTES


def fetch_github_action_logs(
    *,
    repo: str,
    run_id: int,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Download and parse logs from failed jobs on a GitHub Actions workflow run."""
    owner, name = parse_repo(repo)
    client = _github_client(github_token)
    try:
        jobs = client.list_workflow_jobs(owner, name, int(run_id))
    except GitHubActionsError as e:
        raise DevOpsError(str(e)) from e

    failed = find_failed_jobs(jobs)
    if not failed:
        raise DevOpsError(f"No failed jobs found for workflow run #{run_id}")

    max_bytes = _log_zip_max_bytes()
    zip_size = None
    zip_path: Optional[str] = None
    try:
        zip_size = client.get_run_logs_zip_size(owner, name, int(run_id))
    except GitHubActionsError:
        zip_size = None

    use_zip = zip_size is None or zip_size <= max_bytes
    if use_zip:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp.close()
        zip_path = tmp.name
        try:
            client.download_run_logs_zip(owner, name, int(run_id), max_bytes=max_bytes, dest_path=zip_path)
        except Exception as e:
            logger.warning("Could not download run logs zip for run #%s: %s", run_id, e)
            use_zip = False
            try:
                os.unlink(zip_path)
            except OSError:
                pass
            zip_path = None

    parts: List[str] = []
    job_names: List[str] = []
    for job in failed[:3]:
        job_name = (job.get("name") or "job").strip()
        job_names.append(job_name)
        raw = ""
        if use_zip and zip_path:
            try:
                raw = extract_job_logs_from_zip(zip_path, job_name)
            except (OSError, zipfile.ZipError) as e:
                logger.warning("Could not extract zip logs for job %s: %s", job_name, e)
        if not raw.strip():
            job_id = job.get("id")
            if job_id:
                try:
                    raw = client.get_job_logs(owner, name, int(job_id))
                except GitHubActionsError as e:
                    parts.append(f"### {job_name}\n(could not fetch logs: {e})")
                    continue
        excerpt = truncate_log_text(raw) if raw.strip() else ""
        if excerpt:
            parts.append(f"### {job_name}\n{excerpt}")

    if zip_path:
        try:
            os.unlink(zip_path)
        except OSError:
            pass

    combined = "\n\n".join(parts).strip()
    if not combined:
        combined = f"No failed-job logs found for run #{run_id}."
    names = ", ".join(job_names) or "unknown job"
    return {
        "status": "ok",
        "summary": f"Parsed failed job logs for run #{run_id} ({names}).",
        "repo": repo,
        "run_id": int(run_id),
        "failed_job_names": job_names,
        "logs": combined[:32000],
        "zip_size_bytes": zip_size,
        "used_zip_download": use_zip,
    }


def get_commit_diff(
    *,
    repo: str,
    commit_sha: str,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch the diff/patches for a commit."""
    owner, name = parse_repo(repo)
    client = _github_client(github_token)
    try:
        commit = client.get_commit(owner, name, commit_sha.strip())
    except GitHubActionsError as e:
        raise DevOpsError(str(e)) from e
    diff_text = format_commit_diff(commit)
    sha = (commit.get("sha") or commit_sha).strip()
    return {
        "status": "ok",
        "summary": f"Fetched diff for commit {sha[:12]}.",
        "repo": repo,
        "commit_sha": sha,
        "diff": diff_text,
        "files_changed": len(commit.get("files") or []),
    }


def create_github_pr(
    *,
    repo: str,
    base_branch: str,
    new_branch_name: str,
    title: str,
    body: str,
    files_to_change: Dict[str, str],
    github_token: Optional[str] = None,
    base_commit_sha: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a branch with file changes and open a pull request."""
    if not files_to_change:
        raise DevOpsError("files_to_change must include at least one file path")
    branch = (new_branch_name or "").strip()
    if not branch.startswith(HOTFIX_BRANCH_PREFIX):
        raise DevOpsError(f"new_branch_name must start with {HOTFIX_BRANCH_PREFIX}")

    owner, name = parse_repo(repo)
    client = _github_client(github_token)
    base = (base_branch or client.get_default_branch(owner, name)).strip()
    parent_sha = (base_commit_sha or "").strip()
    if not parent_sha:
        try:
            parent_sha = client.get_ref_sha(owner, name, base)
        except GitHubActionsError as e:
            raise DevOpsError(str(e)) from e
    try:
        base_commit = client.get_commit(owner, name, parent_sha)
    except GitHubActionsError as e:
        raise DevOpsError(str(e)) from e

    base_tree = ((base_commit.get("commit") or {}).get("tree") or {}).get("sha") or ""
    if not base_tree:
        raise DevOpsError(f"Could not resolve tree for base branch {base}")

    entries: List[Dict[str, str]] = []
    for path, content in files_to_change.items():
        filename = (path or "").strip().lstrip("/")
        if not filename:
            continue
        blob_sha = client.create_blob(owner, name, content)
        entries.append({"path": filename, "mode": "100644", "type": "blob", "sha": blob_sha})
    if not entries:
        raise DevOpsError("No valid file paths in files_to_change")

    try:
        tree_sha = client.create_tree(owner, name, base_tree, entries)
        commit_sha = client.create_git_commit(
            owner,
            name,
            message=title.strip() or f"fix: {branch}",
            tree_sha=tree_sha,
            parent_sha=parent_sha,
        )
        client.create_branch_ref(owner, name, branch, commit_sha)
        pr = client.create_pull_request(
            owner,
            name,
            title=title.strip() or f"Hotfix: {branch}",
            body=(body or "").strip() or "Automated hotfix from Bigas self-healing CI.",
            head=branch,
            base=base,
        )
    except GitHubActionsError as e:
        raise DevOpsError(str(e)) from e

    pr_number = pr.get("number")
    html_url = (pr.get("html_url") or "").strip()
    return {
        "status": "ok",
        "summary": f"Opened pull request #{pr_number} from `{branch}` → `{base}`.",
        "repo": repo,
        "base_branch": base,
        "head_branch": branch,
        "pr_number": pr_number,
        "html_url": html_url,
        "commit_sha": commit_sha,
    }


def get_failed_run_excerpt(
    *,
    repo: str,
    run_id: int,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch a short log excerpt from failed jobs on a workflow run."""
    fetched = fetch_github_action_logs(repo=repo, run_id=run_id, github_token=github_token)
    return {
        "status": "ok",
        "summary": fetched.get("summary") or "",
        "repo": repo,
        "run_id": int(run_id),
        "failed_job_names": fetched.get("failed_job_names") or [],
        "excerpt": (fetched.get("logs") or "")[:8000],
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
