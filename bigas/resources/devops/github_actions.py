"""GitHub Actions API client for workflow dispatch and status."""
from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile
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

    def get_run_logs_zip_size(self, owner: str, repo: str, run_id: int) -> Optional[int]:
        """Return Content-Length for the workflow run logs zip, or None if unknown."""
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{int(run_id)}/logs"
        resp = requests.head(
            url,
            headers=self._headers,
            timeout=30,
            allow_redirects=True,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        length = resp.headers.get("Content-Length")
        try:
            return int(length) if length is not None else None
        except (TypeError, ValueError):
            return None

    def download_run_logs_zip(
        self,
        owner: str,
        repo: str,
        run_id: int,
        *,
        max_bytes: int,
        dest_path: str,
    ) -> int:
        """Download workflow run logs zip to dest_path; abort if larger than max_bytes."""
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{int(run_id)}/logs"
        with requests.get(
            url,
            headers=self._headers,
            timeout=120,
            allow_redirects=True,
            stream=True,
        ) as resp:
            if resp.status_code == 404:
                raise GitHubActionsError(f"Workflow run logs not found: {run_id}")
            if resp.status_code in (401, 403):
                raise GitHubActionsError(
                    f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
                )
            resp.raise_for_status()
            total = 0
            with open(dest_path, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise GitHubActionsError(
                            f"Workflow run logs exceed size limit ({max_bytes} bytes)"
                        )
                    handle.write(chunk)
        return total

    def get_commit(self, owner: str, repo: str, ref: str) -> Dict[str, Any]:
        url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"
        resp = requests.get(url, headers=self._headers, timeout=60)
        if resp.status_code == 404:
            raise GitHubActionsError(f"Commit not found: {ref}")
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        return resp.json() or {}

    def get_ref_sha(self, owner: str, repo: str, ref: str) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{ref}"
        resp = requests.get(url, headers=self._headers, timeout=30)
        if resp.status_code == 404:
            raise GitHubActionsError(f"Branch not found: {ref}")
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        data = resp.json() or {}
        obj = data.get("object") or {}
        sha = (obj.get("sha") or "").strip()
        if not sha:
            raise GitHubActionsError(f"Could not resolve ref: {ref}")
        return sha

    def create_blob(self, owner: str, repo: str, content: str) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/blobs"
        resp = requests.post(
            url,
            headers=self._headers,
            json={"content": content, "encoding": "utf-8"},
            timeout=60,
        )
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        data = resp.json() or {}
        sha = (data.get("sha") or "").strip()
        if not sha:
            raise GitHubActionsError("GitHub did not return blob sha")
        return sha

    def create_tree(
        self,
        owner: str,
        repo: str,
        base_tree_sha: str,
        entries: List[Dict[str, str]],
    ) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees"
        resp = requests.post(
            url,
            headers=self._headers,
            json={"base_tree": base_tree_sha, "tree": entries},
            timeout=60,
        )
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        data = resp.json() or {}
        sha = (data.get("sha") or "").strip()
        if not sha:
            raise GitHubActionsError("GitHub did not return tree sha")
        return sha

    def create_git_commit(
        self,
        owner: str,
        repo: str,
        *,
        message: str,
        tree_sha: str,
        parent_sha: str,
    ) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/commits"
        resp = requests.post(
            url,
            headers=self._headers,
            json={
                "message": message,
                "tree": tree_sha,
                "parents": [parent_sha],
            },
            timeout=60,
        )
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        data = resp.json() or {}
        sha = (data.get("sha") or "").strip()
        if not sha:
            raise GitHubActionsError("GitHub did not return commit sha")
        return sha

    def create_branch_ref(
        self,
        owner: str,
        repo: str,
        branch: str,
        commit_sha: str,
    ) -> None:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/refs"
        resp = requests.post(
            url,
            headers=self._headers,
            json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
            timeout=30,
        )
        if resp.status_code == 422:
            raise GitHubActionsError(f"Branch already exists: {branch}")
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> Dict[str, Any]:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        resp = requests.post(
            url,
            headers=self._headers,
            json={"title": title, "body": body, "head": head, "base": base},
            timeout=60,
        )
        if resp.status_code == 422:
            raise GitHubActionsError(
                f"Pull request rejected: {_github_error_detail(resp)}"
            )
        if resp.status_code in (401, 403):
            raise GitHubActionsError(
                f"GitHub auth failed ({resp.status_code}): {_github_error_detail(resp)}"
            )
        resp.raise_for_status()
        return resp.json() or {}


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


HOTFIX_BRANCH_PREFIX = "bigas-hotfix/"
DEFAULT_LOG_ZIP_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_LOG_TAIL_LINES = 2000


def _normalize_job_folder(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _match_job_log_paths(zip_names: List[str], job_name: str) -> List[str]:
    """Return zip member paths that belong to the given job folder."""
    needle = _normalize_job_folder(job_name)
    if not needle:
        return []
    matched: List[str] = []
    for path in zip_names:
        parts = path.split("/")
        if len(parts) < 2:
            continue
        folder = parts[0]
        if _normalize_job_folder(folder) == needle or needle in _normalize_job_folder(folder):
            if path.endswith(".txt") or "/" in path:
                matched.append(path)
    return sorted(matched)


def extract_job_logs_from_zip(zip_path: str, job_name: str) -> str:
    """Extract and concatenate log text files for a job from a GHA logs zip."""
    chunks: List[str] = []
    with zipfile.ZipFile(zip_path, "r") as archive:
        names = archive.namelist()
        paths = _match_job_log_paths(names, job_name)
        if not paths:
            # Fallback: any .txt under a folder containing the job name fragment
            frag = _normalize_job_folder(job_name)[:12]
            paths = sorted(
                n
                for n in names
                if n.endswith(".txt") and frag and frag in _normalize_job_folder(n.split("/")[0])
            )
        for path in paths:
            try:
                raw = archive.read(path).decode("utf-8", errors="replace")
            except KeyError:
                continue
            if raw.strip():
                chunks.append(raw)
    return "\n".join(chunks)


def truncate_log_text(raw: str, *, tail_lines: int = DEFAULT_LOG_TAIL_LINES, max_chars: int = 32000) -> str:
    """Keep the tail of a log and apply smart excerpting for LLM context."""
    lines = (raw or "").splitlines()
    if len(lines) > tail_lines:
        lines = lines[-tail_lines:]
    trimmed = "\n".join(lines)
    return excerpt_gha_logs(trimmed, max_chars=max_chars, tail_lines=min(tail_lines, 120))


def format_commit_diff(commit: Dict[str, Any], *, max_chars: int = 24000) -> str:
    """Format commit file patches into a diff string for LLM context."""
    files = commit.get("files") or []
    if not isinstance(files, list):
        files = []
    parts: List[str] = []
    sha = (commit.get("sha") or "")[:12]
    msg = ((commit.get("commit") or {}).get("message") or "").strip()
    header = f"Commit {sha}"
    if msg:
        header += f": {msg.splitlines()[0]}"
    parts.append(header)
    for item in files:
        if not isinstance(item, dict):
            continue
        filename = (item.get("filename") or "").strip()
        if not filename:
            continue
        status = (item.get("status") or "modified").strip()
        patch = (item.get("patch") or "").strip()
        block = f"\n--- {filename} ({status}) ---"
        if patch:
            block += f"\n{patch}"
        else:
            block += "\n(no textual diff — binary or too large)"
        parts.append(block)
    text = "\n".join(parts).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 24].rstrip() + "\n…(diff truncated)"
    return text


def find_failed_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    failed = [
        job
        for job in jobs
        if isinstance(job, dict) and (job.get("conclusion") or "").lower() == "failure"
    ]
    if failed:
        return failed
    return [
        job
        for job in jobs
        if isinstance(job, dict)
        and (job.get("conclusion") or "").lower() not in ("success", "skipped", "cancelled", "")
    ]
