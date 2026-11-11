"""Setup checks for a Bigas instance and the GitHub repos it drives.

Used by ``scripts/bigas_doctor.py``. Every check returns a ``Check`` with a
status (``ok``, ``warn``, ``fail``, ``info``) and, when something is missing,
a ``fix`` an operator or an AI agent can run. Read ``docs/agent-setup.md``
for the procedure these checks verify.

GitHub checks use the REST API with ``GITHUB_TOKEN`` and never print secret
values.
"""
from __future__ import annotations

import base64
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

LLM_KEYS = ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")
PR_REVIEW_PATH = ".github/workflows/pr-review.yml"
VERSIONED_WORKFLOWS = ("cherry_pick.yml", "rebase_release.yml")
WEBHOOK_PATH = "/mcp/tools/github_workflow_run"
_PLACEHOLDER_MARKERS = ("your_", "_here", "changeme")


@dataclass
class Check:
    id: str
    status: str  # ok | warn | fail | info
    message: str
    fix: str = ""
    project: str = ""


@dataclass
class Project:
    key: str
    name: str
    repo: str
    base_branch: str
    deploy_repo: str = ""
    deploy_workflows: List[str] = field(default_factory=list)
    release_branch: str = ""  # e.g. "staging" → staging-0.2.3 branches
    staging_env: Optional[Dict[str, Any]] = None


def _set(name: str) -> bool:
    value = (os.environ.get(name) or "").strip()
    if not value:
        return False
    lower = value.lower()
    return not any(marker in lower for marker in _PLACEHOLDER_MARKERS)


def public_url() -> str:
    """The URL GitHub Actions use to reach Bigas. Only read by setup scripts."""
    return (os.environ.get("BIGAS_PUBLIC_URL") or "").strip().rstrip("/")


def load_projects() -> List[Project]:
    """Projects from env, resolved the same way the running server resolves them."""
    from bigas.portfolio import brand_name, jira_project_keys, repo_map
    from bigas.resources.devops.config import resolve_deploy_target
    from bigas.resources.devops.gpw_pipeline import staging_envs
    from bigas.resources.product.jira_automation.config import JiraAutomationConfig
    from bigas.resources.product.release_workflow import (
        project_branch_mapping_from_env,
        uses_versioned_feature_branches,
    )

    cfg = JiraAutomationConfig.from_env()
    repos = repo_map()
    branch_map = project_branch_mapping_from_env()
    envs = staging_envs()
    projects: List[Project] = []
    for key in jira_project_keys():
        repo = repos.get(key, "")
        base = cfg.base_branch_for_repo(repo) if repo else ""
        target = resolve_deploy_target(project_key=key) if repo else None
        mapped = branch_map.get(key, "")
        release_branch = mapped if mapped and uses_versioned_feature_branches(mapped, base or "main") else ""
        env = envs.get(key)
        projects.append(
            Project(
                key=key,
                name=brand_name(key),
                repo=repo,
                base_branch=base,
                deploy_repo=(target.dispatch_repo if target else ""),
                deploy_workflows=list(target.workflows) if target else [],
                release_branch=release_branch,
                staging_env=(
                    {
                        "repo": env.repo,
                        "candidate_branch": env.candidate_branch,
                        "production_branch": env.production_branch,
                        "workflows": dict(env.workflows),
                    }
                    if env
                    else None
                ),
            )
        )
    return projects


