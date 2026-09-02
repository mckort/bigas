"""Hotfix: cherry-pick a staging merge to main for a Jira issue (BIG-42)."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

from bigas.resources.devops.github_actions import GitHubActionsClient, GitHubActionsError
from bigas.resources.product.hotfix.cherry_pick import (
    CherryPickError,
    cherry_pick_commit_to_branch,
    find_merged_pr_for_issue,
    open_pull_request,
)
from bigas.resources.product.jira_automation.config import JiraAutomationConfig
from bigas.resources.product.release_workflow import (
    project_branch_mapping_from_env,
    resolve_production_branch,
)
from bigas.tickets.config import jira_configured

logger = logging.getLogger(__name__)

_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
_CHERRY_PICK_WORKFLOW = "cherry_pick.yml"


class HotfixError(RuntimeError):
    pass


def _github_token() -> str:
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        raise HotfixError("GITHUB_TOKEN is required")
    return token


def _parse_repo(repo: str) -> tuple[str, str]:
    text = (repo or "").strip()
    if "/" not in text:
        raise HotfixError(f"Invalid repo: {repo!r}")
    owner, name = text.split("/", 1)
    return owner.strip(), name.strip()


def _issue_key_from_input(raw: str) -> str:
    key = (raw or "").strip().upper()
    if not _ISSUE_KEY_RE.match(key):
        raise HotfixError(f"Invalid issue key: {raw!r}")
    return key


class HotfixService:
    def __init__(self, *, config: Optional[JiraAutomationConfig] = None) -> None:
        self._config = config or JiraAutomationConfig.from_env()

    def cherry_pick_to_main(
        self,
        *,
        issue_key: str,
        repo: Optional[str] = None,
        staging_branch: Optional[str] = None,
        production_branch: Optional[str] = None,
        use_workflow: bool = True,
    ) -> Dict[str, Any]:
        """
        Cherry-pick a merged staging PR for issue_key onto main and open a hotfix PR.

        Chat / MCP: `@bigas hotfix VFA-123`
        """
        key = _issue_key_from_input(issue_key)
        project_key = key.split("-", 1)[0]
        mapped_repo = repo or self._config.repo_for_project(project_key)
        if not mapped_repo:
            raise HotfixError(f"No GitHub repo mapped for project {project_key}")

        owner, name = _parse_repo(mapped_repo)
        branch_map = self._config.project_branch_map or project_branch_mapping_from_env()
        staging = (
            (staging_branch or "").strip()
            or branch_map.get(project_key)
            or branch_map.get("DEFAULT")
            or "staging"
        )
        production = (
            (production_branch or "").strip()
            or resolve_production_branch(
                project_key=project_key,
                repo=mapped_repo,
                repo_base_branches=self._config.repo_base_branches,
                default_base_branch=self._config.default_base_branch,
            )
        )
        if staging == production:
            raise HotfixError(
                f"Project {project_key} does not use a staging branch "
                f"(both staging and production are `{production}`)"
            )

        token = _github_token()
        pr = find_merged_pr_for_issue(
            token=token,
            owner=owner,
            repo=name,
            issue_key=key,
            base_branch=staging,
        )
        merge_sha = (pr.get("merge_commit_sha") or "").strip()
        pr_number = pr.get("number")
        source_pr_url = (pr.get("html_url") or "").strip()
        if not merge_sha:
            raise HotfixError(f"Merged PR for {key} has no merge_commit_sha")

        if use_workflow:
            dispatched = self._try_dispatch_workflow(
                owner=owner,
                repo=name,
                ref=production,
                issue_key=key,
                merge_sha=merge_sha,
                production_branch=production,
            )
            if dispatched:
                return {
                    "ok": True,
                    "mode": "workflow_dispatch",
                    "issue_key": key,
                    "repo": mapped_repo,
                    "staging_branch": staging,
                    "production_branch": production,
                    "source_pr_url": source_pr_url,
                    "source_pr_number": pr_number,
                    "merge_commit_sha": merge_sha,
                    **dispatched,
                }

        hotfix_branch = f"hotfix/{key.lower()}"
        branch, commit_sha = cherry_pick_commit_to_branch(
            token=token,
            owner=owner,
            repo=name,
            merge_commit_sha=merge_sha,
            target_branch=production,
            new_branch=hotfix_branch,
        )
        pr_url = open_pull_request(
            token=token,
            owner=owner,
            repo=name,
            head_branch=branch,
            base_branch=production,
            title=f"Hotfix: {key}",
            body=(
                f"Jira: {key}\n\n"
                f"Cherry-picked from staging PR #{pr_number} ({source_pr_url}).\n"
                f"Merge commit: `{merge_sha}`\n\n"
                "Opened by Bigas hotfix automation."
            ),
        )
        return {
            "ok": True,
            "mode": "github_api",
            "issue_key": key,
            "repo": mapped_repo,
            "staging_branch": staging,
            "production_branch": production,
            "hotfix_branch": branch,
            "commit_sha": commit_sha,
            "pr_url": pr_url,
            "source_pr_url": source_pr_url,
            "source_pr_number": pr_number,
            "merge_commit_sha": merge_sha,
        }

    def _try_dispatch_workflow(
        self,
        *,
        owner: str,
        repo: str,
        ref: str,
        issue_key: str,
        merge_sha: str,
        production_branch: str,
    ) -> Optional[Dict[str, Any]]:
        token = _github_token()
        try:
            client = GitHubActionsClient(token=token)
            client.trigger_workflow(
                owner=owner,
                repo=repo,
                workflow_id=_CHERRY_PICK_WORKFLOW,
                ref=ref,
                inputs={
                    "issue_key": issue_key,
                    "commit_sha": merge_sha,
                    "production_branch": production_branch,
                },
            )
            return {
                "workflow": _CHERRY_PICK_WORKFLOW,
                "workflow_ref": ref,
            }
        except GitHubActionsError as exc:
            logger.info(
                "Cherry-pick workflow dispatch unavailable for %s/%s: %s",
                owner,
                repo,
                exc,
            )
            return None
