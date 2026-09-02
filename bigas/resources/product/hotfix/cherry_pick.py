"""GitHub helpers for hotfix cherry-pick workflow dispatch (BIG-42)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

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
    page = 1
    per_page = 100
    while True:
        pulls = _request(
            "GET",
            url,
            token,
            params={
                "state": "closed",
                "base": base,
                "sort": "updated",
                "direction": "desc",
                "per_page": per_page,
                "page": page,
            },
        )
        if not isinstance(pulls, list):
            raise CherryPickError("Unexpected GitHub pulls response")
        if not pulls:
            break

        for pr in pulls:
            if not isinstance(pr, dict) or not pr.get("merged_at"):
                continue
            title = (pr.get("title") or "").upper()
            body = (pr.get("body") or "").upper()
            head_ref = ((pr.get("head") or {}).get("ref") or "").upper()
            if key in title or key in body or key.replace("-", "") in head_ref.replace("-", ""):
                return pr

        if len(pulls) < per_page:
            break
        page += 1

    raise CherryPickError(
        f"No merged PR found for {key} on base `{base}` in {owner}/{repo}"
    )
