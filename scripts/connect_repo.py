#!/usr/bin/env python3
"""Connect a GitHub repo to Bigas as a board project.

Without --apply this only prints the plan. With --apply it:

  1. updates the env file (project key, repo, base branch, deploy workflows,
     release branches, display name);
  2. sets the Actions variable BIGAS_URL and secret BIGAS_API_KEY on the repo
     (gh CLI; the secret value is read from BIGAS_ACCESS_KEYS and never printed);
  3. opens one pull request in the repo, on branch bigas-setup, adding
     .github/workflows/pr-review.yml (and cherry_pick.yml + rebase_release.yml
     with --versioned);
  4. with --webhook, adds a workflow_run webhook so failed runs reach Bigas.

Examples:

  python scripts/connect_repo.py --key ACME --repo acme/shop
  python scripts/connect_repo.py --key ACME --repo acme/shop --branch develop \\
      --name "Acme Shop" --deploy deploy.yml --versioned staging --webhook --apply
  python scripts/connect_repo.py --key ACME --repo acme/shop --skip-github --apply   # env only

Needs: gh (authenticated, admin on the repo) for the GitHub steps.
After --apply: restart Bigas locally, or sync secrets and run ./deploy.sh.
Then verify with: python scripts/bigas_doctor.py
Procedure: docs/agent-setup.md
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bigas.setup_repo import (  # noqa: E402
    RepoSpec,
    caller_workflow,
    first_access_key,
    plan_env_changes,
    read_env_file,
    update_env_text,
)

SETUP_BRANCH = "bigas-setup"
CALLER_TEMPLATE = ROOT / "docs" / "pr-review.caller.yml"
VERSIONED_FILES = ("cherry_pick.yml", "rebase_release.yml")
WEBHOOK_PATH = "/mcp/tools/github_workflow_run"


def gh(args: List[str], *, input_text: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], input=input_text, text=True, capture_output=True, check=check)


def gh_json(args: List[str], payload: Optional[dict] = None, check: bool = False):
    """Call ``gh api`` and return parsed JSON, or None on a non-zero exit.

    ``check`` defaults to False so a 4xx/5xx (branch missing, write rejected,
    pull request or webhook denied) comes back as None. Callers branch on that
    instead of an unhandled ``CalledProcessError``.
    """
    try:
        proc = gh(["api", *args, *(["--input", "-"] if payload is not None else [])],
                  input_text=json.dumps(payload) if payload is not None else None, check=check)
    except subprocess.CalledProcessError:
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError:
        return None


def _decoded(payload) -> str:
    """Text of a GitHub contents payload; "" when it cannot be decoded (then the file is rewritten)."""
    try:
        return base64.b64decode(payload.get("content") or "").decode("utf-8", "replace")
    except (ValueError, TypeError, AttributeError):
        return ""


def workflow_files(spec: RepoSpec, source: str) -> dict:
    files = {".github/workflows/pr-review.yml": caller_workflow(CALLER_TEMPLATE.read_text(encoding="utf-8"), source)}
    if spec.release_prefix:
        for name in VERSIONED_FILES:
            files[f".github/workflows/{name}"] = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    return files


def open_setup_pr(spec: RepoSpec, files: dict) -> Optional[str]:
    """Open the setup PR.

    Returns the PR URL, ``""`` when every workflow file is already on the base
    branch, or None when commits were pushed but the pull request was not opened.
    """
    repo = spec.repo
    base = gh_json([f"repos/{repo}/git/ref/heads/{spec.branch}"])
    if not base:
        raise SystemExit(f"✗ {repo}: branch {spec.branch} not found. Pass --branch <default branch>.")
    sha = base["object"]["sha"]
    created = gh_json(
        [f"repos/{repo}/git/refs", "-X", "POST"], {"ref": f"refs/heads/{SETUP_BRANCH}", "sha": sha}, check=False
    )
    if created is None:
        # bigas-setup is left from an earlier run. With an open PR, keep it and
        # update its files. Otherwise move it to the current base branch so the
        # new PR does not start from a stale commit.
        owner = repo.split("/")[0]
        open_prs = gh_json([f"repos/{repo}/pulls?head={owner}:{SETUP_BRANCH}&state=open"], check=False)
        if isinstance(open_prs, list) and open_prs and isinstance(open_prs[0], dict):
            pr = open_prs[0]
            if ((pr.get("base") or {}).get("ref") or spec.branch) != spec.branch:
                retargeted = gh_json(
                    [f"repos/{repo}/pulls/{pr.get('number')}", "-X", "PATCH"], {"base": spec.branch}, check=False
                )
                if retargeted is None:
                    raise SystemExit(
                        f"✗ {repo}: the open {SETUP_BRANCH} PR targets {pr['base']['ref']} and could not be "
                        f"moved to {spec.branch}. Close it and rerun."
                    )
                print(f"  ~ open {SETUP_BRANCH} PR now targets {spec.branch}")
        else:
            moved = gh_json(
                [f"repos/{repo}/git/refs/heads/{SETUP_BRANCH}", "-X", "PATCH"],
                {"sha": sha, "force": True},
                check=False,
            )
            if moved is None:
                raise SystemExit(
                    f"✗ {repo}: branch {SETUP_BRANCH} exists and could not be moved to {spec.branch}. "
                    f"Delete it on GitHub and rerun."
                )
            print(f"  ~ {SETUP_BRANCH} reset to the tip of {spec.branch}")

    changed = 0
    for path, content in files.items():
        on_base = gh_json([f"repos/{repo}/contents/{path}?ref={spec.branch}"], check=False)
        if on_base and _decoded(on_base) == content:
            print(f"  = {path} already up to date on {spec.branch}")
            continue
        existing = gh_json([f"repos/{repo}/contents/{path}?ref={SETUP_BRANCH}"], check=False)
        body = {
            "message": f"Bigas: add {path.rsplit('/', 1)[-1]}",
            "content": base64.b64encode(content.encode()).decode(),
            "branch": SETUP_BRANCH,
        }
        if existing and existing.get("sha"):
            body["sha"] = existing["sha"]
        if gh_json([f"repos/{repo}/contents/{path}", "-X", "PUT"], body) is None:
            raise SystemExit(f"✗ {repo}: could not write {path}")
        print(f"  + {path}")
        changed += 1
    if not changed:
        return ""

    owner = repo.split("/")[0]
    open_prs = gh_json([f"repos/{repo}/pulls?head={owner}:{SETUP_BRANCH}&state=open"], check=False) or []
    if isinstance(open_prs, list) and open_prs:
        first = open_prs[0] if isinstance(open_prs[0], dict) else {}
        return first.get("html_url") or None
    lines = [
        "Connects this repo to Bigas.",
        "",
        "- `pr-review.yml` calls the Bigas review → autofix → merge workflow on every PR.",
    ]
    if spec.release_prefix:
        lines.append(f"- `cherry_pick.yml` and `rebase_release.yml` support `{spec.release_prefix}-x.y.z` release branches.")
    lines += ["", "Needs Actions variable `BIGAS_URL` and secret `BIGAS_API_KEY` (set by `scripts/connect_repo.py`)."]
    pr = gh_json([f"repos/{repo}/pulls", "-X", "POST"], {
        "title": "Connect this repo to Bigas",
        "head": SETUP_BRANCH,
        "base": spec.branch,
        "body": "\n".join(lines),
    })
    if not isinstance(pr, dict):
        return None
    return pr.get("html_url") or None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--key", required=True, help="board project key, e.g. ACME (cards become ACME-1, ACME-2)")
    p.add_argument("--repo", required=True, help="GitHub owner/repo")
    p.add_argument("--branch", default="main", help="branch Cursor starts from and PRs target (default main)")
    p.add_argument("--name", default="", help="display name, e.g. 'Acme Shop'")
    p.add_argument("--deploy", default="", help="deploy workflow file(s) DevOps dispatches, comma-separated")
    p.add_argument("--deploy-repo", default="", help="repo that holds the deploy workflow, when not --repo")
    p.add_argument("--versioned", default="", metavar="PREFIX",
                   help="use PREFIX-x.y.z release branches (e.g. staging); adds cherry_pick/rebase_release workflows")
    p.add_argument("--bigas-url", default="", help="public Bigas URL (default: BIGAS_PUBLIC_URL from the env file)")
    p.add_argument("--workflow-source", default="",
                   help="reusable workflow repo@ref (default: BIGAS_WORKFLOW_SOURCE or mckort/bigas@main)")
    p.add_argument("--env-file", default=str(ROOT / ".env"))
    p.add_argument("--webhook", action="store_true", help="add a workflow_run webhook for self-healing CI")
    p.add_argument("--skip-env", action="store_true", help="do not touch the env file")
    p.add_argument("--skip-github", action="store_true", help="do not touch GitHub")
    p.add_argument("--apply", action="store_true", help="make the changes (default: print the plan)")
    args = p.parse_args()

    spec = RepoSpec(
        key=args.key.strip().upper(),
        repo=args.repo.strip().strip("/"),
        branch=args.branch.strip(),
        name=args.name.strip(),
        deploy_workflows=[w.strip() for w in args.deploy.split(",") if w.strip()],
        deploy_repo=args.deploy_repo.strip(),
        release_prefix=args.versioned.strip(),
    )
    errors = spec.validate()
    if errors:
        for e in errors:
            print(f"✗ {e}")
        return 2

    env_path = Path(args.env_file)
    env = read_env_file(env_path)
    merged = {**env, **{k: v for k, v in os.environ.items() if k not in env}}
    bigas_url = (args.bigas_url or merged.get("BIGAS_PUBLIC_URL") or "").rstrip("/")
    source = args.workflow_source or merged.get("BIGAS_WORKFLOW_SOURCE") or "mckort/bigas@main"
    access_key = first_access_key(merged)
    webhook_secret = merged.get("GITHUB_WEBHOOK_SECRET") or merged.get("JIRA_AUTOMATION_WEBHOOK_SECRET") or ""

    changes = {} if args.skip_env else plan_env_changes(env, spec)
    files = {} if args.skip_github else workflow_files(spec, source)

    print(f"Connect {spec.key} → {spec.repo} (base {spec.branch})\n")
    if not args.skip_env:
        print(f"1. Env file {env_path}:")
        for name, value in changes.items():
            print(f"   {name}={value}")
        if not changes:
            print("   (already configured)")
    if not args.skip_github:
        print(f"2. Actions on {spec.repo}: variable BIGAS_URL={bigas_url or '<missing>'}, secret BIGAS_API_KEY={'<from BIGAS_ACCESS_KEYS>' if access_key else '<missing>'}")
        print(f"3. Pull request on {spec.repo} ({SETUP_BRANCH} → {spec.branch}): {', '.join(files)} (reusable workflow {source})")
        if args.webhook:
            print(f"4. Webhook {bigas_url or '<missing>'}{WEBHOOK_PATH} on workflow_run")
    if spec.deploy_workflows:
        deploy_repo = spec.deploy_repo or spec.repo
        print(f"\nDeploy workflows must exist on {deploy_repo} with a workflow_dispatch trigger: {', '.join(spec.deploy_workflows)}")
        print("Template: docs/workflows/deploy.example.yml")

    if not args.apply:
        print("\nDry run. Add --apply to make these changes.")
        return 0

    problems = []
    if not args.skip_github:
        if not shutil.which("gh"):
            problems.append("gh CLI not found (https://cli.github.com), or rerun with --skip-github")
        if not bigas_url:
            problems.append("no Bigas URL: set BIGAS_PUBLIC_URL in the env file or pass --bigas-url")
        if not access_key:
            problems.append("BIGAS_ACCESS_KEYS is empty in the env file")
        if args.webhook and not webhook_secret:
            problems.append("--webhook needs GITHUB_WEBHOOK_SECRET in the env file")
    if problems:
        for msg in problems:
            print(f"✗ {msg}")
        return 2

    if changes:
        text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
        env_path.write_text(update_env_text(text, changes), encoding="utf-8")
        print(f"\n✓ Updated {env_path}")

    exit_code = 0
    url = ""
    if not args.skip_github:
        for label, cmd, stdin in (
            ("variable BIGAS_URL", ["variable", "set", "BIGAS_URL", "--repo", spec.repo, "--body", bigas_url], None),
            ("secret BIGAS_API_KEY", ["secret", "set", "BIGAS_API_KEY", "--repo", spec.repo], access_key),
        ):
            proc = gh(cmd, input_text=stdin, check=False)
            if proc.returncode != 0:
                reason = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["unknown error"]
                print(f"✗ {spec.repo}: could not set {label}: {reason[0]}")
                print("  The gh account needs admin on the repo (gh auth status). Nothing else was changed on GitHub.")
                return 1
        print(f"✓ Set BIGAS_URL and BIGAS_API_KEY on {spec.repo}")
        url = open_setup_pr(spec, files)
        if url is None:
            print(f"✗ {spec.repo}: changes are on {SETUP_BRANCH}, but the pull request was not created.")
            exit_code = 1
        elif url:
            print(f"✓ Pull request: {url}")
        else:
            print("✓ Workflows already on the base branch")
        if args.webhook:
            hooks = gh_json([f"repos/{spec.repo}/hooks"], check=False)
            if isinstance(hooks, list) and any(
                WEBHOOK_PATH in ((h.get("config") or {}).get("url") or "") for h in hooks if isinstance(h, dict)
            ):
                print("✓ Webhook already present")
            else:
                created = gh_json([f"repos/{spec.repo}/hooks", "-X", "POST"], {
                    "name": "web",
                    "active": True,
                    "events": ["workflow_run"],
                    "config": {"url": f"{bigas_url}{WEBHOOK_PATH}", "content_type": "json", "secret": webhook_secret},
                })
                if created is None:
                    print(f"✗ {spec.repo}: could not add the workflow_run webhook.")
                    exit_code = 1
                else:
                    print("✓ Added workflow_run webhook")

    print("\nNext:")
    if not args.skip_github and url is None:
        print(f"  - Open a pull request from {SETUP_BRANCH} into {spec.branch} (the branch was pushed).")
    else:
        print("  - Merge the pull request above.")
    if changes:
        print("  - Local: restart Bigas. Cloud Run: if SECRET_MANAGER=true run `python scripts/sync_env_to_secret_manager.py`, then `./deploy.sh`.")
    print("  - Verify: python scripts/bigas_doctor.py")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
