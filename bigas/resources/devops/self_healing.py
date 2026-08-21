"""Self-healing CI/CD: GitHub workflow_run webhook → DevOps agent hotfix PR."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from bigas.discord_webhook import post_to_discord
from bigas.resources.devops.github_actions import HOTFIX_BRANCH_PREFIX
from bigas.resources.devops.service import (
    DevOpsError,
    create_github_pr,
    fetch_github_action_logs,
    get_commit_diff,
)

logger = logging.getLogger(__name__)

_SELF_HEALING_JOBS: Dict[str, Dict[str, Any]] = {}
_SELF_HEALING_LOCK = threading.Lock()


class SelfHealingError(RuntimeError):
    pass


def self_healing_enabled() -> bool:
    flag = (os.environ.get("ENABLE_SELF_HEALING_CI") or "true").strip().lower()
    return flag in ("1", "true", "yes")


def webhook_secret() -> str:
    return (
        (os.environ.get("GITHUB_WEBHOOK_SECRET") or "").strip()
        or (os.environ.get("JIRA_AUTOMATION_WEBHOOK_SECRET") or "").strip()
    )


def verify_github_signature(payload_body: bytes, signature_header: Optional[str], secret: str) -> bool:
    """Verify GitHub X-Hub-Signature-256 HMAC."""
    exp = (secret or "").strip()
    if not exp or not signature_header:
        return False
    header = signature_header.strip()
    if not header.lower().startswith("sha256="):
        return False
    expected = header.split("=", 1)[1].strip()
    digest = hmac.new(exp.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def should_process_workflow_run(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (process, reason) for a workflow_run webhook payload."""
    if not self_healing_enabled():
        return False, "self_healing_disabled"

    action = (payload.get("action") or "").strip().lower()
    if action != "completed":
        return False, f"ignored_action:{action or 'missing'}"

    run = payload.get("workflow_run") or {}
    if not isinstance(run, dict):
        return False, "missing_workflow_run"

    conclusion = (run.get("conclusion") or "").strip().lower()
    if conclusion != "failure":
        return False, f"ignored_conclusion:{conclusion or 'missing'}"

    branch = (run.get("head_branch") or "").strip()
    if branch.startswith(HOTFIX_BRANCH_PREFIX):
        return False, "ignored_hotfix_branch"

    run_id = run.get("id")
    if not run_id:
        return False, "missing_run_id"

    repo_obj = payload.get("repository") or {}
    owner = ((repo_obj.get("owner") or {}).get("login") or "").strip()
    name = (repo_obj.get("name") or "").strip()
    if not owner or not name:
        return False, "missing_repository"

    return True, "ok"


def parse_workflow_run_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    run = payload.get("workflow_run") or {}
    repo_obj = payload.get("repository") or {}
    owner = ((repo_obj.get("owner") or {}).get("login") or "").strip()
    name = (repo_obj.get("name") or "").strip()
    run_id = int(run.get("id"))
    head_sha = (run.get("head_sha") or "").strip()
    head_branch = (run.get("head_branch") or "").strip()
    html_url = (run.get("html_url") or "").strip()
    if not html_url:
        html_url = f"https://github.com/{owner}/{name}/actions/runs/{run_id}"
    workflow_name = (run.get("name") or run.get("path") or "workflow").strip()
    return {
        "repo": f"{owner}/{name}",
        "run_id": run_id,
        "head_sha": head_sha,
        "head_branch": head_branch,
        "html_url": html_url,
        "workflow_name": workflow_name,
    }


def build_self_healing_prompt(
    *,
    repo: str,
    run_id: int,
    head_sha: str,
    head_branch: str,
    workflow_name: str,
    html_url: str,
    diff: str,
    logs: str,
) -> str:
    branch = f"{HOTFIX_BRANCH_PREFIX}run-{run_id}"
    return f"""You are the Bigas DevOps agent fixing a failed GitHub Actions CI/CD run.

Repository: {repo}
Failed workflow: {workflow_name}
Run: #{run_id} — {html_url}
Triggering commit: {head_sha} on branch `{head_branch}`

## Commit diff
{diff or "(no diff available)"}

## Failed job logs
{logs or "(no logs available)"}

## Instructions
1. Diagnose the root cause from the logs and the commit diff above.
2. Determine the smallest safe code fix on branch `{head_branch}`.
3. Call `create_github_pr` with:
   - repo: `{repo}`
   - base_branch: `{head_branch}`
   - new_branch_name: `{branch}`
   - title: concise summary of the fix
   - body: explain root cause and fix (include run link)
   - files_to_change: dict mapping file paths to their full new contents
4. Do not merge the PR. Do not change unrelated files.
5. Respond with ONLY JSON:
{{"action":"tool","tool_name":"create_github_pr","arguments":{{...}}}}
If you cannot determine a fix, respond with:
{{"action":"answer","text":"<brief explanation>"}}
"""


def _post_discord_devops(message: str) -> None:
    url = (
        (os.environ.get("DISCORD_WEBHOOK_URL_DEVOPS") or "").strip()
        or (os.environ.get("DISCORD_WEBHOOK_URL_CTO") or "").strip()
    )
    if not url or url.lower().startswith("placeholder"):
        return
    msg = (message or "").strip()
    if len(msg) > 1900:
        msg = msg[:1897] + "..."
    try:
        post_to_discord(url, msg)
    except Exception:
        logger.warning("Discord notify failed for self-healing CI", exc_info=True)