def instance_checks() -> List[Check]:
    from bigas.features import enabled_features

    checks: List[Check] = []
    llm = [name for name in LLM_KEYS if _set(name)]
    checks.append(
        Check("llm_key", "ok", f"LLM key set: {', '.join(llm)}")
        if llm
        else Check(
            "llm_key",
            "fail",
            "No LLM key. Every agent needs one.",
            "Set GEMINI_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY in .env.",
        )
    )

    storage = (os.environ.get("CHAT_STORAGE_MODE") or "firestore").strip().lower()
    auth = (os.environ.get("CHAT_AUTH_MODE") or "").strip().lower() or "auto"
    if storage == "memory" or auth == "dev":
        checks.append(
            Check(
                "chat_mode",
                "warn",
                f"Local mode (CHAT_STORAGE_MODE={storage}, CHAT_AUTH_MODE={auth}). "
                "Fine on a laptop; data is lost on restart and anyone with the dev token can sign in.",
                "For a server: CHAT_STORAGE_MODE=firestore, CHAT_AUTH_MODE=firebase (README: Chat web interface → Setup).",
            )
        )
    else:
        checks.append(Check("chat_mode", "ok", f"Chat storage {storage}, auth {auth}"))

    if _set("BIGAS_ACCESS_KEYS"):
        checks.append(Check("access_keys", "ok", "BIGAS_ACCESS_KEYS set (GitHub workflows use one as BIGAS_API_KEY)"))
    else:
        checks.append(
            Check(
                "access_keys",
                "warn",
                "BIGAS_ACCESS_KEYS is empty. GitHub workflows need one of these keys as the BIGAS_API_KEY secret.",
                "Generate one: python -c 'import secrets; print(secrets.token_urlsafe(32))' and set BIGAS_ACCESS_KEYS=<value>.",
            )
        )

    url = public_url()
    if not url or "localhost" in url or "127.0.0.1" in url:
        checks.append(
            Check(
                "public_url",
                "warn",
                "No public Bigas URL. GitHub Actions cannot reach a laptop, so PR review and deploy callbacks need a deployed server.",
                "Deploy (README: Tutorial: deploy your first Bigas server), then set BIGAS_PUBLIC_URL=https://<your-service>.run.app.",
            )
        )
    else:
        checks.append(Check("public_url", "ok", f"Public URL {url}"))

    for name, why, fix in (
        ("GITHUB_TOKEN", "PR review comments, merges, release branches and deploy dispatch", "Fine-grained PAT on the product repos: Contents, Pull requests, Actions, Workflows read/write; Administration read for checks."),
        ("CURSOR_API_KEY", "In Progress (AI) → PR and review autofix", "Cursor dashboard → Integrations → API key."),
    ):
        checks.append(
            Check(name.lower(), "ok", f"{name} set")
            if _set(name)
            else Check(name.lower(), "warn", f"{name} missing: no {why}.", fix)
        )
    if _set("GITHUB_WEBHOOK_SECRET") or _set("JIRA_AUTOMATION_WEBHOOK_SECRET"):
        checks.append(Check("webhook_secret", "ok", "Webhook secret set (self-healing CI, deploy post-checks)"))
    else:
        checks.append(
            Check(
                "webhook_secret",
                "warn",
                "GITHUB_WEBHOOK_SECRET missing: failed GitHub Actions runs do not reach Bigas.",
                "Set GITHUB_WEBHOOK_SECRET to a random string, then add the repo webhook (scripts/connect_repo.py --webhook).",
            )
        )

    on = sorted(enabled_features())
    checks.append(
        Check(
            "features",
            "info",
            f"Optional areas on: {', '.join(on) if on else 'none'} (BIGAS_FEATURES={os.environ.get('BIGAS_FEATURES') or 'unset = all'})",
        )
    )
    return checks


def project_config_checks(projects: List[Project]) -> List[Check]:
    from bigas.resources.product.jira_automation.config import JiraAutomationConfig

    allowed = set(JiraAutomationConfig.from_env().allowed_projects)
    checks: List[Check] = []
    if not projects:
        checks.append(
            Check(
                "projects",
                "warn",
                "No projects. JIRA_PROJECT_KEYS is empty, so the board has only the default boards.",
                "python scripts/connect_repo.py --key ACME --repo owner/acme --apply",
            )
        )
        return checks
    for p in projects:
        if not p.repo:
            checks.append(
                Check(
                    "repo_mapped",
                    "fail",
                    f"{p.key} has no GitHub repo.",
                    f"python scripts/connect_repo.py --key {p.key} --repo owner/repo --apply",
                    p.key,
                )
            )
            continue
        detail = f"{p.repo} @ {p.base_branch}"
        if p.deploy_workflows:
            detail += f"; deploy {', '.join(p.deploy_workflows)}"
            if p.deploy_repo and p.deploy_repo != p.repo:
                detail += f" on {p.deploy_repo}"
        if p.release_branch:
            detail += f"; versioned releases ({p.release_branch}-x.y.z)"
        if p.staging_env:
            detail += "; staging rehearsal"
        checks.append(Check("repo_mapped", "ok", detail, project=p.key))
        if p.key not in allowed:
            checks.append(
                Check(
                    "ai_allowed",
                    "fail",
                    f"{p.key} is not in BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS, so dragging its cards to an AI column does nothing.",
                    f"Add {p.key} to BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS, or unset it to allow every board project.",
                    p.key,
                )
            )
        if not p.deploy_workflows and not p.staging_env:
            checks.append(
                Check(
                    "deploy_mapped",
                    "info",
                    f"{p.key} has no deploy workflow, so it has no Ship card. Fine if you deploy another way.",
                    f"python scripts/connect_repo.py --key {p.key} --repo {p.repo} --deploy deploy.yml --apply",
                    p.key,
                )
            )
    return checks


