"""Create GitHub releases from semver fix versions (BIG-42)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from bigas.resources.product.release_workflow import normalize_semver_tag

logger = logging.getLogger(__name__)

_GITHUB_API_VERSION = "2022-11-28"


class GitHubReleaseError(RuntimeError):
    pass


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }


def create_github_release(
    *,
    token: str,
    owner: str,
    repo: str,
    fix_version: str,
    title: str,
    body: str,
    target_commitish: Optional[str] = None,
    draft: bool = False,
    prerelease: bool = False,
) -> Dict[str, Any]:
    """Create (or return existing) GitHub release tagged vX.Y.Z."""
    tag_name = normalize_semver_tag(fix_version)
    url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    payload: Dict[str, Any] = {
        "tag_name": tag_name,
        "name": title or f"Release {fix_version}",
        "body": body or "",
        "draft": bool(draft),
        "prerelease": bool(prerelease),
    }
    if target_commitish:
        payload["target_commitish"] = target_commitish.strip()

    resp = requests.post(url, headers=_headers(token), json=payload, timeout=60)
    if resp.status_code == 422:
        # Tag may already exist — fetch latest matching release.
        existing = get_release_by_tag(
            token=token,
            owner=owner,
            repo=repo,
            tag_name=tag_name,
        )
        if existing:
            return existing
    if resp.status_code >= 400:
        raise GitHubReleaseError(
            f"GitHub release create failed ({resp.status_code}): {(resp.text or '')[:400]}"
        )
    data = resp.json() if resp.text else {}
    return data if isinstance(data, dict) else {}


def get_release_by_tag(
    *,
    token: str,
    owner: str,
    repo: str,
    tag_name: str,
) -> Optional[Dict[str, Any]]:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag_name}"
    resp = requests.get(url, headers=_headers(token), timeout=30)
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise GitHubReleaseError(
            f"GitHub release lookup failed ({resp.status_code}): {(resp.text or '')[:300]}"
        )
    data = resp.json() if resp.text else {}
    return data if isinstance(data, dict) else None
