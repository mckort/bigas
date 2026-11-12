"""Plan the env changes that connect one project to one GitHub repo.

Pure functions, used by ``scripts/connect_repo.py`` and its tests. The env
maps keep their historical ``JIRA_`` names; they drive the built-in board too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass
class RepoSpec:
    key: str
    repo: str
    branch: str = "main"
    name: str = ""
    deploy_workflows: List[str] = field(default_factory=list)
    deploy_repo: str = ""
    release_prefix: str = ""  # "staging" → staging-0.2.3 release branches

    def validate(self) -> List[str]:
        errors = []
        if not _KEY_RE.match(self.key):
            errors.append(f"--key {self.key!r}: use uppercase letters and digits, e.g. ACME or GPW-PROD")
        if not _REPO_RE.match(self.repo):
            errors.append(f"--repo {self.repo!r}: use owner/repo")
        if self.deploy_repo and not _REPO_RE.match(self.deploy_repo):
            errors.append(f"--deploy-repo {self.deploy_repo!r}: use owner/repo")
        for wf in self.deploy_workflows:
            if not wf.endswith((".yml", ".yaml")):
                errors.append(f"--deploy {wf!r}: give the workflow file name, e.g. deploy.yml")
        if self.release_prefix and self.release_prefix == self.branch:
            errors.append("--versioned must differ from --branch (it is the prefix of staging-x.y.z branches)")
        return errors


def _csv_items(raw: str) -> List[Tuple[str, str]]:
    items = []
    for part in (raw or "").split(","):
        part = part.strip()
        if ":" in part:
            k, v = part.split(":", 1)
            items.append((k.strip(), v.strip()))
    return items


def _csv_upsert(raw: str, key: str, value: str, *, case_insensitive_key: bool = True) -> str:
    items = _csv_items(raw)
    norm = (lambda s: s.upper()) if case_insensitive_key else (lambda s: s)
    out, found = [], False
    for k, v in items:
        if norm(k) == norm(key):
            out.append((key, value))
            found = True
        else:
            out.append((k, v))
    if not found:
        out.append((key, value))
    return ",".join(f"{k}:{v}" for k, v in out)


def _pipe_upsert(raw: str, key: str, workflows: List[str]) -> str:
    """BIGAS_DEPLOY_WORKFLOW_MAP: ``KEY:a.yml,b.yml|KEY2:deploy.yml``."""
    parts = [p.strip() for p in (raw or "").split("|") if p.strip() and ":" in p]
    out, found = [], False
    for part in parts:
        k, v = part.split(":", 1)
        if k.strip().upper() == key.upper():
            out.append(f"{key}:{','.join(workflows)}")
            found = True
        else:
            out.append(f"{k.strip()}:{v.strip()}")
    if not found:
        out.append(f"{key}:{','.join(workflows)}")
    return "|".join(out)


def _list_add(raw: str, key: str) -> str:
    keys = [k.strip().upper() for k in (raw or "").split(",") if k.strip()]
    if key not in keys:
        keys.append(key)
    return ",".join(keys)


def plan_env_changes(env: Dict[str, str], spec: RepoSpec) -> Dict[str, str]:
    """Return ``{NAME: new value}`` for every env var that has to change."""
    get = lambda name: (env.get(name) or "").strip()  # noqa: E731
    want: Dict[str, str] = {}

    # The server reads JIRA_PROJECT_KEYS first, then JIRA_PROJECT_KEY. Update
    # whichever is in use; deploy.sh passes both to Cloud Run.
    keys_var = "JIRA_PROJECT_KEYS" if get("JIRA_PROJECT_KEYS") else "JIRA_PROJECT_KEY"
    want[keys_var] = _list_add(get(keys_var), spec.key)
    if get("BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS"):
        want["BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS"] = _list_add(
            get("BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS"), spec.key
        )
    want["BIGAS_JIRA_PROJECT_REPO_MAP"] = _csv_upsert(get("BIGAS_JIRA_PROJECT_REPO_MAP"), spec.key, spec.repo)
    want["BIGAS_JIRA_REPO_BASE_BRANCH_MAP"] = _csv_upsert(
        get("BIGAS_JIRA_REPO_BASE_BRANCH_MAP"), spec.repo, spec.branch, case_insensitive_key=False
    )
    if spec.name:
        want["BIGAS_PROJECT_NAME_MAP"] = _csv_upsert(get("BIGAS_PROJECT_NAME_MAP"), spec.key, spec.name)
    if spec.deploy_workflows:
        want["BIGAS_DEPLOY_WORKFLOW_MAP"] = _pipe_upsert(
            get("BIGAS_DEPLOY_WORKFLOW_MAP"), spec.key, spec.deploy_workflows
        )
    if spec.deploy_repo and spec.deploy_repo != spec.repo:
        want["BIGAS_DEPLOY_REPO_MAP"] = _csv_upsert(get("BIGAS_DEPLOY_REPO_MAP"), spec.key, spec.deploy_repo)
    if spec.release_prefix:
        name = "PROJECT_BRANCH_MAPPING" if get("PROJECT_BRANCH_MAPPING") or not get("BIGAS_PROJECT_BRANCH_MAPPING") else "BIGAS_PROJECT_BRANCH_MAPPING"
        current = get(name) or "DEFAULT:main"
        want[name] = _csv_upsert(current, spec.key, spec.release_prefix)

    return {name: value for name, value in want.items() if value != get(name)}


def update_env_text(text: str, changes: Dict[str, str]) -> str:
    """Replace ``NAME=...`` lines (uncommented) or append them."""
    lines = text.splitlines()
    done = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if name.startswith("export "):
            name = name[len("export ") :].strip()
        if name in changes and name not in done:
            lines[i] = f"{name}={changes[name]}"
            done.add(name)
    missing = [n for n in changes if n not in done]
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Added by scripts/connect_repo.py")
        lines.extend(f"{n}={changes[n]}" for n in missing)
    return "\n".join(lines) + "\n"


def caller_workflow(template: str, source: str) -> str:
    """docs/pr-review.caller.yml with ``uses:`` pointed at ``owner/repo@ref``."""
    repo, _, ref = source.partition("@")
    ref = ref or "main"
    return re.sub(
        r"uses:\s*[\w.-]+/[\w.-]+/\.github/workflows/pr-review\.yml@\S+",
        f"uses: {repo}/.github/workflows/pr-review.yml@{ref}",
        template,
    )


def read_env_file(path: Path) -> Dict[str, str]:
    from dotenv import dotenv_values

    if not path.is_file():
        return {}
    return {k: v or "" for k, v in dotenv_values(path).items()}


def first_access_key(env: Dict[str, str]) -> Optional[str]:
    for part in (env.get("BIGAS_ACCESS_KEYS") or "").split(","):
        if part.strip():
            return part.strip()
    return None


# Same text as docs/pr-review.caller.yml (docs/ is not in the Docker image, and
# the app needs it for "Go to production"). tests/test_go_live.py keeps them equal.
PR_REVIEW_CALLER_TEMPLATE = """# Thin caller for product repos (VFA, WAYW, GPWW, FYDA, MYL, REM, FRI / frimaninvestments, AURA / auralearn, …).
# Copy this file to `.github/workflows/pr-review.yml` in the product repo.
# The implementation lives in mckort/bigas so every project follows the same
# review → autofix → merge → Final approval flow.
name: Bigas PR review

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, closed]

permissions:
  contents: write
  pull-requests: write

jobs:
  bigas:
    uses: mckort/bigas/.github/workflows/pr-review.yml@main
    with:
      bigas_url: ${{ vars.BIGAS_URL }}
      auto_fix: ${{ vars.BIGAS_AUTO_FIX || 'true' }}
      # Optional. Seconds to wait before every review (including one commit).
      # Overrides repository variable BIGAS_REVIEW_SETTLE_SECONDS. Default 120.
      # settle_seconds: ${{ vars.BIGAS_REVIEW_SETTLE_SECONDS || '120' }}
    secrets:
      BIGAS_API_KEY: ${{ secrets.BIGAS_API_KEY }}
      GH_PAT_FOR_BIGAS: ${{ secrets.GH_PAT_FOR_BIGAS }}
"""
