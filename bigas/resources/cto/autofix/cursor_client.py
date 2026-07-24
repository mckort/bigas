"""Minimal Cursor Cloud Agents API client (HTTPS + Basic auth)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

CURSOR_API_BASE = "https://api.cursor.com/v1"


class CursorCloudAgentError(RuntimeError):
    pass


class CursorCloudAgentClient:
    def __init__(self, api_key: str) -> None:
        key = (api_key or "").strip()
        if not key:
            raise ValueError("CURSOR_API_KEY is required")
        self._api_key = key

    def launch_pr_autofix(
        self,
        *,
        repo_url: str,
        pr_url: str,
        prompt_text: str,
        name: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Launch a cloud agent against an existing PR head branch.

        Uses prUrl + workOnCurrentBranch so fixes land on the open PR.
        Does not auto-create a new PR and does not request reviewers.
        """
        payload: dict[str, Any] = {
            "prompt": {"text": prompt_text},
            "repos": [
                {
                    "url": repo_url,
                    "prUrl": pr_url,
                }
            ],
            "workOnCurrentBranch": True,
            "autoCreatePR": False,
            "skipReviewerRequest": True,
        }
        if name:
            payload["name"] = name[:100]
        if model_id:
            payload["model"] = {"id": model_id}

        return self._post_agent(payload, context=pr_url)

    def launch_implementation(
        self,
        *,
        repo_url: str,
        prompt_text: str,
        starting_ref: str = "main",
        name: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Launch a cloud agent on a repo to implement work and open a new PR.

        Creates a Cursor-generated branch from starting_ref and sets autoCreatePR.
        """
        ref = (starting_ref or "main").strip() or "main"
        payload: dict[str, Any] = {
            "prompt": {"text": prompt_text},
            "repos": [
                {
                    "url": repo_url,
                    "startingRef": ref,
                }
            ],
            "workOnCurrentBranch": False,
            "autoCreatePR": True,
            "skipReviewerRequest": True,
        }
        if name:
            payload["name"] = name[:100]
        if model_id:
            payload["model"] = {"id": model_id}

        return self._post_agent(payload, context=repo_url)

    def _post_agent(self, payload: dict[str, Any], *, context: str) -> dict[str, Any]:
        try:
            resp = requests.post(
                f"{CURSOR_API_BASE}/agents",
                auth=(self._api_key, ""),
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
        except requests.RequestException as e:
            raise CursorCloudAgentError(f"Cursor API request failed: {e}") from e

        if resp.status_code in (401, 403):
            raise CursorCloudAgentError(
                f"Cursor API auth error {resp.status_code}: {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            raise CursorCloudAgentError(
                f"Cursor API error {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json() if resp.text else {}
        agent = data.get("agent") or {}
        run = data.get("run") or {}
        agent_id = agent.get("id") or ""
        agent_url = agent.get("url") or (
            f"https://cursor.com/agents/{agent_id}" if agent_id else ""
        )
        logger.info(
            "Launched Cursor cloud agent %s for %s (run=%s)",
            agent_id,
            context,
            run.get("id"),
        )
        return {
            "agent_id": agent_id,
            "agent_url": agent_url,
            "run_id": run.get("id") or "",
            "raw": data,
        }

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        aid = (agent_id or "").strip()
        if not aid:
            raise CursorCloudAgentError("agent_id is required")
        return self._get_json(f"{CURSOR_API_BASE}/agents/{aid}")

    def get_run(self, agent_id: str, run_id: str) -> dict[str, Any]:
        aid = (agent_id or "").strip()
        rid = (run_id or "").strip()
        if not aid or not rid:
            raise CursorCloudAgentError("agent_id and run_id are required")
        return self._get_json(f"{CURSOR_API_BASE}/agents/{aid}/runs/{rid}")

    def get_run_status(
        self,
        *,
        agent_id: str,
        run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Return normalized run status for polling.

        Terminal statuses: FINISHED, ERROR, CANCELLED, EXPIRED.
        """
        agent = self.get_agent(agent_id)
        rid = (run_id or "").strip() or (agent.get("latestRunId") or "").strip()
        if not rid:
            raise CursorCloudAgentError("No run_id available for agent")
        run = self.get_run(agent_id, rid)
        status = (run.get("status") or "").strip().upper()
        # Some Cursor API versions expose status on the agent, not the run.
        if not status:
            status = (agent.get("status") or "").strip().upper()
        terminal = status in {"FINISHED", "ERROR", "CANCELLED", "EXPIRED"}
        agent_url = agent.get("url") or f"https://cursor.com/agents/{agent_id}"
        pr_url, branch_name = extract_pr_and_branch(agent, run)
        return {
            "agent_id": agent_id,
            "run_id": rid,
            "status": status or "UNKNOWN",
            "done": terminal,
            "ok": status == "FINISHED",
            "agent_url": agent_url,
            "pr_url": pr_url,
            "branch_name": branch_name,
            "result_text": (run.get("result") or run.get("text") or "")
            if isinstance(run.get("result"), str)
            else (run.get("text") or ""),
            "run": run,
            "agent": agent,
        }


def extract_pr_and_branch(*payloads: Any) -> tuple[str, str]:
    """Best-effort PR URL + branch from Cursor agent/run payloads."""
    pr_url = ""
    branch_name = ""

    def walk(obj: Any, depth: int = 0) -> None:
        nonlocal pr_url, branch_name
        if depth > 6 or obj is None:
            return
        if isinstance(obj, dict):
            for key, val in obj.items():
                lk = str(key).lower()
                if isinstance(val, str) and val.strip():
                    s = val.strip()
                    if not pr_url and (
                        lk in {"prurl", "pr_url", "pullrequesturl"}
                        or ("github.com" in s and "/pull/" in s)
                    ):
                        pr_url = s
                    if not branch_name and lk in {
                        "branchname",
                        "branch_name",
                        "branch",
                        "headref",
                        "head_ref",
                    }:
                        branch_name = s
                elif isinstance(val, (dict, list)):
                    walk(val, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:50]:
                walk(item, depth + 1)

    for payload in payloads:
        walk(payload)
        if pr_url and branch_name:
            break
    return pr_url, branch_name

    def _get_json(self, url: str) -> dict[str, Any]:
        try:
            resp = requests.get(url, auth=(self._api_key, ""), timeout=60)
        except requests.RequestException as e:
            raise CursorCloudAgentError(f"Cursor API request failed: {e}") from e
        if resp.status_code in (401, 403):
            raise CursorCloudAgentError(
                f"Cursor API auth error {resp.status_code}: {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            raise CursorCloudAgentError(
                f"Cursor API error {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json() if resp.text else {}
        if not isinstance(data, dict):
            raise CursorCloudAgentError("Cursor API returned unexpected payload")
        return data
