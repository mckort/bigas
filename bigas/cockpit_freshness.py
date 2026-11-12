"""Git check for the ship card: is there something new since the last deploy?

The cockpit refreshes every 15 seconds, so results are cached briefly. No
GitHub token, or a failed lookup, leaves ``ahead`` unset. The card then stays
grey unless the board already has a cut or a deploy confirm waiting.

When the compare already lists commits, those subjects come back as
``pending`` so a development or staging card can name what the next deploy
ships. A failed compare still reports ``ahead`` and an empty list.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TTL_SEC = 90
_PENDING_CAP = 20
_lock = threading.Lock()
_cache: Dict[str, tuple] = {}
_SEMVER_TAG = re.compile(r"^v?\d+\.\d+\.\d+")


def reset_cache_for_tests() -> None:
    with _lock:
        _cache.clear()


def lookup_deploy_freshness(specs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """``{PROJECT: {ahead, latest, pending, pending_count}}``.

    ``pending`` is the newest commit subjects that are not deployed yet,
    capped, with ``pending_count`` the full number when GitHub reports more.
    """
    if not specs:
        return {}
    client = _client()
    if client is None:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    missing: List[Dict[str, Any]] = []
    for spec in specs:
        key = (spec.get("key") or "").upper()
        if not key:
            continue
        cached = _cache_get(_cache_key(spec))
        if cached is None:
            missing.append(spec)
        else:
            out[key] = cached
    if not missing:
        return out

    def _one(spec: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return fetch_deploy_freshness(client, spec)
        except Exception:
            logger.warning("Deploy freshness failed for %s", spec.get("key"), exc_info=True)
            return {"ahead": None, "latest": None}

    pool = ThreadPoolExecutor(max_workers=min(8, len(missing)))
    try:
        futures = {pool.submit(_one, spec): spec for spec in missing}
        for future in as_completed(futures, timeout=8):
            spec = futures[future]
            key = (spec.get("key") or "").upper()
            try:
                value = future.result()
            except Exception:
                logger.warning("Deploy freshness failed for %s", key, exc_info=True)
                value = {"ahead": None, "latest": None}
            if not isinstance(value, dict):
                value = {"ahead": None, "latest": None}
            _cache_set(_cache_key(spec), value)
            out[key] = value
    except FuturesTimeout:
        logger.warning("Deploy freshness timed out; ship cards keep the board signal")
    finally:
        # A context-manager shutdown waits for running workers. A hung GitHub
        # call would then stall the 15-second cockpit poll past this timeout.
        pool.shutdown(wait=False, cancel_futures=True)
    for spec in missing:
        key = (spec.get("key") or "").upper()
        if key in out:
            continue
        value = {"ahead": None, "latest": None}
        _cache_set(_cache_key(spec), value)
        out[key] = value
    return out


def _commit_subject(message: str) -> str:
    """First line of a commit, or the pull-request title under a merge subject."""
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    if not lines:
        return ""
    first = lines[0]
    if first.lower().startswith("merge ") and len(lines) > 1:
        return lines[1]
    return first


def pending_from_compare(compare: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Newest-first subjects from a GitHub compare.

    ``{"items": [{key, title, url}], "count": n}``. ``count`` is the number
    of commits GitHub says are ahead when that is larger than the page, so
    the card can say how many more there are. Empty when nothing has a subject.
    """
    if not isinstance(compare, dict):
        return {"items": [], "count": 0}
    commits = compare.get("commits") if isinstance(compare.get("commits"), list) else []
    items: List[Dict[str, str]] = []
    named = 0
    for raw in reversed(commits):
        if not isinstance(raw, dict):
            continue
        commit = raw.get("commit") if isinstance(raw.get("commit"), dict) else {}
        title = _commit_subject(str(commit.get("message") or ""))
        if not title:
            continue
        named += 1
        if len(items) >= _PENDING_CAP:
            continue
        items.append(
            {
                "key": "",
                "title": title[:180],
                "url": str(raw.get("html_url") or "").strip(),
            }
        )
    try:
        ahead = int(compare.get("ahead_by") or 0)
    except (TypeError, ValueError):
        ahead = 0
    try:
        total = int(compare.get("total_commits") or 0)
    except (TypeError, ValueError):
        total = 0
    if not items:
        return {"items": [], "count": 0}
    count = max(ahead, total, named, len(items))
    return {"items": items, "count": count}