def _parse_json_action(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    elif not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
        else:
            return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def run_self_healing_fix(
    *,
    repo: str,
    run_id: int,
    head_sha: str,
    head_branch: str,
    workflow_name: str,
    html_url: str,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch context and invoke the DevOps agent to open a hotfix PR."""
    if not head_sha:
        raise SelfHealingError("head_sha is required to map the failure to a commit")

    logs_result = fetch_github_action_logs(
        repo=repo,
        run_id=run_id,
        github_token=github_token,
    )
    diff_result = get_commit_diff(
        repo=repo,
        commit_sha=head_sha,
        github_token=github_token,
    )

    from bigas.llm.factory import get_llm_client

    prompt = build_self_healing_prompt(
        repo=repo,
        run_id=run_id,
        head_sha=head_sha,
        head_branch=head_branch or "main",
        workflow_name=workflow_name,
        html_url=html_url,
        diff=(diff_result.get("diff") or "").strip(),
        logs=(logs_result.get("logs") or "").strip(),
    )

    llm, _ = get_llm_client(feature="chat")
    raw = llm.complete(
        [
            {
                "role": "system",
                "content": (
                    "You are the Bigas DevOps specialist. Fix CI/CD failures by opening "
                    "hotfix pull requests via create_github_pr. Follow the user instructions exactly."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    action = _parse_json_action(raw)
    if not action:
        raise SelfHealingError("DevOps agent did not return a structured response")

    if action.get("action") == "answer":
        text = str(action.get("text") or "").strip() or "Could not determine a fix."
        return {
            "status": "skipped",
            "summary": text,
            "repo": repo,
            "run_id": run_id,
        }

    if action.get("action") != "tool":
        raise SelfHealingError(f"Unexpected agent action: {action.get('action')}")

    tool_name = (action.get("tool_name") or "").strip()
    if tool_name != "create_github_pr":
        raise SelfHealingError(f"Unexpected tool: {tool_name}")

    args = action.get("arguments") or {}
    if not isinstance(args, dict):
        raise SelfHealingError("create_github_pr arguments must be an object")

    files = args.get("files_to_change") or {}
    if isinstance(files, list):
        converted: Dict[str, str] = {}
        for item in files:
            if isinstance(item, dict):
                path = (item.get("path") or item.get("filename") or "").strip()
                content = item.get("content")
                if path and isinstance(content, str):
                    converted[path] = content
        files = converted
    if not isinstance(files, dict) or not files:
        raise SelfHealingError("create_github_pr requires files_to_change")

    pr_result = create_github_pr(
        repo=str(args.get("repo") or repo),
        base_branch=str(args.get("base_branch") or head_branch or "main"),
        new_branch_name=str(args.get("new_branch_name") or f"{HOTFIX_BRANCH_PREFIX}run-{run_id}"),
        title=str(args.get("title") or f"fix(ci): workflow run #{run_id}"),
        body=str(args.get("body") or f"Automated hotfix for failed run {html_url}"),
        files_to_change={str(k): str(v) for k, v in files.items()},
        github_token=github_token,
        base_commit_sha=head_sha,
    )

    _post_discord_devops(
        f"**Self-healing CI hotfix opened**\n"
        f"Repo: `{repo}`\n"
        f"Run: #{run_id} ({workflow_name})\n"
        f"PR: {pr_result.get('html_url') or pr_result.get('summary')}"
    )

    return {
        "status": "ok",
        "summary": pr_result.get("summary") or "Hotfix PR opened.",
        "repo": repo,
        "run_id": run_id,
        "head_sha": head_sha,
        "pr_number": pr_result.get("pr_number"),
        "html_url": pr_result.get("html_url"),
        "logs_summary": logs_result.get("summary"),
        "diff_files_changed": diff_result.get("files_changed"),
    }


def _run_self_healing_job(job_id: str, context: Dict[str, Any]) -> None:
    try:
        result = run_self_healing_fix(
            repo=context["repo"],
            run_id=int(context["run_id"]),
            head_sha=context.get("head_sha") or "",
            head_branch=context.get("head_branch") or "main",
            workflow_name=context.get("workflow_name") or "workflow",
            html_url=context.get("html_url") or "",
            github_token=context.get("github_token"),
        )
        with _SELF_HEALING_LOCK:
            _SELF_HEALING_JOBS[job_id] = {"status": "done", "result": result}
    except (DevOpsError, SelfHealingError) as e:
        logger.warning("Self-healing CI job %s failed: %s", job_id, e)
        with _SELF_HEALING_LOCK:
            _SELF_HEALING_JOBS[job_id] = {
                "status": "error",
                "error": str(e),
            }
    except Exception as e:
        logger.exception("Self-healing CI job %s failed", job_id)
        with _SELF_HEALING_LOCK:
            _SELF_HEALING_JOBS[job_id] = {
                "status": "error",
                "error": str(e),
            }


def enqueue_self_healing(context: Dict[str, Any], *, job_id: str) -> None:
    with _SELF_HEALING_LOCK:
        _SELF_HEALING_JOBS[job_id] = {"status": "queued", **context}
    thread = threading.Thread(
        target=_run_self_healing_job,
        args=(job_id, context),
        daemon=True,
        name=f"self-heal-{job_id[:8]}",
    )
    thread.start()


def get_self_healing_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _SELF_HEALING_LOCK:
        return dict(_SELF_HEALING_JOBS.get(job_id) or {})
