"""Move a project from development to production (versioned releases).

Development: PRs merge to the production branch (main/master) and DevOps
deploys it. Production: work for version x.y.z lands on ``staging-x.y.z``;
"Prepare deploy" reviews the cut, merges it to the production branch through
a PR, deploys, tags and opens the next version.

``plan()`` is a dry run that reports each step as done, todo or blocked.
``apply()`` runs the steps that are not done yet:

1. ``workflows``: one PR (branch ``bigas-production``) adding what the repo
   lacks: the Bigas PR review caller, ``cherry_pick.yml`` (hotfixes) and
   ``rebase_release.yml`` (rebase newer release branches after a ship).
2. ``protect``: a GitHub ruleset on the production branch: pull request
   required, no force pushes or deletion; repository admins can bypass.
3. ``release``: the first board version (default ``0.1.0``) when the project
   has no open version.
4. ``branch``: ``staging-<version>`` from the production branch.
5. ``mode``: the project setting that makes AI work target that branch.

Projects with a staging-rehearsal environment (GPW-PROD) or a versioned
``PROJECT_BRANCH_MAPPING`` entry in env are already in production.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
SETUP_BRANCH = "bigas-production"
DEFAULT_FIRST_VERSION = "0.1.0"
VERSIONED_WORKFLOWS = ("cherry_pick.yml", "rebase_release.yml")
PR_REVIEW_PATH = ".github/workflows/pr-review.yml"


class GoLiveError(RuntimeError):
    pass


def _workflow_source() -> str:
    return (os.environ.get("BIGAS_WORKFLOW_SOURCE") or "mckort/bigas@main").strip()


def wanted_files() -> Dict[str, str]:
    from bigas.setup_repo import PR_REVIEW_CALLER_TEMPLATE, caller_workflow

    files = {PR_REVIEW_PATH: caller_workflow(PR_REVIEW_CALLER_TEMPLATE, _workflow_source())}
    for name in VERSIONED_WORKFLOWS:
        path = ROOT / ".github" / "workflows" / name
        files[f".github/workflows/{name}"] = path.read_text(encoding="utf-8")
    return files


def release_state(project_key: str) -> Dict[str, Any]:
    """Where the project is today: mode (development/production), source, repo, branches."""
    from bigas.project_settings import get_settings
    from bigas.resources.devops.gpw_pipeline import staging_envs
    from bigas.resources.product.jira_automation.config import JiraAutomationConfig
    from bigas.resources.product.release_workflow import (
        parse_project_branch_mapping,
        uses_versioned_feature_branches,
    )

    key = (project_key or "").strip().upper()
    cfg = JiraAutomationConfig.from_env()
    repo = (cfg.repo_for_project(key) or "").strip()
    production = cfg.base_branch_for_repo(repo) if repo else "main"
    env_map = parse_project_branch_mapping(
        os.environ.get("PROJECT_BRANCH_MAPPING") or os.environ.get("BIGAS_PROJECT_BRANCH_MAPPING")
    )
    setting = get_settings(key)
    state = {
        "project_key": key,
        "repo": repo,
        "production_branch": production,
        "release_prefix": "",
        "mode": "development",
        "source": "",
    }
    if key in staging_envs():
        state.update(mode="production", source="staging")
    elif key in env_map and uses_versioned_feature_branches(env_map[key], production):
        state.update(mode="production", source="env", release_prefix=env_map[key])
    elif setting.get("release_mode") == "production":
        state.update(mode="production", source="app", release_prefix=setting.get("release_prefix") or "staging")
    return state


def _client():
    from bigas.resources.devops.github_actions import GitHubActionsClient

    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        raise GoLiveError("GITHUB_TOKEN is not set on the Bigas server")
    return GitHubActionsClient(token)


def _open_release(project_key: str) -> Optional[Dict[str, Any]]:
    from bigas.tickets.releases import list_releases

    open_items = [r for r in list_releases(project_key) if not r.get("released")]
    for r in open_items:
        if r.get("is_default"):
            return r
    return open_items[-1] if open_items else None  # lowest open version (list is newest first)


def _missing_files(gh, owner: str, name: str, branch: str) -> List[str]:
    missing = []
    for path, content in wanted_files().items():
        current = gh.get_file_text(owner, name, path, branch)
        if current is None:
            missing.append(path)
        elif path == PR_REVIEW_PATH and "/.github/workflows/pr-review.yml@" not in current:
            missing.append(path)  # a different review workflow: replace with the Bigas caller
    return missing


def plan(project_key: str, *, version: str = "", client=None) -> Dict[str, Any]:
    from bigas.tickets.semver import SemverError, normalize_version_name

    state = release_state(project_key)
    key = state["project_key"]
    steps: List[Dict[str, Any]] = []
    blocked = ""
    if state["mode"] == "production":
        blocked = {
            "staging": "Already in production with a staging rehearsal environment.",
            "env": f"Already in production: PROJECT_BRANCH_MAPPING has {key}:{state['release_prefix']}.",
            "app": "Already in production.",
        }[state["source"]]
    elif not state["repo"]:
        blocked = f"{key} has no GitHub repo. Map it first (scripts/connect_repo.py)."

    try:
        release = _open_release(key)
        wanted = (release or {}).get("name") or normalize_version_name(version or DEFAULT_FIRST_VERSION)
    except SemverError:
        return {**state, "steps": [], "can_apply": False, "blocked": f"{version!r} is not a version like 0.1.0"}
    prefix = state["release_prefix"] or "staging"
    branch = f"{prefix}-{wanted}"

    if blocked:
        return {**state, "version": wanted, "release_branch": branch, "steps": [], "can_apply": False, "blocked": blocked}

    try:
        gh = client or _client()
        owner, name = state["repo"].split("/", 1)
        if not gh.branch_exists(owner, name, state["production_branch"]):
            raise GoLiveError(f"Branch {state['production_branch']} not found on {state['repo']}")
        missing = _missing_files(gh, owner, name, state["production_branch"])
        open_pr = gh.find_open_pull_request(owner, name, head=SETUP_BRANCH, base=state["production_branch"])
        rulesets = gh.list_repo_rulesets(owner, name)
        protected = any(
            (r or {}).get("name") == f"Bigas: protect {state['production_branch']}" for r in rulesets
        )
        branch_exists = gh.branch_exists(owner, name, branch)
    except Exception as exc:
        return {
            **state,
            "version": wanted,
            "release_branch": branch,
            "steps": [],
            "can_apply": False,
            "blocked": f"Could not read {state['repo']} on GitHub: {exc}",
        }

    if not missing:
        wf = {"status": "done", "detail": "Review and release workflows are already on " + state["production_branch"] + "."}
    elif open_pr:
        wf = {"status": "pending", "detail": f"Pull request open, merge it: {open_pr.get('html_url')}", "url": open_pr.get("html_url")}
    else:
        wf = {"status": "todo", "detail": "Open a pull request adding " + ", ".join(p.rsplit("/", 1)[-1] for p in missing) + "."}
    steps.append({"id": "workflows", "title": "Add the release workflows", **wf})
    steps.append(
        {
            "id": "protect",
            "title": f"Protect {state['production_branch']}",
            "status": "done" if protected else "todo",
            "detail": (
                f"Ruleset in place: changes reach {state['production_branch']} only through a pull request."
                if protected
                else f"Add a ruleset: pull request required on {state['production_branch']}, no force pushes or deletion. Admins can bypass."
            ),
        }
    )
    steps.append(
        {
            "id": "release",
            "title": f"First version {wanted}",
            "status": "done" if release else "todo",
            "detail": f"Version {wanted} is open on the board." if release else f"Create version {wanted} on the {key} board.",
        }
    )
    steps.append(
        {
            "id": "branch",
            "title": f"Release branch {branch}",
            "status": "done" if branch_exists else "todo",
            "detail": f"{branch} exists." if branch_exists else f"Create {branch} from {state['production_branch']}.",
        }
    )
    steps.append(
        {
            "id": "mode",
            "title": "Switch to versioned releases",
            "status": "todo",
            "detail": f"New AI work on {key} targets {branch}. Cards already in progress keep their branch.",
        }
    )
    return {**state, "version": wanted, "release_branch": branch, "steps": steps, "can_apply": True, "blocked": ""}


def _open_workflow_pr(gh, state: Dict[str, Any], missing: List[str]) -> str:
    owner, name = state["repo"].split("/", 1)
    base = state["production_branch"]
    existing = gh.find_open_pull_request(owner, name, head=SETUP_BRANCH, base=base)
    base_sha = gh.get_ref_sha(owner, name, base)
    if gh.branch_exists(owner, name, SETUP_BRANCH):
        if not existing:
            gh.update_branch_ref(owner, name, SETUP_BRANCH, base_sha, force=True)
    else:
        gh.create_branch_ref(owner, name, SETUP_BRANCH, base_sha)
    head_sha = gh.get_ref_sha(owner, name, SETUP_BRANCH)
    files = wanted_files()
    entries = [
        {"path": path, "mode": "100644", "type": "blob", "sha": gh.create_blob(owner, name, files[path])}
        for path in missing
    ]
    tree = gh.create_tree(owner, name, gh.get_commit(owner, name, head_sha)["commit"]["tree"]["sha"], entries)
    commit = gh.create_git_commit(
        owner, name, message="Bigas: add release workflows for versioned deploys", tree_sha=tree, parent_sha=head_sha
    )
    gh.update_branch_ref(owner, name, SETUP_BRANCH, commit, force=False)
    if existing:
        return existing.get("html_url") or ""
    body = "\n".join(
        [
            f"Moves **{state['project_key']}** to versioned releases. Opened by the Bigas cockpit (*Go to production*).",
            "",
            "- `pr-review.yml`: Bigas review → autofix → merge on every PR.",
            "- `cherry_pick.yml`: hotfixes from a release branch onto the production branch.",
            "- `rebase_release.yml`: newer release branches are rebased after a version ships.",
            "",
            "Merge this so hotfixes and post-release rebases work. Needs Actions variable `BIGAS_URL` and secret `BIGAS_API_KEY`.",
        ]
    )
    pr = gh.create_pull_request(
        owner, name, title="Bigas: versioned releases", body=body, head=SETUP_BRANCH, base=base
    )
    return pr.get("html_url") or ""


def apply(project_key: str, *, version: str = "", by: str = "", client=None) -> Dict[str, Any]:
    from bigas.project_settings import set_release_mode
    from bigas.resources.product.release_branches import ensure_versioned_release_branch
    from bigas.tickets.releases import create_release

    current = plan(project_key, version=version, client=client)
    if not current.get("can_apply"):
        raise GoLiveError(current.get("blocked") or "Cannot go to production")
    gh = client or _client()
    state = {k: current[k] for k in ("project_key", "repo", "production_branch")}
    owner, name = state["repo"].split("/", 1)
    results: List[Dict[str, Any]] = []

    def run(step_id: str, fn: Callable[[], str]) -> bool:
        step = next(s for s in current["steps"] if s["id"] == step_id)
        if step["status"] in ("done", "pending"):
            results.append({"id": step_id, "status": "skipped", "detail": step["detail"]})
            return True
        try:
            detail = fn()
            results.append({"id": step_id, "status": "ok", "detail": detail})
            return True
        except Exception as exc:
            logger.warning("Go to production step %s failed for %s", step_id, state["project_key"], exc_info=True)
            results.append({"id": step_id, "status": "error", "detail": str(exc)[:300]})
            return False

    def workflows() -> str:
        url = _open_workflow_pr(gh, state, _missing_files(gh, owner, name, state["production_branch"]))
        return f"Pull request opened: {url}" if url else "Pull request opened"

    def protect() -> str:
        gh.upsert_protect_branch_ruleset(owner, name, state["production_branch"])
        return f"{state['production_branch']} now needs a pull request"

    def release() -> str:
        create_release(state["project_key"], name=current["version"], is_default=True)
        return f"Version {current['version']} created on the board"

    def branch() -> str:
        ensure_versioned_release_branch(
            repo=state["repo"],
            branch=current["release_branch"],
            production=state["production_branch"],
            prefix=current["release_branch"].rsplit("-", 1)[0],
            client=gh,
        )
        return f"{current['release_branch']} created from {state['production_branch']}"

    run("workflows", workflows)
    run("protect", protect)
    ok_release = run("release", release)
    ok_branch = run("branch", branch)
    if ok_release and ok_branch:
        run(
            "mode",
            lambda: (
                set_release_mode(state["project_key"], "production", prefix=current["release_branch"].rsplit("-", 1)[0], by=by)
                and f"{state['project_key']} is in production. New work targets {current['release_branch']}."
            ),
        )
    else:
        results.append({"id": "mode", "status": "skipped", "detail": "Not switched: the version or its branch failed."})
    return {
        "project_key": state["project_key"],
        "version": current["version"],
        "release_branch": current["release_branch"],
        "results": results,
        "in_production": release_state(state["project_key"])["mode"] == "production",
    }


def back_to_development(project_key: str, *, by: str = "") -> Dict[str, Any]:
    """Undo the app setting. Leaves the ruleset, workflows, branches and versions in place."""
    from bigas.project_settings import set_release_mode

    state = release_state(project_key)
    if state["source"] in ("env", "staging"):
        raise GoLiveError(
            "This project is in production through server config "
            + ("(PROJECT_BRANCH_MAPPING)." if state["source"] == "env" else "(staging environment).")
        )
    set_release_mode(state["project_key"], "development", by=by)
    return release_state(state["project_key"])