def _pending_tuple(compare: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], int]:
    pending = pending_from_compare(compare)
    return list(pending["items"]), int(pending["count"])


def fetch_deploy_freshness(client: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    """One project's ahead-of-deploy flag, pending commits, and a release when needed."""
    mode = spec.get("mode") or ""
    latest = None
    repo = ""
    if mode == "staging":
        ahead, repo, pending, count = _staging_ahead(client, (spec.get("key") or "").upper())
    else:
        ahead, repo, pending, count = _app_ahead(client, spec)
    if spec.get("need_latest") and repo:
        latest = latest_github_release(client, repo)
    return {"ahead": ahead, "latest": latest, "pending": pending, "pending_count": count}


def latest_github_release(client: Any, repo: str) -> Optional[Dict[str, str]]:
    """Newest non-draft semver release, or None."""
    from bigas.resources.devops.config import parse_repo

    try:
        owner, name = parse_repo(repo)
        releases = client.list_releases(owner, name, limit=20)
    except Exception:
        logger.warning("Could not list releases for %s", repo, exc_info=True)
        return None
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name") or "").strip()
        if not tag or not _SEMVER_TAG.match(tag):
            continue
        label = tag if tag.lower().startswith("v") else f"v{tag}"
        return {
            "version": label,
            "tag": tag,
            "released_at": str(rel.get("published_at") or rel.get("created_at") or "").strip(),
        }
    return None


def newest_successful_sha(runs: List[Dict[str, Any]]) -> Tuple[str, str]:
    """Sha and timestamp of the newest successful run. GitHub lists newest first.

    The timestamp is that run's own ``updated_at`` (or ``created_at``). A failed
    run that shares the sha is ignored, so it cannot win a comparison across
    workflows. ``("", "")`` when nothing succeeded.
    """
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        if (run.get("conclusion") or "").lower() != "success":
            continue
        sha = str(run.get("head_sha") or "").strip()
        at = str(run.get("updated_at") or run.get("created_at") or "")
        return sha, at
    return "", ""


def _app_ahead(client: Any, spec: Dict[str, Any]) -> tuple:
    from bigas.resources.devops.config import parse_repo, resolve_deploy_target
    from bigas.resources.product.jira_automation.config import JiraAutomationConfig

    key = (spec.get("key") or "").upper()
    target = resolve_deploy_target(project_key=key)
    if not target:
        return None, "", [], 0
    try:
        owner, name = parse_repo(target.repo)
    except ValueError:
        return None, "", [], 0
    try:
        production = (JiraAutomationConfig.from_env().base_branch_for_repo(target.repo) or "main").strip()
    except Exception:
        logger.warning("Could not read the production branch for %s", key, exc_info=True)
        production = "main"
    version = str(spec.get("version") or "").strip()
    if (spec.get("release_mode") or "production") != "development" and version:
        ahead, pending, count = _version_branch_ahead(
            client,
            owner,
            name,
            production,
            str(spec.get("release_prefix") or "staging"),
            version,
        )
        return ahead, target.repo, pending, count
    if (target.dispatch_repo or target.repo).lower() != target.repo.lower():
        ahead, pending, count = _tag_ahead(client, owner, name, production)
        return ahead, target.repo, pending, count
    ahead, pending, count = _workflow_ahead(
        client, owner, name, list(target.workflows or []), production
    )
    return ahead, target.repo, pending, count


def _version_branch_ahead(
    client: Any,
    owner: str,
    name: str,
    production: str,
    prefix: str,
    version: str,
) -> Tuple[Optional[bool], List[Dict[str, str]], int]:
    from bigas.resources.devops.prepare import compare_ahead_count
    from bigas.resources.product.release_workflow import versioned_feature_branch

    head = versioned_feature_branch(prefix or "staging", version)
    if not head or head == production:
        return None, [], 0
    try:
        if not client.branch_exists(owner, name, head):
            return False, [], 0
        compare = client.compare_refs(owner, name, production, head)
    except Exception:
        logger.warning("Could not compare %s...%s on %s/%s", production, head, owner, name, exc_info=True)
        return None, [], 0
    if compare_ahead_count(compare) <= 0:
        return False, [], 0
    pending, count = _pending_tuple(compare)
    return True, pending, count


def _workflow_ahead(
    client: Any,
    owner: str,
    name: str,
    workflows: List[str],
    production: str,
) -> Tuple[Optional[bool], List[Dict[str, str]], int]:
    """True when the production branch is not the commit of the latest successful deploy.

    The commits since that successful run are the next deploy. A compare that
    fails leaves the flag and an empty list.
    """
    names = [item for item in workflows if item and "eas-preview" not in item]
    if not names:
        return None, [], 0
    newest_at = ""
    newest_sha = ""
    saw = False
    for workflow in names:
        try:
            runs = client.list_workflow_runs(owner, name, workflow, branch=production, limit=10)
        except Exception:
            logger.warning("Could not list %s runs for %s/%s", workflow, owner, name, exc_info=True)
            continue
        saw = True
        sha, at = newest_successful_sha(runs if isinstance(runs, list) else [])
        if not sha:
            continue
        if at >= newest_at:
            newest_at = at
            newest_sha = sha
    if not saw:
        return None, [], 0
    if not newest_sha:
        return True, [], 0
    try:
        branch_sha = client.get_ref_sha(owner, name, production)
    except Exception:
        logger.warning("Could not read %s on %s/%s", production, owner, name, exc_info=True)
        return None, [], 0
    if not branch_sha or branch_sha == newest_sha:
        return False, [], 0
    try:
        compare = client.compare_refs(owner, name, newest_sha, production)
    except Exception:
        logger.warning(
            "Could not list commits since the last deploy of %s/%s", owner, name, exc_info=True
        )
        return True, [], 0
    pending, count = _pending_tuple(compare)
    return True, pending, count


def _tag_ahead(
    client: Any, owner: str, name: str, production: str
) -> Tuple[Optional[bool], List[Dict[str, str]], int]:
    """App repo whose deploy workflow lives in another repo: compare to the latest version tag."""
    from bigas.resources.devops.prepare import compare_ahead_count

    latest = latest_github_release(client, f"{owner}/{name}")
    if not latest:
        return None, [], 0
    ref = str(latest.get("tag") or latest.get("version") or "").strip()
    if not ref:
        return None, [], 0
    try:
        compare = client.compare_refs(owner, name, ref, production)
    except Exception:
        logger.warning("Could not compare %s to %s", ref, production, exc_info=True)
        return None, [], 0
    if compare_ahead_count(compare) <= 0:
        return False, [], 0
    pending, count = _pending_tuple(compare)
    return True, pending, count


def _staging_ahead(client: Any, key: str) -> tuple:
    from bigas.resources.devops.gpw_pipeline import staging_envs
    from bigas.resources.devops.prepare import compare_ahead_count

    env = staging_envs().get(key)
    if not env or "/" not in (getattr(env, "repo", "") or ""):
        return None, "", [], 0
    owner, name = env.repo.split("/", 1)
    try:
        compare = client.compare_refs(owner, name, env.production_branch, env.candidate_branch)
    except Exception:
        logger.warning("Could not compare staging for %s", key, exc_info=True)
        return None, env.repo, [], 0
    if compare_ahead_count(compare) <= 0:
        return False, env.repo, [], 0
    pending, count = _pending_tuple(compare)
    return True, env.repo, pending, count


def _client() -> Any:
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        return None
    from bigas.resources.devops.github_actions import GitHubActionsClient

    try:
        return GitHubActionsClient(token)
    except ValueError:
        return None


def _cache_key(spec: Dict[str, Any]) -> str:
    return "|".join(
        [
            (spec.get("key") or "").upper(),
            str(spec.get("mode") or ""),
            str(spec.get("release_mode") or ""),
            str(spec.get("version") or ""),
            "1" if spec.get("need_latest") else "0",
        ]
    )


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    with _lock:
        hit = _cache.get(key)
        if not hit:
            return None
        stored_at, value = hit
        if time.monotonic() - stored_at > _TTL_SEC:
            return None
        return value


def _cache_set(key: str, value: Dict[str, Any]) -> None:
    with _lock:
        _cache[key] = (time.monotonic(), value)
