"""Rehearsal on a real staging environment, then production deploy.

This is separate from versioned ``prepare deploy PROJECT VERSION`` (a git
branch such as ``staging-0.2.3`` merged to ``main``). A staging environment
is a stack the chat can build, update, and tear down.

Projects are listed in ``DEFAULT_STAGING_ENVS``. ``BIGAS_STAGING_ENV_MAP``
(JSON object) adds or replaces entries. With one configured project,
``prepare staging`` is enough. With several, name the key:
``prepare staging GPW-PROD``.

GPW-PROD is the built-in entry: review ``develop`` against ``production``,
build staging from the live production image plus a database copy, then
deploy only after a green rehearsal. Maintenance stays on until the
post-deploy check is green.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_SEC = 90 * 60
_WORKFLOW_PHASES = (
    "prepare_staging",
    "update_staging",
    "teardown",
    "backup",
    "deploy_production",
)
_STAGING_VERB_RE = re.compile(
    r"\b(?P<verb>prepare\s+staging|update\s+staging|teardown\s+staging)\b"
    r"(?:\s+(?P<key>[A-Za-z][A-Za-z0-9_-]*))?",
    re.I,
)
_PREPARE_DEPLOY_RE = re.compile(
    r"\bprepare\s+deploy(?:\s+(?P<key>[A-Za-z][A-Za-z0-9_-]*))?(?:\s+(?P<rest>\S+))?",
    re.I,
)
_PROJECT_KEY_RE = re.compile(r"[A-Z0-9]+(?:[-_][A-Z0-9]+)*")


@dataclass(frozen=True)
class StagingEnv:
    project_key: str
    repo: str
    candidate_branch: str
    production_branch: str
    staging_url: str
    production_url: str
    workflows: Dict[str, str]


def _gpw_workflows() -> Dict[str, str]:
    return {
        "prepare_staging": "prepare-staging.yml",
        "update_staging": "update-staging.yml",
        "teardown": "teardown-staging.yml",
        "backup": "backup-production.yml",
        "deploy_production": "deploy-production.yml",
    }


DEFAULT_STAGING_ENVS: Dict[str, StagingEnv] = {
    "GPW-PROD": StagingEnv(
        project_key="GPW-PROD",
        repo="Green-Promo-Wear-Global/GPW",
        candidate_branch="develop",
        production_branch="production",
        staging_url="https://staging.greenpromowear.com",
        production_url="https://store.greenpromowear.com",
        workflows=_gpw_workflows(),
    ),
}

_AUTOFIX_PHASES = {"update_staging", "deploy_production"}
_VERB_TO_COMMAND = {
    "prepare staging": "prepare_staging",
    "update staging": "update_staging",
    "teardown staging": "teardown",
}


def keep_maintenance(*, health_ok: bool) -> bool:
    """Maintenance stays up until the post-deploy check is green."""
    return not health_ok


def _normalize_key(value: str) -> str:
    return (value or "").strip().upper()


def _key_token(token: str) -> str:
    key = _normalize_key(token)
    if key and _PROJECT_KEY_RE.fullmatch(key):
        return key
    return ""


def _env_from_mapping(key: str, item: Any) -> Optional[StagingEnv]:
    project_key = _normalize_key(key)
    if not isinstance(item, dict) or not project_key:
        return None
    if not _key_token(project_key):
        logger.warning("Staging env %s has an invalid project key", project_key)
        return None
    workflows_raw = item.get("workflows") if isinstance(item.get("workflows"), dict) else {}
    workflows: Dict[str, str] = {}
    for phase in _WORKFLOW_PHASES:
        raw_val = workflows_raw.get(phase)
        if not isinstance(raw_val, str):
            logger.warning(
                "Staging env %s has invalid workflow mapping for %s", project_key, phase
            )
            return None
        name = raw_val.strip()
        if not name:
            logger.warning("Staging env %s is missing workflow filename for %s", project_key, phase)
            return None
        workflows[phase] = name
    repo = str(item.get("repo") or "").strip()
    if "/" not in repo:
        logger.warning("Staging env %s needs repo owner/name", project_key)
        return None
    return StagingEnv(
        project_key=project_key,
        repo=repo,
        candidate_branch=str(item.get("candidate_branch") or "develop").strip(),
        production_branch=str(item.get("production_branch") or "main").strip(),
        staging_url=str(item.get("staging_url") or "").strip(),
        production_url=str(item.get("production_url") or "").strip(),
        workflows=workflows,
    )


def staging_envs() -> Dict[str, StagingEnv]:
    """Built-in staging environments, plus JSON overrides from BIGAS_STAGING_ENV_MAP."""
    out = dict(DEFAULT_STAGING_ENVS)
    raw = (os.environ.get("BIGAS_STAGING_ENV_MAP") or "").strip()
    if not raw:
        return out
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("BIGAS_STAGING_ENV_MAP is not valid JSON")
        return out
    if not isinstance(parsed, dict):
        logger.warning("BIGAS_STAGING_ENV_MAP must be a JSON object")
        return out
    for key, item in parsed.items():
        env = _env_from_mapping(str(key), item)
        if env:
            out[env.project_key] = env
    return out


def _owner_name(env: StagingEnv) -> tuple:
    owner, name = env.repo.split("/", 1)
    return owner, name


def parse_staging_command(text: str) -> tuple:
    """Return ``(command, project_key)``. Command is empty when this is not a staging-env request."""
    blob = text or ""
    deploy = _PREPARE_DEPLOY_RE.search(blob)
    if deploy:
        key = _key_token(deploy.group("key") or "")
        rest = (deploy.group("rest") or "").strip().rstrip(".,!?;:")
        if rest or not key or key not in staging_envs():
            return "", ""
        return "prepare_deploy", key
    match = _STAGING_VERB_RE.search(blob)
    if not match:
        return "", ""
    command = _VERB_TO_COMMAND[re.sub(r"\s+", " ", match.group("verb").lower())]
    return command, _key_token(match.group("key") or "")


def resolve_staging_env(project_key: str) -> tuple:
    """Return ``(env, error)``. A missing key uses the only configured environment."""
    envs = staging_envs()
    key = _normalize_key(project_key)
    if key:
        env = envs.get(key)
        if env:
            return env, ""
        known = ", ".join(f"**{name}**" for name in envs) or "none"
        return None, f"**{key}** has no staging environment. Configured: {known}."
    if len(envs) == 1:
        return next(iter(envs.values())), ""
    if not envs:
        return None, "No staging environments are configured."
    known = ", ".join(f"**{name}**" for name in envs)
    example = next(iter(envs))
    return None, (
        "Which project? Name it, for example **prepare staging "
        f"{example}**. Configured: {known}."
    )


def is_gpw_command(text: str) -> bool:
    command, _key = parse_staging_command(text)
    return bool(command)


def list_gpw_command_shortcuts() -> list:
    """One composer group per configured staging environment."""
    from bigas.portfolio import brand_name

    groups = []
    for env in staging_envs().values():
        key = env.project_key
        groups.append(
            {
                "key": key,
                "name": brand_name(key) or key,
                "commands": [
                    {"label": "Prepare staging", "prompt": f"prepare staging {key}"},
                    {"label": "Update staging", "prompt": f"update staging {key}"},
                    {"label": "Teardown staging", "prompt": f"teardown staging {key}"},
                    {"label": "Prepare deploy", "prompt": f"prepare deploy {key}"},
                ],
            }
        )
    return groups


def parse_gpw_command(text: str) -> str:
    command, _key = parse_staging_command(text)
    return command


def _store():
    from bigas.chat.db import get_chat_store

    return get_chat_store()


def _post(thread_id: Optional[str], content: str, *, status: Optional[str] = None) -> None:
    if not thread_id or not (content or "").strip():
        return
    meta: Dict[str, Any] = {"agent_id": "devops", "pipeline": True}
    if status:
        meta["status"] = status
    _store().add_message(thread_id, role="assistant", content=content.strip(), metadata=meta)


def _complete_progress(thread_id: Optional[str]) -> None:
    if not thread_id:
        return
    store = _store()
    if not hasattr(store, "patch_message"):
        return
    for message in store.list_messages(thread_id):
        meta = message.get("metadata") or {}
        if meta.get("pipeline") and meta.get("status") == "in_progress":
            store.patch_message(message["message_id"], metadata={"status": "complete"})


def _thread(thread_id: Optional[str]) -> Dict[str, Any]:
    if not thread_id:
        return {}
    return _store().get_thread(thread_id) or {}


def _patch(thread_id: Optional[str], **fields: Any) -> None:
    if not thread_id or not hasattr(_store(), "patch_thread"):
        return
    _store().patch_thread(thread_id, **fields)


def pending_gpw_action(thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    pending = _thread(thread_id).get("pending_deploy")
    if isinstance(pending, dict) and pending.get("kind") == "gpw":
        return pending
    return None


def _rehearsal(thread_id: Optional[str]) -> Dict[str, Any]:
    data = _thread(thread_id).get("gpw_rehearsal")
    return dict(data) if isinstance(data, dict) else {}


def _set_rehearsal(thread_id: Optional[str], payload: Dict[str, Any]) -> None:
    _patch(thread_id, gpw_rehearsal=payload)


def _github():
    from bigas.resources.devops.service import _github_client

    return _github_client()


def _require_staging_env(env: Optional[StagingEnv]) -> StagingEnv:
    if env is not None:
        return env
    configured = staging_envs()
    if len(configured) == 1:
        return next(iter(configured.values()))
    if len(configured) == 0:
        raise ValueError("No staging environments are configured.")
    gpw = configured.get("GPW-PROD")
    if gpw is not None:
        return gpw
    raise ValueError(
        "Staging environment is required when multiple projects are configured."
    )


def review_candidate(env: Optional[StagingEnv] = None) -> Dict[str, Any]:
    """Review the candidate branch against production. Returns shas and whether it may proceed."""
    from bigas.resources.cto.autofix.heuristics import review_is_ready_to_merge
    from bigas.resources.cto.pr_review.service import PRReviewService

    env = _require_staging_env(env)
    client = _github()
    owner, name = _owner_name(env)
    compare = client.compare_refs(owner, name, env.production_branch, env.candidate_branch)
    ahead = int(compare.get("ahead_by") or 0)
    production_sha = client.get_ref_sha(owner, name, env.production_branch)
    candidate_sha = client.get_ref_sha(owner, name, env.candidate_branch)
    if ahead <= 0:
        return {
            "ok": False,
            "reason": (
                f"`{env.candidate_branch}` is not ahead of `{env.production_branch}`. "
                "Nothing new to stage."
            ),
            "production_sha": production_sha,
            "candidate_sha": candidate_sha,
        }
    diff = client.get_compare_diff(owner, name, env.production_branch, env.candidate_branch)
    review = PRReviewService().review(diff)
    body = (review.text or "").strip()
    ready = review_is_ready_to_merge(body)
    pr = client.find_open_pull_request(
        owner, name, head=env.candidate_branch, base=env.production_branch
    )
    return {
        "ok": ready,
        "review": body,
        "production_sha": production_sha,
        "candidate_sha": candidate_sha,
        "pr_number": (pr or {}).get("number"),
        "pr_url": (pr or {}).get("html_url") or "",
    }


def _launch_review_autofix(env: StagingEnv, result: Dict[str, Any]) -> str:
    repo = env.repo
    pr_number = result.get("pr_number")
    body = result.get("review") or ""
    if pr_number:
        from bigas.resources.cto.autofix.service import AutofixService

        launched = AutofixService().run(
            repo=repo,
            pr_number=int(pr_number),
            review_body=body,
        )
        url = (launched.get("agent_url") or launched.get("pr_url") or "").strip()
        return f"Autofix started on the open PR{(': ' + url) if url else '.'}"
    from bigas.resources.cto.deploy_hotfix import launch_failed_deploy_fix

    launched = launch_failed_deploy_fix(
        repo=repo,
        failures=[
            {
                "workflow": "prepare-staging review",
                "run_id": "",
                "conclusion": "review",
                "excerpt": body[:4000] or "(empty review)",
            }
        ],
        starting_ref=env.candidate_branch,
        extra_instructions=(
            f"This is a code review of {env.candidate_branch} against {env.production_branch} "
            f"for {env.project_key}. Fix the review findings on {env.candidate_branch}. "
            "Do not deploy and do not touch production."
        ),
    )
    url = (launched.get("agent_url") or "").strip()
    return f"Autofix started on `{env.candidate_branch}`{(': ' + url) if url else '.'}"


def dispatch_gpw_workflow(
    phase: str,
    inputs: Optional[Dict[str, str]] = None,
    *,
    env: Optional[StagingEnv] = None,
) -> Dict[str, Any]:
    from bigas.resources.devops.service import dispatch_workflow

    env = _require_staging_env(env)
    workflow = env.workflows[phase]
    client = _github()
    owner, name = _owner_name(env)
    ref = client.get_default_branch(owner, name)
    return dispatch_workflow(repo=env.repo, workflow=workflow, ref=ref, inputs=inputs or None)


def _begin_poll(
    thread_id: Optional[str],
    *,
    env: StagingEnv,
    phase: str,
    run: Dict[str, Any],
    production_sha: str = "",
    candidate_sha: str = "",
) -> Dict[str, Any]:
    _patch(
        thread_id,
        pending_deploy_poll={
            "kind": "gpw",
            "phase": phase,
            "project_key": env.project_key,
            "repo": env.repo,
            "triggered": [
                {
                    "workflow": run.get("workflow") or env.workflows.get(phase) or phase,
                    "run_id": run.get("run_id"),
                    "html_url": run.get("html_url") or "",
                }
            ],
            "production_sha": production_sha,
            "candidate_sha": candidate_sha,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_lines": [],
            "failed_runs": [],
            "done_run_ids": [],
        },
        has_pending_deploy_poll=True,
    )
    url = run.get("html_url") or ""
    run_id = run.get("run_id") or "?"
    _post(
        thread_id,
        f"Workflow `{run.get('workflow') or phase}` run #{run_id} is running"
        + (f" — {url}" if url else ".")
        + "\n\nI'll post here when it finishes.",
        status="in_progress",
    )
    return {"status": "in_progress", "summary": f"{phase} started", "deploy_poll_active": True}


def _start_prepare_staging(thread_id: Optional[str], env: StagingEnv) -> Dict[str, Any]:
    _post(
        thread_id,
        f"Reviewing `{env.candidate_branch}` against `{env.production_branch}` "
        f"for **{env.project_key}** before building staging.",
        status="in_progress",
    )
    try:
        reviewed = review_candidate(env)
    except Exception as exc:
        logger.exception("Prepare-staging review failed for %s", env.project_key)
        _complete_progress(thread_id)
        _post(thread_id, f"Review failed: {exc}")
        return {"status": "complete", "summary": str(exc)}
    if not reviewed.get("ok"):
        reason = (reviewed.get("reason") or "").strip()
        if reason:
            _complete_progress(thread_id)
            _post(thread_id, reason)
            return {"status": "complete", "summary": reason}
        note = ""
        try:
            note = _launch_review_autofix(env, reviewed)
        except Exception as exc:
            logger.exception("Review autofix failed for %s", env.project_key)
            note = f"Could not start autofix ({exc})."
        _complete_progress(thread_id)
        preview = (reviewed.get("review") or "").strip()
        truncated = len(preview) > 1200
        if truncated:
            preview = preview[:1200] + "…"
        pr_url = (reviewed.get("pr_url") or "").strip()
        if pr_url:
            review_link = f"Full review: {pr_url}"
        else:
            owner, name = _owner_name(env)
            compare_url = (
                f"https://github.com/{owner}/{name}/compare/"
                f"{env.production_branch}...{env.candidate_branch}"
            )
            review_link = f"Full diff: {compare_url}"
        _post(
            thread_id,
            "Review is not clean, so staging was not built.\n\n"
            + (preview + "\n\n" if preview else "")
            + f"{review_link}\n\n"
            + note,
        )
        return {"status": "complete", "summary": "Review blocked staging."}
    production_sha = reviewed.get("production_sha") or ""
    candidate_sha = reviewed.get("candidate_sha") or ""
    _set_rehearsal(
        thread_id,
        {
            "project_key": env.project_key,
            "candidate_sha": candidate_sha,
            "production_sha": production_sha,
            "staging_ready": False,
            "updated_ok": False,
            "updated_sha": "",
        },
    )
    try:
        run = dispatch_gpw_workflow(
            "prepare_staging",
            {"production_sha": production_sha, "candidate_sha": candidate_sha},
            env=env,
        )
    except Exception as exc:
        logger.exception("Prepare-staging dispatch failed for %s", env.project_key)
        _complete_progress(thread_id)
        _post(thread_id, f"Could not start prepare-staging: {exc}")
        return {"status": "complete", "summary": str(exc)}
    _post(
        thread_id,
        "Review is clean. Building a full staging environment from production "
        f"`{production_sha[:7]}`, including a copy of the production database.",
    )
    return _begin_poll(
        thread_id,
        env=env,
        phase="prepare_staging",
        run=run,
        production_sha=production_sha,
        candidate_sha=candidate_sha,
    )


def _same_rehearsal(rehearsal: Dict[str, Any], env: StagingEnv) -> bool:
    stored = _normalize_key(str(rehearsal.get("project_key") or ""))
    if stored:
        return stored == env.project_key
    if len(staging_envs()) == 1:
        return True
    return env.project_key == "GPW-PROD"


def _start_update_staging(thread_id: Optional[str], env: StagingEnv) -> Dict[str, Any]:
    rehearsal = _rehearsal(thread_id)
    if not rehearsal.get("staging_ready") or not _same_rehearsal(rehearsal, env):
        _post(
            thread_id,
            f"Staging for **{env.project_key}** is not ready. Run **prepare staging {env.project_key}** "
            "first so the environment matches production, including the database copy.",
        )
        return {"status": "complete", "summary": "Staging not prepared."}
    candidate_sha = rehearsal.get("candidate_sha") or ""
    try:
        client = _github()
        owner, name = _owner_name(env)
        current = client.get_ref_sha(owner, name, env.candidate_branch)
    except Exception as exc:
        _post(thread_id, f"Could not read `{env.candidate_branch}`: {exc}")
        return {"status": "complete", "summary": str(exc)}
    if current != candidate_sha:
        _post(
            thread_id,
            f"`{env.candidate_branch}` moved after the review (`{candidate_sha[:7]}` → `{current[:7]}`). "
            f"Run **prepare staging {env.project_key}** again.",
        )
        return {"status": "complete", "summary": "candidate branch moved"}
    try:
        run = dispatch_gpw_workflow("update_staging", {"image_sha": candidate_sha}, env=env)
    except Exception as exc:
        logger.exception("Update-staging dispatch failed for %s", env.project_key)
        _post(thread_id, f"Could not start update-staging: {exc}")
        return {"status": "complete", "summary": str(exc)}
    _post(
        thread_id,
        f"Updating staging to `{candidate_sha[:7]}`, including migrations. "
        "Maintenance is on until the post-deploy check is green.",
        status="in_progress",
    )
    return _begin_poll(
        thread_id,
        env=env,
        phase="update_staging",
        run=run,
        production_sha=rehearsal.get("production_sha") or "",
        candidate_sha=candidate_sha,
    )


def _ask(
    thread_id: Optional[str],
    action: str,
    *,
    env: StagingEnv,
    candidate_sha: str = "",
) -> Dict[str, Any]:
    _patch(
        thread_id,
        pending_deploy={
            "kind": "gpw",
            "action": action,
            "project_key": env.project_key,
            "candidate_sha": candidate_sha,
        },
    )
    return {"status": "complete", "summary": f"Waiting to confirm {action}."}


def _start_teardown(thread_id: Optional[str], env: StagingEnv) -> Dict[str, Any]:
    where = f" ({env.staging_url})" if env.staging_url else ""
    _post(
        thread_id,
        f"This destroys the staging stack for **{env.project_key}**{where}. "
        "Production is not touched. State and manual secrets stay, "
        f"so the next **prepare staging {env.project_key}** can build it again.\n\n"
        "Reply **yes** to tear staging down.",
    )
    return _ask(thread_id, "teardown", env=env)


def _start_prepare_deploy(thread_id: Optional[str], env: StagingEnv) -> Dict[str, Any]:
    rehearsal = _rehearsal(thread_id)
    if not rehearsal.get("updated_ok") or not _same_rehearsal(rehearsal, env):
        _post(
            thread_id,
            f"There is no green staging rehearsal for **{env.project_key}**. "
            f"Run **prepare staging {env.project_key}**, then **update staging {env.project_key}**, "
            "and log in on staging before preparing a production deploy.",
        )
        return {"status": "complete", "summary": "No rehearsal."}
    candidate_sha = rehearsal.get("updated_sha") or ""
    try:
        client = _github()
        owner, name = _owner_name(env)
        current = client.get_ref_sha(owner, name, env.candidate_branch)
    except Exception as exc:
        _post(thread_id, f"Could not read `{env.candidate_branch}`: {exc}")
        return {"status": "complete", "summary": str(exc)}
    if current != candidate_sha:
        _post(
            thread_id,
            f"`{env.candidate_branch}` moved after the rehearsal "
            f"(`{candidate_sha[:7]}` → `{current[:7]}`). "
            f"Run **update staging {env.project_key}** again before production.",
        )
        return {"status": "complete", "summary": "candidate branch moved"}
    try:
        run = dispatch_gpw_workflow("backup", {"candidate_sha": candidate_sha}, env=env)
    except Exception as exc:
        logger.exception("Production backup dispatch failed for %s", env.project_key)
        _post(thread_id, f"Could not start the production backup: {exc}")
        return {"status": "complete", "summary": str(exc)}
    _post(
        thread_id,
        f"Rehearsal for `{candidate_sha[:7]}` is green. Taking a production database backup "
        "before I ask you to deploy.",
        status="in_progress",
    )
    return _begin_poll(
        thread_id,
        env=env,
        phase="backup",
        run=run,
        production_sha=rehearsal.get("production_sha") or "",
        candidate_sha=candidate_sha,
    )


def _env_from_record(record: Dict[str, Any]) -> Optional[StagingEnv]:
    key = _normalize_key(str(record.get("project_key") or ""))
    if not key:
        return None
    return staging_envs().get(key)


def _confirm(thread_id: Optional[str], pending: Dict[str, Any]) -> Dict[str, Any]:
    action = pending.get("action") or ""
    env = _env_from_record(pending) or _env_from_record(_rehearsal(thread_id))
    if env is None:
        only = list(staging_envs().values())
        env = only[0] if len(only) == 1 else None
    if env is None:
        _patch(thread_id, pending_deploy=None)
        _post(thread_id, "That confirmation is not tied to a staging environment anymore.")
        return {"status": "complete", "summary": "Missing staging environment."}
    _patch(thread_id, pending_deploy=None)
    if action == "teardown":
        try:
            run = dispatch_gpw_workflow("teardown", {"confirm": "yes"}, env=env)
        except Exception as exc:
            _post(thread_id, f"Could not start teardown: {exc}")
            return {"status": "complete", "summary": str(exc)}
        _post(thread_id, "Tearing down staging.", status="in_progress")
        return _begin_poll(thread_id, env=env, phase="teardown", run=run)
    if action == "deploy":
        candidate_sha = pending.get("candidate_sha") or _rehearsal(thread_id).get("updated_sha") or ""
        try:
            run = dispatch_gpw_workflow(
                "deploy_production",
                {"image_sha": candidate_sha},
                env=env,
            )
        except Exception as exc:
            _post(thread_id, f"Could not start the production deploy: {exc}")
            return {"status": "complete", "summary": str(exc)}
        _post(
            thread_id,
            f"Maintenance is on for **{env.project_key}**. Deploying, migrating, then running the "
            "post-deploy check. Maintenance comes off only if that check is green.",
            status="in_progress",
        )
        return _begin_poll(
            thread_id,
            env=env,
            phase="deploy_production",
            run=run,
            candidate_sha=candidate_sha,
            production_sha=_rehearsal(thread_id).get("production_sha") or "",
        )
    _post(thread_id, "Nothing is waiting for a confirmation.")
    return {"status": "complete", "summary": "No pending staging action."}


def run_gpw_pipeline(*, thread_id: Optional[str], user_message: str) -> Dict[str, Any]:
    from bigas.resources.devops.pipeline import is_cancel, is_confirm

    pending = pending_gpw_action(thread_id)
    if pending and is_cancel(user_message):
        _patch(thread_id, pending_deploy=None)
        _complete_progress(thread_id)
        _post(thread_id, "Cancelled. Nothing was changed.")
        return {"status": "complete", "summary": "Cancelled."}
    if pending and is_confirm(user_message):
        return _confirm(thread_id, pending)

    command, project_key = parse_staging_command(user_message)
    if not command:
        names = ", ".join(f"**{key}**" for key in staging_envs()) or "none"
        _post(
            thread_id,
            "Say **prepare staging**, **update staging**, **teardown staging**, "
            f"or **prepare deploy** plus a project. Configured: {names}.",
        )
        return {"status": "complete", "summary": "Unknown staging command."}
    env, error = resolve_staging_env(project_key)
    if error or env is None:
        _post(thread_id, error or "No staging environment.")
        return {"status": "complete", "summary": error or "No staging environment."}
    if command == "prepare_staging":
        return _start_prepare_staging(thread_id, env)
    if command == "update_staging":
        return _start_update_staging(thread_id, env)
    if command == "teardown":
        return _start_teardown(thread_id, env)
    if command == "prepare_deploy":
        return _start_prepare_deploy(thread_id, env)
    return {"status": "complete", "summary": "Unknown staging command."}


def _env_for_poll(poll: Dict[str, Any]) -> Optional[StagingEnv]:
    env = _env_from_record(poll)
    if env is None and len(staging_envs()) == 1:
        env = next(iter(staging_envs().values()))
    if env is None:
        repo = str(poll.get("repo") or "").strip()
        matches = [item for item in staging_envs().values() if item.repo == repo]
        if len(matches) == 1:
            env = matches[0]
    return env


def _env_for_finish(poll: Dict[str, Any], rehearsal: Dict[str, Any]) -> Optional[StagingEnv]:
    env = _env_for_poll(poll)
    if env is None:
        env = _env_from_record(rehearsal)
    return env


def _resolve_missing_gpw_run_id(poll: Dict[str, Any]) -> Optional[int]:
    """Find a workflow run id when dispatch returned before GitHub registered the run."""
    triggered = poll.get("triggered") or []
    item = triggered[0] if triggered else {}
    phase = (poll.get("phase") or "").strip()
    env = _env_for_poll(poll)
    workflow = (item.get("workflow") or (env.workflows.get(phase) if env else "") or "").strip()
    if not workflow or env is None:
        return None
    started = _parse_started(poll.get("started_at") or "")
    slack = timedelta(seconds=30)
    try:
        client = _github()
        owner, name = _owner_name(env)
        branch = client.get_default_branch(owner, name)
        runs = client.list_workflow_runs(owner, name, workflow, branch=branch, limit=10)
    except Exception as exc:
        logger.warning("Could not list workflow runs for %s: %s", workflow, exc)
        return None
    for run in runs:
        created = _parse_started(run.get("created_at") or "")
        if created + slack < started:
            continue
        run_id = run.get("id")
        if run_id:
            return int(run_id)
    return None


def _parse_started(value: str) -> datetime:
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _finish_success(thread_id: str, poll: Dict[str, Any]) -> None:
    phase = poll.get("phase") or ""
    candidate = (poll.get("candidate_sha") or "").strip()
    production = (poll.get("production_sha") or "").strip()
    rehearsal = _rehearsal(thread_id)
    env = _env_for_finish(poll, rehearsal)
    staging_url = env.staging_url if env else ""
    production_url = env.production_url if env else ""
    production_branch = env.production_branch if env else "production"
    project_key = env.project_key if env else str(poll.get("project_key") or "")
    if phase == "prepare_staging":
        rehearsal.update(
            {
                "project_key": project_key or rehearsal.get("project_key") or "",
                "staging_ready": True,
                "production_sha": production or rehearsal.get("production_sha") or "",
                "candidate_sha": candidate or rehearsal.get("candidate_sha") or "",
                "updated_ok": False,
                "updated_sha": "",
            }
        )
        _set_rehearsal(thread_id, rehearsal)
        _post(
            thread_id,
            "Staging is up as a copy of production, including the database. "
            f"New code is not applied yet.\n\n{staging_url}",
        )
        return
    if phase == "update_staging":
        rehearsal.update(
            {
                "project_key": project_key or rehearsal.get("project_key") or "",
                "staging_ready": True,
                "updated_ok": True,
                "updated_sha": candidate or rehearsal.get("candidate_sha") or "",
                "candidate_sha": candidate or rehearsal.get("candidate_sha") or "",
            }
        )
        _set_rehearsal(thread_id, rehearsal)
        _post(
            thread_id,
            "Staging updated and the post-deploy check passed. Maintenance is off.\n\n"
            f"Log in at {staging_url}. The staging admin password is `ADMIN_PASSWORD` "
            "in Secret Manager for the staging project (it is not posted here). "
            "Accounts from the production database copy can also sign in.",
        )
        return
    if phase == "teardown":
        rehearsal.update({"staging_ready": False, "updated_ok": False, "updated_sha": ""})
        _set_rehearsal(thread_id, rehearsal)
        _post(thread_id, "Staging is torn down. Production was not changed.")
        return
    if phase == "backup":
        _post(
            thread_id,
            "Production database backup is done.\n\n"
            f"Deploy `{candidate[:7]}` to {production_url}? "
            "Maintenance goes on at the start and comes off only after a green post-deploy check.\n\n"
            "Reply **yes** to deploy, or **no** to stop.",
        )
        _patch(
            thread_id,
            pending_deploy={
                "kind": "gpw",
                "action": "deploy",
                "project_key": project_key,
                "candidate_sha": candidate,
            },
        )
        return
    if phase == "deploy_production":
        if env is None:
            rehearsal.update({"staging_ready": False, "updated_ok": False, "updated_sha": ""})
            _set_rehearsal(thread_id, rehearsal)
            _post(
                thread_id,
                "Production deploy finished, but the target repository could not be determined "
                "from this poll (missing project key and repo). Rehearsal state was cleared. "
                "Check production manually and verify whether maintenance is still on.",
            )
            return
        note = ""
        try:
            client = _github()
            owner, name = _owner_name(env)
            if candidate:
                client.update_branch_ref(owner, name, production_branch, candidate, force=False)
                note = f"\n\nFast-forwarded `{production_branch}` to `{candidate[:7]}`."
        except Exception as exc:
            logger.exception("Production branch fast-forward failed for %s", project_key)
            note = (
                f"\n\nProduction is updated, but `{production_branch}` was not fast-forwarded: {exc}"
            )
        rehearsal.update({"staging_ready": False, "updated_ok": False, "updated_sha": ""})
        _set_rehearsal(thread_id, rehearsal)
        _post(
            thread_id,
            "Production deploy and post-deploy check passed. Maintenance is off.\n\n"
            f"{production_url}{note}",
        )
        return
    _post(thread_id, "Staging workflow finished.")


def _finish_failure(thread_id: str, poll: Dict[str, Any], failed_runs: list) -> None:
    phase = poll.get("phase") or ""
    lines = [f"The {poll.get('project_key') or 'staging'} workflow failed."]
    for item in failed_runs:
        url = item.get("html_url") or ""
        lines.append(
            f"- {item.get('workflow') or phase} #{item.get('run_id')} "
            f"{item.get('conclusion') or 'failure'}"
            + (f" — {url}" if url else "")
        )
    if phase in {"deploy_production", "update_staging"}:
        lines.append(
            "Maintenance stays on because the post-deploy check did not pass."
            if phase == "deploy_production"
            else "Staging maintenance stays on because the post-deploy check did not pass."
        )
    _post(thread_id, "\n".join(lines))
    if phase not in _AUTOFIX_PHASES:
        return
    env = _env_for_finish(poll, _rehearsal(thread_id))
    repo = (poll.get("repo") or (env.repo if env else "")).strip()
    branch = env.candidate_branch if env else "develop"
    project_key = env.project_key if env else str(poll.get("project_key") or "this project")
    if not repo:
        _post(thread_id, "Could not start autofix because the staging environment is unknown.")
        return
    try:
        from bigas.resources.cto.deploy_hotfix import launch_failed_deploy_fix
        from bigas.resources.devops.service import get_failed_run_excerpt

        enriched = []
        for item in failed_runs:
            row = dict(item)
            run_id = item.get("run_id")
            if run_id and not row.get("excerpt"):
                try:
                    fetched = get_failed_run_excerpt(repo=repo, run_id=int(run_id))
                    row["excerpt"] = (fetched.get("excerpt") or "")[:2500]
                except Exception as exc:
                    row["excerpt"] = f"(could not fetch logs: {exc})"
            enriched.append(row)
        launched = launch_failed_deploy_fix(
            repo=repo,
            failures=enriched or failed_runs,
            starting_ref=branch,
            extra_instructions=(
                f"This is {project_key}. "
                "Do not deploy, do not run terraform against production, and do not disable "
                f"maintenance mode. Open a pull request against {branch}."
            ),
        )
    except Exception as exc:
        logger.exception("Staging autofix launch failed")
        _post(thread_id, f"Could not start autofix ({exc}).")
        return
    url = (launched.get("agent_url") or "").strip()
    _post(
        thread_id,
        f"Autofix is looking at the failure and will open a PR on `{branch}`"
        + (f": {url}" if url else "."),
    )


def poll_gpw(thread_id: str) -> Dict[str, Any]:
    """One client-driven poll step for a GPW workflow run."""
    from bigas.resources.devops.service import get_deployment_status

    thread = _thread(thread_id)
    poll = thread.get("pending_deploy_poll")
    if not isinstance(poll, dict) or poll.get("kind") != "gpw":
        return {"status": "complete", "active": False}
    triggered = poll.get("triggered") or [{}]
    if not triggered[0].get("run_id"):
        resolved = _resolve_missing_gpw_run_id(poll)
        if resolved:
            item = dict(triggered[0])
            item["run_id"] = resolved
            if not item.get("workflow"):
                phase = (poll.get("phase") or "").strip()
                env = _env_for_poll(poll)
                item["workflow"] = (env.workflows.get(phase) if env else "") or phase
            triggered = [item]
            poll = {**poll, "triggered": triggered}
            _patch(thread_id, pending_deploy_poll=poll)
        else:
            started = _parse_started(poll.get("started_at") or "")
            if datetime.now(timezone.utc) < started + timedelta(seconds=_POLL_TIMEOUT_SEC):
                return {"status": "in_progress", "active": True}
            _complete_progress(thread_id)
            _patch(thread_id, pending_deploy_poll=None, has_pending_deploy_poll=False)
            _post(thread_id, "No GitHub Actions run id came back. Check the Actions tab.")
            return {"status": "complete", "active": False}

    item = triggered[0]
    run_id = int(item["run_id"])
    started = _parse_started(poll.get("started_at") or "")
    if datetime.now(timezone.utc) >= started + timedelta(seconds=_POLL_TIMEOUT_SEC):
        _complete_progress(thread_id)
        _patch(thread_id, pending_deploy_poll=None, has_pending_deploy_poll=False)
        _post(thread_id, f"Timed out waiting for run #{run_id}. Check the Actions tab.")
        return {"status": "complete", "active": False}
    try:
        status = get_deployment_status(repo=poll.get("repo") or "", run_id=run_id)
    except Exception as exc:
        logger.warning("Staging workflow poll failed for run %s: %s", run_id, exc)
        return {"status": "in_progress", "active": True}
    if (status.get("workflow_status") or "").lower() != "completed":
        return {"status": "in_progress", "active": True}

    conclusion = (status.get("conclusion") or "unknown").lower()
    _complete_progress(thread_id)
    _patch(thread_id, pending_deploy_poll=None, has_pending_deploy_poll=False)
    if conclusion == "success":
        _finish_success(thread_id, poll)
    else:
        failed = [
            {
                "workflow": item.get("workflow") or poll.get("phase"),
                "run_id": run_id,
                "conclusion": conclusion,
                "html_url": status.get("html_url") or item.get("html_url") or "",
            }
        ]
        _finish_failure(thread_id, poll, failed)
    return {"status": "complete", "active": False}