Fetcher = Callable[[str], tuple]


def github_fetcher(token: str) -> Fetcher:
    """GET a GitHub REST path → (status_code, json_or_None)."""
    import requests

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )

    def fetch(path: str) -> tuple:
        resp = session.get(f"https://api.github.com{path}", timeout=20)
        try:
            body = resp.json()
        except ValueError:
            body = None
        return resp.status_code, body

    return fetch


def _file(fetch: Fetcher, repo: str, path: str, ref: str) -> Optional[str]:
    status, body = fetch(f"/repos/{repo}/contents/{path}?ref={ref}")
    if status != 200 or not isinstance(body, dict):
        return None
    try:
        return base64.b64decode(body.get("content") or "").decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def github_checks(projects: List[Project], fetch: Fetcher, *, bigas_url: str = "") -> List[Check]:
    checks: List[Check] = []
    seen_repos: Dict[str, bool] = {}
    for p in projects:
        if not p.repo:
            continue
        connect = f"python scripts/connect_repo.py --key {p.key} --repo {p.repo} --branch {p.base_branch} --apply"
        status, body = fetch(f"/repos/{p.repo}")
        if status != 200:
            checks.append(
                Check(
                    "gh_repo",
                    "fail",
                    f"{p.repo}: not reachable with GITHUB_TOKEN (HTTP {status}).",
                    "Give the token access to this repo, or fix BIGAS_JIRA_PROJECT_REPO_MAP.",
                    p.key,
                )
            )
            continue
        perms = (body or {}).get("permissions") or {}
        if not perms.get("push", True):
            checks.append(Check("gh_repo", "fail", f"{p.repo}: token cannot push.", "Grant Contents: write.", p.key))
        status, _ = fetch(f"/repos/{p.repo}/branches/{p.base_branch}")
        if status != 200:
            checks.append(
                Check(
                    "gh_branch",
                    "fail",
                    f"{p.repo}: base branch {p.base_branch} not found.",
                    f"Set BIGAS_JIRA_REPO_BASE_BRANCH_MAP={p.repo}:<default branch>.",
                    p.key,
                )
            )
            continue

        caller = _file(fetch, p.repo, PR_REVIEW_PATH, p.base_branch)
        if caller is None:
            checks.append(
                Check("gh_pr_review", "fail", f"{p.repo}: no {PR_REVIEW_PATH} on {p.base_branch}.", connect, p.key)
            )
        elif "/.github/workflows/pr-review.yml@" not in caller:
            checks.append(
                Check(
                    "gh_pr_review",
                    "warn",
                    f"{p.repo}: {PR_REVIEW_PATH} does not call the Bigas reusable workflow.",
                    "Replace it with docs/pr-review.caller.yml.",
                    p.key,
                )
            )
        else:
            checks.append(Check("gh_pr_review", "ok", f"{p.repo}: PR review workflow present", project=p.key))

        if p.repo not in seen_repos:
            seen_repos[p.repo] = True
            checks.extend(_actions_settings_checks(p, fetch, connect, bigas_url))

        for wf in p.deploy_workflows:
            repo = p.deploy_repo or p.repo
            text = _file(fetch, repo, f".github/workflows/{wf}", "HEAD")
            if text is None:
                checks.append(
                    Check(
                        "gh_deploy",
                        "fail",
                        f"{repo}: deploy workflow {wf} not found.",
                        f"Add .github/workflows/{wf} with a workflow_dispatch trigger (template: docs/workflows/deploy.example.yml), or fix BIGAS_DEPLOY_WORKFLOW_MAP.",
                        p.key,
                    )
                )
            elif "workflow_dispatch" not in text:
                checks.append(
                    Check("gh_deploy", "fail", f"{repo}: {wf} has no workflow_dispatch trigger.", f"Add `on: workflow_dispatch:` to {wf}.", p.key)
                )
            else:
                checks.append(Check("gh_deploy", "ok", f"{repo}: {wf} can be dispatched", project=p.key))

        if p.release_branch:
            for wf in VERSIONED_WORKFLOWS:
                if _file(fetch, p.repo, f".github/workflows/{wf}", p.base_branch) is None:
                    checks.append(
                        Check(
                            "gh_versioned",
                            "fail",
                            f"{p.repo}: {wf} missing (needed for {p.release_branch}-x.y.z releases).",
                            connect.replace("--apply", f"--versioned {p.release_branch} --apply"),
                            p.key,
                        )
                    )
        if p.staging_env:
            for phase, wf in (p.staging_env.get("workflows") or {}).items():
                if _file(fetch, p.staging_env["repo"], f".github/workflows/{wf}", "HEAD") is None:
                    checks.append(
                        Check(
                            "gh_staging",
                            "fail",
                            f"{p.staging_env['repo']}: staging workflow {wf} ({phase}) missing.",
                            "See docs/agent-setup.md → Staging rehearsal.",
                            p.key,
                        )
                    )
    return checks


