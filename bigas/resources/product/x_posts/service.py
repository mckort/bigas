"""Generate weekly X post drafts from git activity."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from bigas.llm.factory import get_llm_client
from bigas.providers.notifications.x import (
    TWEET_MAX_CHARS,
    XProvider,
    clamp_tweet,
    load_account_credentials,
    parse_account_names,
)
from bigas.resources.product.create_release_notes.jira_client import normalize_project_keys
from bigas.resources.product.progress_updates.github_commits import (
    fetch_commits_for_projects,
    format_commits_for_prompt,
    project_repo_map_from_env,
)
from bigas.resources.product.x_posts.drafts import (
    DEFAULT_TTL_HOURS,
    DraftStore,
    GcsDraftStore,
    is_expired,
)
from bigas.resources.product.x_posts.prompts import (
    X_POSTS_SYSTEM_PROMPT,
    build_x_posts_user_prompt,
)
from bigas.resources.product.x_posts.signing import sign_draft_id, signing_secret

logger = logging.getLogger(__name__)

MAX_THREAD_TWEETS = 5


class XPostsError(RuntimeError):
    pass


def _extract_json(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    if not t:
        return {}
    if "```" in t:
        if "```json" in t:
            t = t.split("```json", 1)[1].split("```", 1)[0].strip()
        else:
            t = t.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        parsed = json.loads(t)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        start = t.find("{")
        end = t.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(t[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.warning("Failed to parse X-post LLM JSON")
            return {}


def public_base_url(request_base: Optional[str] = None) -> str:
    env = (
        (os.environ.get("BIGAS_PUBLIC_URL") or "").strip()
        or (os.environ.get("SERVER_URL") or "").strip()
    )
    return (env or (request_base or "")).rstrip("/")


def review_url(draft_id: str, *, base_url: str, token: str) -> str:
    query = urlencode({"token": token})
    return f"{base_url.rstrip('/')}/api/x-posts/{draft_id}?{query}"


def default_draft_store() -> DraftStore:
    return GcsDraftStore()


def _normalize_tweets(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    tweets: List[str] = []
    for item in raw:
        text = clamp_tweet(str(item or ""))
        if text:
            tweets.append(text)
        if len(tweets) >= MAX_THREAD_TWEETS:
            break
    return tweets


def _normalize_edited_tweets(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    tweets: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > TWEET_MAX_CHARS:
            raise XPostsError(f"Tweet exceeds {TWEET_MAX_CHARS} characters")
        tweets.append(text)
        if len(tweets) >= MAX_THREAD_TWEETS:
            break
    return tweets


def _ttl_hours() -> int:
    raw = (os.environ.get("X_POST_DRAFT_TTL_HOURS") or "").strip()
    if not raw:
        return DEFAULT_TTL_HOURS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_TTL_HOURS


class XPostsService:
    def __init__(
        self,
        *,
        x_provider: Optional[XProvider] = None,
        draft_store: Optional[DraftStore] = None,
        openai_model: Optional[str] = None,
        github_token: Optional[str] = None,
    ) -> None:
        self._x = x_provider if x_provider is not None else XProvider()
        self._store = draft_store
        self._explicit_model = openai_model
        self._github_token = (
            (github_token or "").strip()
            or (os.environ.get("GITHUB_TOKEN") or "").strip()
            or None
        )
        self._llm = None
        self._model = ""

    def _llm_client(self):
        if self._llm is None:
            self._llm, self._model = get_llm_client(
                feature="x_posts",
                explicit_model=self._explicit_model,
            )
        return self._llm

    def _store_or_default(self) -> DraftStore:
        if self._store is None:
            self._store = default_draft_store()
        return self._store

    def generate(
        self,
        *,
        days: int = 7,
        accounts: Optional[List[str]] = None,
        project_keys: Optional[Any] = None,
        public_url: Optional[str] = None,
        dry_run: bool = False,
        tweets: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if days < 1 or days > 365:
            raise XPostsError("days must be between 1 and 365")

        requested = [a.strip().lstrip("@") for a in (accounts or []) if str(a).strip()]
        configured = self._x.configured_accounts()
        if requested:
            missing = [a for a in requested if a.lower() not in {c.lower() for c in configured}]
            if missing:
                raise XPostsError(
                    "No X credentials for account(s): "
                    + ", ".join(missing)
                    + f" (configured: {', '.join(configured) or 'none'})"
                )
            target_accounts = requested
        else:
            target_accounts = configured
        if not target_accounts:
            raise XPostsError(
                "X posting is not configured. Set X_ACCOUNTS and per-account "
                "credentials (or X_CREDENTIALS_JSON)."
            )

        forced_tweets = None if tweets is None else _normalize_tweets(tweets)
        if tweets is not None and not forced_tweets:
            raise XPostsError("tweets must contain at least one non-empty string")

        git_stats: Dict[str, Any] = {}
        git_errors: List[Any] = []
        if forced_tweets is not None:
            skip = False
            reason = "Manual tweet override"
            drafted = forced_tweets
            model_name = "manual"
        else:
            resolved_keys = normalize_project_keys(project_keys)
            if not resolved_keys:
                resolved_keys = list(project_repo_map_from_env().keys())
            git_payload = fetch_commits_for_projects(
                project_keys=resolved_keys,
                days=days,
                token=self._github_token,
            )
            git_stats = git_payload.get("stats") or {}
            git_errors = git_payload.get("errors") or []
            git_commits_text = format_commits_for_prompt(
                git_payload.get("by_project") or {},
                stats=git_stats,
            )

            user_prompt = build_x_posts_user_prompt(
                days=days,
                git_commits_text=git_commits_text,
                git_stats=git_stats,
            )
            try:
                content = self._llm_client().complete(
                    messages=[
                        {"role": "system", "content": X_POSTS_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=1200,
                    temperature=0.4,
                )
            except Exception as e:
                raise XPostsError(f"LLM request failed: {e}") from e

            parsed = _extract_json(content)
            skip = bool(parsed.get("skip"))
            reason = str(parsed.get("reason") or "").strip()
            drafted = _normalize_tweets(parsed.get("tweets"))
            if not drafted and not skip:
                skip = True
                reason = reason or "No newsworthy user-facing changes this period."
            model_name = self._model

        result: Dict[str, Any] = {
            "ok": True,
            "skip": skip,
            "reason": reason,
            "tweets": drafted,
            "accounts": target_accounts,
            "days": days,
            "model": model_name,
            "git_stats": git_stats,
            "git_errors": git_errors,
        }
        if skip or dry_run:
            return result

        secret = signing_secret()
        if not secret:
            raise XPostsError(
                "Set X_POST_SIGNING_SECRET (or JIRA_AUTOMATION_WEBHOOK_SECRET) "
                "so approval links can be signed."
            )
        draft_id = str(uuid.uuid4())
        payload = {
            "id": draft_id,
            "accounts": target_accounts,
            "tweets": drafted,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "days": days,
            "reason": reason,
        }
        self._store_or_default().save(draft_id, payload)
        token = sign_draft_id(draft_id, secret=secret)
        base = public_base_url(public_url)
        result["draft_id"] = draft_id
        result["review_url"] = review_url(draft_id, base_url=base, token=token) if base else ""
        result["expires_hours"] = _ttl_hours()
        return result

    def load_draft(self, draft_id: str) -> Dict[str, Any]:
        payload = self._store_or_default().load(draft_id)
        if not payload:
            raise XPostsError("Draft not found")
        if is_expired(payload, ttl_hours=_ttl_hours()):
            self._store_or_default().delete(draft_id)
            raise XPostsError("Draft expired")
        return payload

    def approve(self, draft_id: str, tweets: Optional[List[str]] = None) -> Dict[str, Any]:
        payload = self.load_draft(draft_id)
        if tweets is None:
            tweets = _normalize_tweets(payload.get("tweets"))
        else:
            tweets = _normalize_edited_tweets(tweets)
            if not tweets:
                raise XPostsError("Add at least one tweet before approving")
        accounts = [str(a).strip().lstrip("@") for a in (payload.get("accounts") or []) if str(a).strip()]
        if not tweets or not accounts:
            self._store_or_default().delete(draft_id)
            raise XPostsError("Draft is missing tweets or accounts")
        # Remove the draft before calling X so a partial API failure cannot be
        # retried from the start (which would duplicate already-posted tweets).
        self._store_or_default().delete(draft_id)
        posted: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for account in accounts:
            try:
                ids = self._x.post_thread(account, tweets)
            except Exception as e:
                entry: Dict[str, Any] = {"account": account, "error": str(e)}
                partial_ids = getattr(e, "posted_ids", None)
                if partial_ids:
                    entry["posted_tweet_ids"] = list(partial_ids)
                    entry["posted_urls"] = [
                        f"https://x.com/{account}/status/{tid}" for tid in partial_ids
                    ]
                failed.append(entry)
                logger.error("Failed to post X thread for account %s", account, exc_info=True)
                continue
            posted.append(
                {
                    "account": account,
                    "tweet_ids": ids,
                    "urls": [f"https://x.com/{account}/status/{tid}" for tid in ids],
                }
            )
        return {"ok": not failed, "posted": posted, "failed": failed}

    def cleanup_expired_drafts(self, *, max_to_delete: int = 50) -> int:
        return int(
            self._store_or_default().cleanup_expired(
                ttl_hours=_ttl_hours(),
                max_to_delete=max_to_delete,
            )
            or 0
        )

    def decline(self, draft_id: str) -> Dict[str, Any]:
        payload = self._store_or_default().load(draft_id)
        if not payload:
            raise XPostsError("Draft not found")
        self._store_or_default().delete(draft_id)
        return {"ok": True, "declined": True, "draft_id": draft_id}


def format_discord_message(result: Dict[str, Any]) -> str:
    if result.get("skip"):
        reason = result.get("reason") or "Nothing newsworthy this period."
        return (
            "**Weekly X post skipped**\n"
            f"{reason}\n"
            "No draft was stored."
        )
    accounts = ", ".join(f"`{a}`" for a in (result.get("accounts") or []))
    tweets = result.get("tweets") or []
    lines = [
        "**New X post draft ready for approval**",
        f"**Accounts:** {accounts or '(none)'}",
        "",
        "**Draft:**",
    ]
    for i, tweet in enumerate(tweets, start=1):
        prefix = f"{i}/ " if len(tweets) > 1 else ""
        lines.append(prefix + tweet)
        lines.append("")
    review_url_value = (result.get("review_url") or "").strip()
    if review_url_value:
        lines.append(f"👉 [Review, then Approve or Decline]({review_url_value})")
    hours = result.get("expires_hours") or DEFAULT_TTL_HOURS
    lines.append(
        f"Draft expires in {hours} hours. Decline deletes the GCS draft; "
        "you can still copy the text above to post manually."
    )
    return "\n".join(lines).strip()


# Keep a module-level helper used by tests without constructing the full service.
def configured_x_accounts() -> List[str]:
    return [c.account for c in load_account_credentials().values()] or parse_account_names()
