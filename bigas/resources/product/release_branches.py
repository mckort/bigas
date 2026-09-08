"""Versioned staging branches (staging-0.2.3) and post-ship rebase onto main."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from bigas.resources.devops.github_actions import (
    GitHubActionsClient,
    GitHubActionsError,
    GitHubMergeConflict,
)
from bigas.resources.product.jira_automation.config import JiraAutomationConfig
from bigas.resources.product.release_workflow import (
    feature_branch_prefix,
    uses_versioned_feature_branches,
    version_from_feature_branch,
    versioned_feature_branch,
)
from bigas.tickets.semver import SemverError, parse_semver, versions_match

logger = logging.getLogger(__name__)

REBASE_WORKFLOW = "rebase_release.yml"


def _github_client(token: Optional[str] = None) -> GitHubActionsClient:
    value = (token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not value:
        raise GitHubActionsError("GITHUB_TOKEN is required for release branches")
    return GitHubActionsClient(value)


def _split_repo(repo: str) -> tuple[str, str]:
    text = (repo or "").strip()
    if "/" not in text:
        raise GitHubActionsError(f"Invalid repo: {repo!r}")
    owner, name = text.split("/", 1)
    return owner.strip(), name.strip()


def mapped_feature_prefix(
    project_key: str,
    repo: str,
    *,
    config: Optional[JiraAutomationConfig] = None,
) -> tuple[str, str]:
    """Return (feature_prefix, production_branch) for a project."""
    cfg = config or JiraAutomationConfig.from_env()
    production = (cfg.base_branch_for_repo(repo) or "main").strip()
    mapped = (cfg.automerge_branch_for_project(project_key, repo) or production).strip()
    return feature_branch_prefix(mapped), production


def _oldest_unreleased_version(project_key: str) -> Optional[str]:
    try:
        from bigas.tickets.releases import list_releases
    except Exception:
        return None
    candidates: List[tuple] = []
    for item in list_releases(project_key) or []:
        if item.get("released"):
            continue
        name = (item.get("name") or "").strip()
        try:
            candidates.append((parse_semver(name), name))
        except SemverError:
            continue
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def should_inherit_legacy_staging(project_key: str, version: Optional[str]) -> bool:
    """Copy unversioned `staging` only for the oldest open cut (one-time migration)."""
    ver = (version or "").strip()
    if not ver or not project_key:
        return False
    oldest = _oldest_unreleased_version(project_key)
    return bool(oldest and versions_match(ver, oldest))


def ensure_versioned_release_branch(
    *,
    repo: str,
    branch: str,
    production: str = "main",
    prefix: str = "staging",
    client: Optional[GitHubActionsClient] = None,
    github_token: Optional[str] = None,
    inherit_legacy_prefix: bool = False,
) -> Dict[str, Any]:
    """
    Create ``branch`` from ``production`` when missing.

    When ``inherit_legacy_prefix`` is true (oldest open board version) and the
    unversioned prefix is ahead of production, copy from that prefix so in-flight
    work on `staging` is not orphaned.
    """
    wanted = (branch or "").strip()
    onto = (production or "main").strip() or "main"
    mapped = (prefix or "").strip() or "staging"
    if not wanted or wanted == onto:
        return {"branch": wanted or onto, "created": False, "source": "production"}

    gh = client or _github_client(github_token)
    owner, name = _split_repo(repo)
    if gh.branch_exists(owner, name, wanted):
        return {"branch": wanted, "created": False, "source": "existing"}

    source = onto
    if inherit_legacy_prefix and mapped and mapped != onto and gh.branch_exists(owner, name, mapped):
        try:
            compare = gh.compare_refs(owner, name, onto, mapped)
            ahead = int(compare.get("ahead_by") or 0) or len(compare.get("commits") or [])
        except GitHubActionsError:
            ahead = 0
        if ahead > 0:
            source = mapped

    created_from = gh.ensure_branch_from_ref(owner, name, wanted, source)
    return {
        "branch": wanted,
        "created": created_from != "existing",
        "source": source if created_from != "existing" else "existing",
    }


def resolve_implement_base_branch(
    *,
    project_key: str,
    repo: str,
    labels: Optional[Iterable[str]] = None,
    fix_version: Optional[str] = None,
    mapped_branch: str = "",
    config: Optional[JiraAutomationConfig] = None,
    github_token: Optional[str] = None,
) -> str:
    """Versioned staging branch for implement, creating it from main if needed."""
    cfg = config or JiraAutomationConfig.from_env()
    branch = (
        cfg.automerge_branch_for_project(
            project_key,
            repo,
            labels=labels,
            fix_version=fix_version,
        )
        or (mapped_branch or "").strip()
        or "main"
    )
    prefix, production = mapped_feature_prefix(project_key, repo, config=cfg)
    if not uses_versioned_feature_branches(prefix, production):
        return branch
    if not version_from_feature_branch(branch):
        return branch
    try:
        ensure_versioned_release_branch(
            repo=repo,
            branch=branch,
            production=production,
            prefix=prefix,
            github_token=github_token,
            inherit_legacy_prefix=should_inherit_legacy_staging(
                project_key, fix_version or version_from_feature_branch(branch)
            ),
        )
    except GitHubActionsError as exc:
        logger.warning(
            "Could not ensure release branch %s on %s: %s",
            branch,
            repo,
            exc,
        )
        raise
    return branch


def newer_release_branch_names(
    *,
    project_key: str,
    repo: str,
    shipped_version: str,
    prefix: str,
    production: str,
    client: GitHubActionsClient,
) -> List[str]:
    """Versioned staging branches newer than the version that just shipped."""
    from bigas.tickets.releases import unreleased_versions_after

    try:
        shipped = parse_semver(shipped_version)
    except SemverError:
        return []

    owner, name = _split_repo(repo)
    wanted: Dict[str, str] = {}
    for version in unreleased_versions_after(project_key, shipped_version):
        branch = versioned_feature_branch(prefix, version)
        if branch:
            wanted[branch] = version
    for branch in client.list_versioned_feature_branches(owner, name, prefix):
        ver = version_from_feature_branch(branch)
        if not ver:
            continue
        try:
            if parse_semver(ver) > shipped:
                wanted[branch] = ver
        except SemverError:
            continue

    existing = []
    for branch, _ver in sorted(wanted.items(), key=lambda item: parse_semver(item[1])):
        if branch == production:
            continue
        if client.branch_exists(owner, name, branch):
            existing.append(branch)
    return existing


def _launch_conflict_agent(
    *,
    repo: str,
    release_branch: str,
    onto: str,
    shipped_version: str,
    pr_url: str = "",
) -> Dict[str, Any]:
    api_key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if not api_key:
        return {"ok": False, "error": "CURSOR_API_KEY is required to fix rebase conflicts"}
    from bigas.resources.cto.autofix.cursor_client import (
        CursorCloudAgentClient,
        CursorCloudAgentError,
    )

    prompt = (
        f"Release {shipped_version} just shipped to `{onto}`. "
        f"The next release branch `{release_branch}` must sit on that new `{onto}`.\n\n"
        f"1. Fetch origin and rebase `{release_branch}` onto `origin/{onto}` "
        f"(merge `origin/{onto}` only if rebase is too messy).\n"
        f"2. Resolve every conflict. Keep both the newly shipped production changes "
        f"and the unreleased work already on `{release_branch}`.\n"
        f"3. Open or update a PR **into `{release_branch}`** — never into `{onto}`/`main`.\n"
        f"4. Do not expand scope."
    )
    if pr_url:
        prompt += (
            f"\n\nA conflict PR is already open: {pr_url}. "
            "Work on that PR's head branch, remove conflict markers, and push."
        )
    client = CursorCloudAgentClient(api_key=api_key)
    try:
        if pr_url:
            launched = client.launch_pr_autofix(
                repo_url=f"https://github.com/{repo}",
                pr_url=pr_url,
                prompt_text=prompt,
                name=f"Bigas rebase {release_branch}"[:100],
            )
        else:
            launched = client.launch_implementation(
                repo_url=f"https://github.com/{repo}",
                prompt_text=prompt,
                starting_ref=release_branch,
                name=f"Bigas rebase {release_branch}"[:100],
            )
    except CursorCloudAgentError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "agent_id": launched.get("agent_id") or "",
        "agent_url": launched.get("agent_url") or "",
        "run_id": launched.get("run_id") or "",
        "pr_url": pr_url,
    }


def _sync_one_branch(
    client: GitHubActionsClient,
    *,
    owner: str,
    name: str,
    repo: str,
    release_branch: str,
    onto: str,
    shipped_version: str,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"branch": release_branch, "onto": onto}
    try:
        client.trigger_workflow(
            owner,
            name,
            REBASE_WORKFLOW,
            ref=onto,
            inputs={
                "release_branch": release_branch,
                "onto_branch": onto,
                "shipped_version": shipped_version,
            },
        )
        result["mode"] = "workflow_dispatch"
        result["workflow"] = REBASE_WORKFLOW
        result["ok"] = True
        return result
    except GitHubActionsError as exc:
        logger.info(
            "Rebase workflow unavailable for %s/%s %s: %s",
            owner,
            name,
            release_branch,
            exc,
        )
        result["workflow_error"] = str(exc)

    try:
        merged = client.merge_branches(
            owner,
            name,
            base=release_branch,
            head=onto,
            message=f"Sync {release_branch} onto {onto} after {shipped_version}",
        )
        result["mode"] = "merge"
        result["ok"] = True
        result["merge"] = merged
        return result
    except GitHubMergeConflict:
        launched = _launch_conflict_agent(
            repo=repo,
            release_branch=release_branch,
            onto=onto,
            shipped_version=shipped_version,
        )
        result["mode"] = "conflict_agent"
        result["ok"] = bool(launched.get("ok"))
        result["conflict"] = True
        result.update(launched)
        return result
    except GitHubActionsError as exc:
        result["mode"] = "merge"
        result["ok"] = False
        result["error"] = str(exc)
        return result


def rebase_newer_release_branches(
    *,
    project_key: str,
    shipped_version: str,
    repo: Optional[str] = None,
    thread_id: Optional[str] = None,
    config: Optional[JiraAutomationConfig] = None,
) -> Dict[str, Any]:
    """Rebase (or merge) newer staging-* branches onto production after a ship."""
    cfg = config or JiraAutomationConfig.from_env()
    mapped_repo = (repo or cfg.repo_for_project(project_key) or "").strip()
    if not mapped_repo:
        return {"ok": True, "skipped": True, "reason": "no repo mapped"}

    prefix, production = mapped_feature_prefix(project_key, mapped_repo, config=cfg)
    if not uses_versioned_feature_branches(prefix, production):
        return {"ok": True, "skipped": True, "reason": "project does not use staging"}

    try:
        client = _github_client()
    except GitHubActionsError as exc:
        return {"ok": False, "error": str(exc)}

    branches = newer_release_branch_names(
        project_key=project_key,
        repo=mapped_repo,
        shipped_version=shipped_version,
        prefix=prefix,
        production=production,
        client=client,
    )
    if not branches:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no newer release branches",
            "branches": [],
        }

    owner, name = _split_repo(mapped_repo)
    results = [
        _sync_one_branch(
            client,
            owner=owner,
            name=name,
            repo=mapped_repo,
            release_branch=branch,
            onto=production,
            shipped_version=shipped_version,
        )
        for branch in branches
    ]
    _post_rebase_summary(thread_id, shipped_version, results)
    return {
        "ok": all(item.get("ok") for item in results),
        "skipped": False,
        "branches": branches,
        "results": results,
    }


def _post_rebase_summary(
    thread_id: Optional[str],
    shipped_version: str,
    results: List[Dict[str, Any]],
) -> None:
    if not thread_id or not results:
        return
    try:
        from bigas.resources.devops.prepare import _post
    except Exception:
        return
    lines = [f"Rebasing newer release branches onto `main` after **{shipped_version}**:"]
    for item in results:
        branch = item.get("branch") or ""
        if item.get("mode") == "workflow_dispatch":
            lines.append(f"- `{branch}` — rebase workflow dispatched")
        elif item.get("conflict"):
            agent = item.get("agent_url") or ""
            extra = f" Cursor: {agent}" if agent else ""
            err = item.get("error") or ""
            if err and not agent:
                extra = f" ({err})"
            lines.append(f"- `{branch}` — conflicts; launched a fix agent.{extra}")
        elif item.get("ok"):
            lines.append(f"- `{branch}` — synced with `main`")
        else:
            lines.append(f"- `{branch}` — failed ({item.get('error') or 'unknown'})")
    _post(thread_id, "\n".join(lines))