def _actions_settings_checks(p: Project, fetch: Fetcher, connect: str, bigas_url: str) -> List[Check]:
    checks: List[Check] = []
    status, body = fetch(f"/repos/{p.repo}/actions/variables/BIGAS_URL")
    if status == 200:
        value = (body or {}).get("value") or ""
        if bigas_url and value.rstrip("/") != bigas_url.rstrip("/"):
            checks.append(
                Check("gh_var", "warn", f"{p.repo}: BIGAS_URL is {value}, expected {bigas_url}.", connect, p.key)
            )
        else:
            checks.append(Check("gh_var", "ok", f"{p.repo}: Actions variable BIGAS_URL set", project=p.key))
    elif status == 404:
        checks.append(Check("gh_var", "fail", f"{p.repo}: Actions variable BIGAS_URL missing.", connect, p.key))
    else:
        checks.append(Check("gh_var", "warn", f"{p.repo}: cannot read Actions variables (HTTP {status}).", "Token needs Variables: read to verify.", p.key))

    status, _ = fetch(f"/repos/{p.repo}/actions/secrets/BIGAS_API_KEY")
    if status == 200:
        checks.append(Check("gh_secret", "ok", f"{p.repo}: Actions secret BIGAS_API_KEY set", project=p.key))
    elif status == 404:
        checks.append(Check("gh_secret", "fail", f"{p.repo}: Actions secret BIGAS_API_KEY missing.", connect, p.key))
    else:
        checks.append(Check("gh_secret", "warn", f"{p.repo}: cannot read Actions secrets (HTTP {status}).", "Token needs Secrets: read to verify.", p.key))

    status, hooks = fetch(f"/repos/{p.repo}/hooks")
    if status == 200 and isinstance(hooks, list):
        has = any(WEBHOOK_PATH in ((h.get("config") or {}).get("url") or "") for h in hooks)
        checks.append(
            Check("gh_webhook", "ok", f"{p.repo}: workflow_run webhook to Bigas", project=p.key)
            if has
            else Check(
                "gh_webhook",
                "warn",
                f"{p.repo}: no workflow_run webhook, so failed runs do not trigger self-healing CI.",
                connect.replace("--apply", "--webhook --apply"),
                p.key,
            )
        )
    return checks


def run_checks(*, offline: bool = False, fetch: Optional[Fetcher] = None) -> Dict[str, Any]:
    projects = load_projects()
    checks = instance_checks() + project_config_checks(projects)
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not offline:
        if fetch is None and _set("GITHUB_TOKEN"):
            fetch = github_fetcher(token)
        if fetch is not None:
            checks += github_checks(projects, fetch, bigas_url=public_url())
        else:
            checks.append(Check("github", "info", "GitHub checks skipped: no GITHUB_TOKEN."))
    # ok / warn / fail / info are the only statuses. Anything else (a new code,
    # or a check that was skipped without an explicit result) counts as a failure
    # so the summary and the exit code stay in agreement.
    counts = {s: 0 for s in ("ok", "warn", "fail", "info")}
    for check in checks:
        if check.status not in counts:
            check.message = f"{check.message} (unrecognized status {check.status!r} counts as a failure)"
            check.status = "fail"
        counts[check.status] += 1
    return {
        "ok": counts["fail"] == 0,
        "counts": counts,
        "projects": [asdict(p) for p in projects],
        "checks": [asdict(c) for c in checks],
    }
