"""Launch a Cursor cloud agent to fix a failed production deploy and open a PR."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from bigas.discord_webhook import post_to_discord
from bigas.github_refs import is_owner_repo
from bigas.resources.cto.autofix.cursor_client import (
    CursorCloudAgentClient,
    CursorCloudAgentError,
)

logger = logging.getLogger(__name__)


class DeployHotfixError(RuntimeError):
    pass


def _slugify(text: str, *, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s or "deploy")[:max_len].strip("-") or "deploy"


def _cursor_model() -> Optional[str]:
    return (
        (os.environ.get("BIGAS_CTO_AUTOFIX_MODEL") or "").strip()
        or (os.environ.get("BIGAS_JIRA_IMPLEMENT_MODEL") or "").strip()
        or None
    )


def _post_discord_cto(message: str) -> None:
    url = (os.environ.get("DISCORD_WEBHOOK_URL_CTO") or "").strip()
    msg = (message or "").strip()
    if len(msg) > 1900:
        msg = msg[:1897] + "..."
    try:
        post_to_discord(url, msg, chat_agent_id="cto")
    except Exception:
        logger.warning("Discord CTO notify failed for deploy hotfix", exc_info=True)


def build_failed_deploy_prompt(
    *,
    repo: str,
    failures: List[Dict[str, Any]],
    starting_ref: str = "main",
) -> str:
    blocks: List[str] = []
    for item in failures:
        workflow = (item.get("workflow") or "workflow").strip()
        run_id = item.get("run_id") or "?"
        conclusion = (item.get("conclusion") or "failure").strip()
        html_url = (item.get("html_url") or "").strip()
        excerpt = (item.get("excerpt") or "").strip() or "(no log excerpt)"
        header = f"### {workflow} run #{run_id} ({conclusion})"
        if html_url:
            header += f"\n{html_url}"
        blocks.append(f"{header}\n\n```\n{excerpt}\n```")

    failure_text = "\n\n".join(blocks) if blocks else "(no failed runs provided)"
    return f"""You are the Bigas CTO agent fixing a failed production deploy.

Repository: {repo}
Base branch: {starting_ref}

## Failed GitHub Actions
{failure_text}

## Instructions
1. Diagnose the failure from the logs and the current code on `{starting_ref}`.
2. Apply the smallest safe code fix that makes the failed deploy workflow succeed.
3. Do not install unrelated dependencies just to silence a missing tsconfig `extends` (e.g. do not add Expo to a Vite web app). Isolate config so the web build does not pick up a mobile/Expo tsconfig.
4. Verify with the same command the workflow ran when practical (e.g. `npm run build` in `web/` for a web deploy).
5. Open a pull request with a concise explanation of the root cause and fix.
6. Do not merge, do not force-push, and do not change unrelated files.
7. Do NOT ask for confirmation, approval, or whether to proceed. This is an unattended cloud agent — implement immediately and open the PR. Do not stop after a proposal.
"""


def launch_failed_deploy_fix(
    *,
    repo: str,
    failures: List[Dict[str, Any]],
    starting_ref: str = "main",
    cursor_api_key: Optional[str] = None,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Launch a Cursor cloud agent that implements a fix and opens a PR."""
    if not is_owner_repo(repo):
        raise DeployHotfixError("repo is required in the form 'owner/repo'")
    if not failures:
        raise DeployHotfixError("No failed workflow runs to fix")

    key = (cursor_api_key or "").strip() or (os.environ.get("CURSOR_API_KEY") or "").strip()
    if not key:
        raise DeployHotfixError("CURSOR_API_KEY is required to launch a CTO fix agent")

    ref = (starting_ref or "main").strip() or "main"
    workflows = [
        (item.get("workflow") or "workflow").strip()
        for item in failures
        if (item.get("workflow") or "").strip()
    ]
    label = workflows[0] if workflows else "deploy"
    prompt = build_failed_deploy_prompt(repo=repo, failures=failures, starting_ref=ref)
    client = CursorCloudAgentClient(api_key=key)
    try:
        launched = client.launch_implementation(
            repo_url=f"https://github.com/{repo}",
            prompt_text=prompt,
            starting_ref=ref,
            name=f"Bigas deploy hotfix {repo} {_slugify(label)}"[:100],
            model_id=(model_id or "").strip() or _cursor_model(),
        )
    except CursorCloudAgentError as e:
        raise DeployHotfixError(str(e)) from e

    agent_url = (launched.get("agent_url") or "").strip()
    agent_id = (launched.get("agent_id") or "").strip()
    run_id = launched.get("run_id") or ""
    _post_discord_cto(
        f"**CTO deploy hotfix launched**\n"
        f"Repo: `{repo}`\n"
        f"Failed: {', '.join(workflows) or 'workflow'}\n"
        f"Agent: {agent_url or agent_id}"
    )
    return {
        "status": "ok",
        "launched": True,
        "repo": repo,
        "starting_ref": ref,
        "agent_id": agent_id,
        "agent_url": agent_url,
        "run_id": run_id,
        "summary": (
            f"CTO agent launched to fix the failed deploy and open a PR. "
            f"{agent_url or agent_id}"
        ).strip(),
    }
