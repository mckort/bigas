"""Cherry-pick a squash merge commit onto main via GitHub Git API (BIG-42)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_GITHUB_API_VERSION = "2022-11-28"


class CherryPickError(RuntimeError):
    pass


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }


def _request(
    method: str,
    url: str,
    token: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    resp = requests.request(
        method,
        url,
        headers=_headers(token),
        json=json,
        params=params,
        timeout=60,
    )
    if resp.status_code >= 400:
        detail = (resp.text or "")[:400]
        raise CherryPickError(f"GitHub API {resp.status_code}: {detail}")
    if resp.status_code == 204 or not (resp.text or "").strip():
        return {}
    return resp.json()


def find_merged_pr_for_issue(
    *,
    token: str,
    owner: str,
    repo: str,
    issue_key: str,
    base_branch: str = "staging",
) -> Dict[str, Any]:
    """Find the newest merged PR for issue_key targeting base_branch."""
    key = (issue_key or "").strip().upper()
    base = (base_branch or "staging").strip()
    if not key:
        raise CherryPickError("issue_key is required")

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    pulls = _request(
        "GET",
        url,
        token,
        params={
            "state": "closed",
            "base": base,
            "sort": "updated",
            "direction": "desc",
            "per_page": 30,
        },
    )
    if not isinstance(pulls, list):
        raise CherryPickError("Unexpected GitHub pulls response")

    for pr in pulls:
        if not isinstance(pr, dict) or not pr.get("merged_at"):
            continue
        title = (pr.get("title") or "").upper()
        body = (pr.get("body") or "").upper()
        head_ref = ((pr.get("head") or {}).get("ref") or "").upper()
        if key in title or key in body or key.replace("-", "") in head_ref.replace("-", ""):
            return pr

    raise CherryPickError(
        f"No merged PR found for {key} on base `{base}` in {owner}/{repo}"
    )


def _tree_modifications(
    token: str,
    owner: str,
    repo: str,
    *,
    base_sha: str,
    source_sha: str,
) -> List[Dict[str, Any]]:
    compare = _request(
        "GET",
        f"https://api.github.com/repos/{owner}/{repo}/compare/{base_sha}...{source_sha}",
        token,
    )
    files = compare.get("files") or []
    mods: List[Dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = (item.get("filename") or "").strip()
        status = (item.get("status") or "").strip()
        if not path:
            continue
        if status == "removed":
            mods.append({"path": path, "sha": None})
            continue
        blob_sha = (item.get("sha") or "").strip()
        if blob_sha:
            mods.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})
    if not mods:
        raise CherryPickError("No file changes found to cherry-pick")
    return mods


def cherry_pick_commit_to_branch(
    *,
    token: str,
    owner: str,
    repo: str,
    merge_commit_sha: str,
    target_branch: str,
    new_branch: str,
) -> Tuple[str, str]:
    """
    Cherry-pick a squash merge onto target_branch by creating new_branch.

    Returns (new_branch, new_commit_sha).
    """
    merge_sha = (merge_commit_sha or "").strip()
    target = (target_branch or "main").strip()
    branch = (new_branch or "").strip()
    if not merge_sha or not branch:
        raise CherryPickError("merge_commit_sha and new_branch are required")

    merge_commit = _request(
        "GET",
        f"https://api.github.com/repos/{owner}/{repo}/commits/{merge_sha}",
        token,
    )
    parents = merge_commit.get("parents") or []
    if not parents:
        raise CherryPickError(f"Commit {merge_sha} has no parent")
    source_parent = (parents[0].get("sha") or "").strip()

    target_ref = _request(
        "GET",
        f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{target}",
        token,
    )
    target_sha = ((target_ref.get("object") or {}).get("sha") or "").strip()
    if not target_sha:
        raise CherryPickError(f"Could not resolve {target} ref")

    target_commit = _request(
        "GET",
        f"https://api.github.com/repos/{owner}/{repo}/commits/{target_sha}",
        token,
    )
    base_tree = ((target_commit.get("tree") or {}).get("sha") or "").strip()

    mods = _tree_modifications(
        token,
        owner,
        repo,
        base_sha=source_parent,
        source_sha=merge_sha,
    )
    new_tree = _request(
        "POST",
        f"https://api.github.com/repos/{owner}/{repo}/git/trees",
        token,
        json={"base_tree": base_tree, "tree": mods},
    )
    tree_sha = (new_tree.get("sha") or "").strip()
    if not tree_sha:
        raise CherryPickError("Failed to create git tree")

    new_commit = _request(
        "POST",
        f"https://api.github.com/repos/{owner}/{repo}/git/commits",
        token,
        json={
            "message": merge_commit.get("commit", {}).get("message")
            or f"Cherry-pick {merge_sha[:7]} onto {target}",
            "tree": tree_sha,
            "parents": [target_sha],
        },
    )
    commit_sha = (new_commit.get("sha") or "").strip()
    if not commit_sha:
        raise CherryPickError("Failed to create commit")

    try:
        _request(
            "POST",
            f"https://api.github.com/repos/{owner}/{repo}/git/refs",
            token,
            json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )
    except CherryPickError as exc:
        if "422" not in str(exc):
            raise
        _request(
            "PATCH",
            f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch}",
            token,
            json={"sha": commit_sha, "force": True},
        )

    return branch, commit_sha


def open_pull_request(
    *,
    token: str,
    owner: str,
    repo: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> str:
    data = _request(
        "POST",
        f"https://api.github.com/repos/{owner}/{repo}/pulls",
        token,
        json={
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
            "draft": False,
        },
    )
    url = (data.get("html_url") or "").strip()
    if not url:
        raise CherryPickError("GitHub PR create returned no html_url")
    return url
