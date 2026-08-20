"""GitHub Actions API client for workflow dispatch and status."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class GitHubActionsError(RuntimeError):
    pass


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_GHA_PREFIX_RE = re.compile(
    r"^(?:[^\t\n]+\t[^\t\n]+\t)?\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?"
)
_ERROR_HINTS = (
    "##[error]",
    "error during build",
    "build failed",
    "tsconfig not found",
    "process completed with exit code",
    "error:",
    "fatal:",
    "✗",
)


def _github_error_detail(resp: requests.Response, *, limit: int = 300) -> str:
    try:
        data = resp.json() if resp.text else None
    except Exception:
        data = None
    if isinstance(data, dict):
        message = (data.get("message") or "").strip()
        if message:
            return message[:limit]
    text = (resp.text or "").strip()
    return text[:limit] if text else ""


class GitHubActionsClient:
    def __init__(self, token: str) -> None:
        if not (token and token.strip()):
            raise ValueError("GitHub token is required")
        self._headers = {
            "Authorization": f"Bearer {token.strip()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_default_branch(self, owner: str, repo: str) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}"
        resp = requests.get(url, headers=self._headers, timeout=30)
        if resp.status_code == 404:
            raise GitHubActionsError(f"Repository not found: {owner}/{repo}")
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        data = resp.json() or {}
        return (data.get("default_branch") or "main").strip()

    def get_latest_release_tag(self, owner: str, repo: str) -> Optional[str]:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        resp = requests.get(url, headers=self._headers, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json() or {}
        tag = (data.get("tag_name") or "").strip()
        return tag or None

    def list_releases(self, owner: str, repo: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        resp = requests.get(
            url,
            headers=self._headers,
            params={"per_page": min(max(limit, 1), 100)},
            timeout=30,
        )
        if resp.status_code == 404:
            return []
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        data = resp.json() or []
        return data if isinstance(data, list) else []

    def latest_release_with_prefix(self, owner: str, repo: str, prefix: str) -> Optional[Dict[str, Any]]:
        """Newest non-draft GitHub release whose tag starts with prefix (list is newest-first)."""
        needle = (prefix or "").strip()
        if not needle:
            return None
        page = 1
        per_page = 100
        while page <= 10:
            url = f"https://api.github.com/repos/{owner}/{repo}/releases"
            resp = requests.get(
                url,
                headers=self._headers,
                params={"per_page": per_page, "page": page},
                timeout=30,
            )
            if resp.status_code == 404:
                return None
            if resp.status_code in (401, 403):
                raise GitHubActionsError(
                    f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
                )
            resp.raise_for_status()
            data = resp.json() or []
            if not isinstance(data, list):
                return None
            for rel in data:
                if not isinstance(rel, dict) or rel.get("draft"):
                    continue
                tag = (rel.get("tag_name") or "").strip()
                if tag.startswith(needle):
                    return rel
            if len(data) < per_page:
                break
            page += 1
        return None

    def compare_refs(self, owner: str, repo: str, base: str, head: str) -> Dict[str, Any]:
        url = f"https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}"
        resp = requests.get(url, headers=self._headers, timeout=60)
        if resp.status_code == 404:
            raise GitHubActionsError(f"Compare failed: {owner}/{repo} {base}...{head}")
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        return resp.json() or {}

    def trigger_workflow(
        self,
        owner: str,
        repo: str,
        workflow_id: str,
        ref: str,
        inputs: Optional[Dict[str, str]] = None,
    ) -> None:
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
        payload: Dict[str, Any] = {"ref": ref}
        if inputs:
            payload["inputs"] = inputs
        resp = requests.post(url, headers=self._headers, json=payload, timeout=30)
        if resp.status_code == 204:
            return
        if resp.status_code == 404:
            raise GitHubActionsError(
                f"Workflow not found or missing workflow_dispatch: {workflow_id} in {owner}/{repo}"
            )
        if resp.status_code == 403:
            raise GitHubActionsError(
                "GitHub returned 403. Ensure GITHUB_TOKEN has actions:write (or repo scope with Actions access)."
            )
        if resp.status_code == 422:
            raise GitHubActionsError(
                f"Workflow dispatch rejected: {_github_error_detail(resp)}"
            )
        resp.raise_for_status()

    def list_workflow_runs(
        self,
        owner: str,
        repo: str,
        workflow_id: str,
        *,
        branch: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        params: Dict[str, Any] = {"per_page": min(max(limit, 1), 20)}
        if branch:
            params["branch"] = branch
        resp = requests.get(url, headers=self._headers, params=params, timeout=30)
        if resp.status_code == 404:
            raise GitHubActionsError(f"Workflow not found: {workflow_id}")
        resp.raise_for_status()
        data = resp.json() or {}
        runs = data.get("workflow_runs") or []
        return runs if isinstance(runs, list) else []

    def get_workflow_run(self, owner: str, repo: str, run_id: int) -> Dict[str, Any]:
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}"
        resp = requests.get(url, headers=self._headers, timeout=30)
        if resp.status_code == 404:
            raise GitHubActionsError(f"Workflow run not found: {run_id}")
        resp.raise_for_status()
        return resp.json() or {}

    def list_workflow_jobs(self, owner: str, repo: str, run_id: int) -> List[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{int(run_id)}/jobs"
        resp = requests.get(
            url,
            headers=self._headers,
            params={"per_page": 100},
            timeout=30,
        )
        if resp.status_code == 404:
            raise GitHubActionsError(f"Workflow run not found: {run_id}")
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        data = resp.json() or {}
        jobs = data.get("jobs") or []
        return jobs if isinstance(jobs, list) else []

    def get_job_logs(self, owner: str, repo: str, job_id: int) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/jobs/{int(job_id)}/logs"
        resp = requests.get(url, headers=self._headers, timeout=60, allow_redirects=True)
        if resp.status_code == 404:
            return ""
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        return resp.text or ""


def clean_gha_log_line(line: str) -> str:
    text = _ANSI_RE.sub("", line or "")
    text = _GHA_PREFIX_RE.sub("", text)
    return text.rstrip()


def excerpt_gha_logs(raw: str, *, max_chars: int = 4000, tail_lines: int = 80) -> str:
    """Return a short, chat-safe excerpt around the likely failure in GHA logs."""
    lines = [clean_gha_log_line(line) for line in (raw or "").splitlines()]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return ""

    error_idx: Optional[int] = None
    for i in range(len(lines) - 1, -1, -1):
        low = lines[i].lower()
        if any(hint in low for hint in _ERROR_HINTS):
            error_idx = i
            break

    if error_idx is None:
        chosen = lines[-tail_lines:]
    else:
        start = max(0, error_idx - (tail_lines - 8))
        chosen = lines[start : error_idx + 8]

    text = "\n".join(chosen).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 16].rstrip() + "\n…(truncated)"
    return text
