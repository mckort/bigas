"""Shared helpers for staging/main branch routing and fix versions (BIG-42)."""
from __future__ import annotations

import os
import re
from typing import Dict, Iterable, Optional, Sequence

from bigas.tickets.semver import SemverError, normalize_version_name

_SEMVER_RE = re.compile(r"^v?(?P<ver>\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._+-]+)?)$", re.I)
_BRANCH_VERSION_RE = re.compile(r"-(\d+\.\d+\.\d+)$")
_HOTFIX_LABELS = frozenset({"hotfix", "urgent-fix", "production-fix"})
_PRODUCTION_BRANCH_NAMES = frozenset({"main", "master"})


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


def version_from_feature_branch(branch: str) -> Optional[str]:
    """Return X.Y.Z if branch looks like `staging-0.2.3`."""
    match = _BRANCH_VERSION_RE.search((branch or "").strip())
    if not match:
        return None
    try:
        return normalize_version_name(match.group(1))
    except SemverError:
        return None


def feature_branch_prefix(branch: str) -> str:
    """Strip a trailing `-{semver}` from a feature branch name."""
    text = (branch or "").strip()
    if version_from_feature_branch(text):
        return _BRANCH_VERSION_RE.sub("", text)
    return text


def versioned_feature_branch(prefix: str, version: str) -> str:
    """`staging` + `0.2.3` → `staging-0.2.3`. Already-versioned names are unchanged."""
    base = (prefix or "").strip()
    if not base:
        return ""
    if version_from_feature_branch(base):
        return base
    try:
        ver = normalize_version_name(version)
    except SemverError:
        return base
    return f"{base}-{ver}"


def uses_versioned_feature_branches(mapped_branch: str, production: str = "main") -> bool:
    """True when the project mapping is a staging-style prefix, not production."""
    mapped = (mapped_branch or "").strip()
    prod = (production or "main").strip()
    if not mapped or mapped == prod:
        return False
    return mapped.lower() not in _PRODUCTION_BRANCH_NAMES


def is_versioned_feature_head(head: str, prefix: str) -> bool:
    """True when `head` is `{prefix}-{semver}` (e.g. staging-0.2.3)."""
    mapped = feature_branch_prefix(prefix)
    if not mapped or not head:
        return False
    if (head or "").strip() == mapped:
        return False
    expected_prefix = f"{mapped}-"
    if not (head or "").startswith(expected_prefix):
        return False
    return version_from_feature_branch(head) is not None


def resolve_automerge_branch(
    *,
    project_key: str,
    repo: str,
    labels: Optional[Iterable[str]] = None,
    project_branch_map: Optional[Dict[str, str]] = None,
    repo_base_branches: Optional[Dict[str, str]] = None,
    default_base_branch: str = "main",
    fix_version: Optional[str] = None,
) -> str:
    """
    Resolve the PR / Cursor base branch for a project issue.

    Priority:
    1. hotfix label → production branch (repo map or DEFAULT/main)
    2. PROJECT_BRANCH_MAPPING for project key
    3. PROJECT_BRANCH_MAPPING DEFAULT entry
    4. BIGAS_JIRA_REPO_BASE_BRANCH_MAP for repo
    5. BIGAS_JIRA_DEFAULT_BASE_BRANCH / main

    When the mapped branch is a staging prefix and ``fix_version`` is a semver
    (e.g. `0.2.3`), the result is versioned (`staging-0.2.3`).
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
    mapped = ""
    if proj and proj in branch_map:
        mapped = branch_map[proj]
    elif "DEFAULT" in branch_map:
        mapped = branch_map["DEFAULT"]
    elif repo_base_branches and repo_key in repo_base_branches:
        mapped = repo_base_branches[repo_key]
    else:
        mapped = prod_branch

    if uses_versioned_feature_branches(mapped, prod_branch) and (fix_version or "").strip():
        return versioned_feature_branch(mapped, fix_version)
    return mapped
