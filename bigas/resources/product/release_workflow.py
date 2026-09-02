"""Shared helpers for staging/main branch routing and fix versions (BIG-42)."""
from __future__ import annotations

import os
import re
from typing import Dict, Iterable, Optional, Sequence

_SEMVER_RE = re.compile(r"^v?(?P<ver>\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._+-]+)?)$", re.I)
_HOTFIX_LABELS = frozenset({"hotfix", "urgent-fix", "production-fix"})


def _parse_key_value_map(raw: Optional[str]) -> Dict[str, str]:
    """Parse `KEY:value,KEY2:value2` (comma-separated, last colon splits value)."""
    out: Dict[str, str] = {}
    if not (raw or "").strip():
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, value = part.rsplit(":", 1)
        k = key.strip().upper()
        v = value.strip()
        if k and v:
            out[k] = v
    return out


def parse_project_branch_mapping(raw: Optional[str]) -> Dict[str, str]:
    """
    Parse PROJECT_BRANCH_MAPPING, e.g. `VFA:staging,DEFAULT:main`.

    Keys are uppercased project keys; values are git branch names.
    """
    return _parse_key_value_map(raw)


def parse_project_active_fix_version(raw: Optional[str]) -> Dict[str, str]:
    """
    Parse BIGAS_PROJECT_ACTIVE_FIX_VERSION for internal boards without Jira.

    Example: `VFA:0.9.0,BIG:1.0.0`
    """
    return _parse_key_value_map(raw)


def labels_include_hotfix(labels: Optional[Sequence[str]]) -> bool:
    for label in labels or ():
        normalized = str(label or "").strip().lower().replace(" ", "-")
        if normalized in _HOTFIX_LABELS:
            return True
    return False


def normalize_semver_tag(version: str) -> str:
    """Return a `vX.Y.Z` tag name from a fix version string."""
    text = (version or "").strip()
    if not text:
        raise ValueError("version is required")
    match = _SEMVER_RE.match(text)
    if match:
        return f"v{match.group('ver')}"
    if re.match(r"^\d+\.\d+\.\d+", text):
        return f"v{text.lstrip('vV')}"
    raise ValueError(f"Invalid semver fix version: {version!r}")


def project_branch_mapping_from_env() -> Dict[str, str]:
    raw = os.environ.get("PROJECT_BRANCH_MAPPING")
    if raw is None or not str(raw).strip():
        raw = os.environ.get("BIGAS_PROJECT_BRANCH_MAPPING")
    parsed = parse_project_branch_mapping(raw)
    if parsed:
        return parsed
    return {"DEFAULT": "main"}


def active_fix_version_from_env(project_key: str) -> Optional[str]:
    mapping = parse_project_active_fix_version(
        os.environ.get("BIGAS_PROJECT_ACTIVE_FIX_VERSION")
    )
    key = (project_key or "").strip().upper()
    if key and key in mapping:
        return mapping[key]
    return mapping.get("DEFAULT")


def resolve_production_branch(
    *,
    project_key: str,
    repo: str,
    repo_base_branches: Optional[Dict[str, str]] = None,
    default_base_branch: str = "main",
) -> str:
    """Production/release branch (main), ignoring staging automerge mapping."""
    del project_key
    repo_key = (repo or "").strip()
    if repo_base_branches and repo_key in repo_base_branches:
        return repo_base_branches[repo_key]
    return default_base_branch or "main"


def resolve_automerge_branch(
    *,
    project_key: str,
    repo: str,
    labels: Optional[Iterable[str]] = None,
    project_branch_map: Optional[Dict[str, str]] = None,
    repo_base_branches: Optional[Dict[str, str]] = None,
    default_base_branch: str = "main",
) -> str:
    """
    Resolve the PR / Cursor base branch for a project issue.

    Priority:
    1. hotfix label → production branch (repo map or DEFAULT/main)
    2. PROJECT_BRANCH_MAPPING for project key
    3. PROJECT_BRANCH_MAPPING DEFAULT entry
    4. BIGAS_JIRA_REPO_BASE_BRANCH_MAP for repo
    5. BIGAS_JIRA_DEFAULT_BASE_BRANCH / main
    """
    labels_list = list(labels or ())
    prod_branch = default_base_branch or "main"
    repo_key = (repo or "").strip()
    if repo_base_branches and repo_key in repo_base_branches:
        prod_branch = repo_base_branches[repo_key]

    if labels_include_hotfix(labels_list):
        return prod_branch

    branch_map = project_branch_map if project_branch_map is not None else project_branch_mapping_from_env()
    proj = (project_key or "").strip().upper()
    if proj and proj in branch_map:
        return branch_map[proj]
    if "DEFAULT" in branch_map:
        return branch_map["DEFAULT"]

    if repo_base_branches and repo_key in repo_base_branches:
        return repo_base_branches[repo_key]
    return prod_branch
