"""Generate weekly X post drafts from git activity, the internal board, and Jira."""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
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
    format_jira_feature_commits,
    jira_feature_commits,
    jira_issue_key_in_subject,
    project_repo_map_from_env,
)
from bigas.resources.product.x_posts.accounts import resolve_account_projects
from bigas.resources.product.x_posts.drafts import (
    DEFAULT_TTL_HOURS,
    DraftStore,
    GcsDraftStore,
    is_expired,
)
from bigas.resources.product.x_posts.prompts import (
    X_POSTS_SYSTEM_PROMPT,
    build_x_posts_user_prompt,
    product_label_for_project_keys,
)
from bigas.resources.product.x_posts.signing import sign_draft_id, signing_secret

logger = logging.getLogger(__name__)

MAX_THREAD_TWEETS = 5
_LEADING_SEPARATORS_RE = re.compile(r"^[\s:\-–—|/]+")
_MULTI_SPACE_RE = re.compile(r"\s+")
_EMPTY_BRACKETS_RE = re.compile(r"\(\s*\)|\[\s*\]")


class XPostsError(RuntimeError):
    pass


def _public_feature_summary(subject: str) -> str:
    """Commit subject without the Jira key, for public tweets."""
    text = str(subject or "").strip()
    key = jira_issue_key_in_subject(text)
    if key:
        pattern = rf"\b{re.escape(key)}\b"
        text = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE).strip()
    text = _EMPTY_BRACKETS_RE.sub("", text)
    text = _LEADING_SEPARATORS_RE.sub("", text)
    return _MULTI_SPACE_RE.sub(" ", text).strip()


def _tweet_for_product(label: str, items: Sequence[str]) -> str:
    body = "; ".join(item for item in items if item)
    if not body:
        return ""
    name = (label or "").strip()
    if name and name != "the product":
        return f"{name}: {body}"
    return body


def fallback_tweets_from_jira_features(
    features: Sequence[Dict[str, Any]],
) -> Tuple[List[str], List[str]]:
    """Deterministic tweets when the model skips a week that shipped Jira features."""
    by_product: Dict[str, List[str]] = {}
    newsworthy: List[str] = []
    seen_news = set()
    for feat in features or []:
        if not isinstance(feat, dict):
            continue
        key = str(feat.get("jira_key") or "").strip() or (
            jira_issue_key_in_subject(str(feat.get("subject") or "")) or ""
        )
        project = str(feat.get("project_key") or "").strip().upper()
        if not project and key:
            project = key.split("-", 1)[0].upper()
        summary = _public_feature_summary(str(feat.get("subject") or ""))
        if not summary:
            continue
        folded = summary.casefold()
        if folded not in seen_news:
            seen_news.add(folded)
            newsworthy.append(summary)
        label = product_label_for_project_keys([project] if project else [])
        bucket = by_product.setdefault(label, [])
        if summary not in bucket:
            bucket.append(summary)

    tweets: List[str] = []
    for label, items in by_product.items():
        if len(tweets) >= MAX_THREAD_TWEETS:
            break
        remaining = list(items)
        while remaining and len(tweets) < MAX_THREAD_TWEETS:
            chunk: List[str] = []
            while remaining:
                candidate_items = chunk + [remaining[0]]
                candidate = _tweet_for_product(label, candidate_items)
                if not chunk or len(candidate) <= TWEET_MAX_CHARS:
                    chunk = candidate_items
                    remaining.pop(0)
                    if len(candidate) > TWEET_MAX_CHARS:
                        break
                else:
                    break
            text = clamp_tweet(_tweet_for_product(label, chunk))
            if text:
                tweets.append(text)
    return tweets[:MAX_THREAD_TWEETS], newsworthy


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


def _normalize_newsworthy(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    items: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            items.append(text)
        if len(items) >= 10:
            break
    return items


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


def _account_name(value: Any) -> str:
    return str(value or "").strip().lstrip("@")


def _done_issue_from_raw(issue: Dict[str, Any], *, source: str) -> Dict[str, Any]:
    fields = issue.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    key = str(issue.get("key") or "").strip()
    project = str((fields.get("project") or {}).get("key") or "").strip().upper()
    if not project and "-" in key:
        project = key.split("-", 1)[0].upper()
    issuetype = fields.get("issuetype") or {}
    type_name = issuetype.get("name") if isinstance(issuetype, dict) else ""
    return {
        "key": key,
        "project_key": project,
        "summary": str(fields.get("summary") or "").strip(),
        "issue_type": str(type_name or "Task"),
        "source": source,
        "sources": [source],
    }


def fetch_done_issues_for_projects(
    *,
    days: int,
    project_keys: Sequence[str],
) -> List[Dict[str, Any]]:
    """Done tickets from the internal board, plus Jira when credentials exist."""
    keys = [str(k).strip().upper() for k in (project_keys or []) if str(k).strip()]
    by_key: Dict[str, Dict[str, Any]] = {}

    try:
        from bigas.tickets.jira_adapter import TicketJiraAdapter

        adapter = TicketJiraAdapter()
        for raw in adapter.search_issues_done_in_last_n_days(
            days=days,
            project_keys=keys or None,
        ):
            item = _done_issue_from_raw(raw, source="board")
            issue_key = (item.get("key") or "").upper()
            if issue_key:
                by_key[issue_key] = item
    except Exception:
        logger.warning("Could not load Done tickets from the internal board", exc_info=True)

    try:
        from bigas.tickets.config import jira_configured
    except Exception:
        jira_configured = lambda: False  # noqa: E731

    if jira_configured():
        try:
            from bigas.resources.product.create_release_notes.jira_client import (
                JiraClient,
                JiraConfig,
            )

            client = JiraClient(JiraConfig.from_env())
            for raw in client.search_issues_done_in_last_n_days(
                days=days,
                project_keys=keys or None,
            ):
                item = _done_issue_from_raw(raw, source="jira")
                issue_key = (item.get("key") or "").upper()
                if not issue_key:
                    continue
                existing = by_key.get(issue_key)
                if existing:
                    sources = list(existing.get("sources") or [existing.get("source") or "board"])
                    if "jira" not in sources:
                        sources.append("jira")
                    existing["sources"] = sources
                else:
                    by_key[issue_key] = item
        except Exception:
            logger.warning("Could not load Done issues from Jira", exc_info=True)

    return list(by_key.values())


def format_done_issues_for_x_prompt(issues: Sequence[Dict[str, Any]]) -> str:
    if not issues:
        return ""
    lines = ["Shipped tickets from the internal board and Jira (deduped by key):"]
    for item in issues:
        key = str(item.get("key") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not key and not summary:
            continue
        lines.append(f"- {key}: {summary}".strip(": ").strip())
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _features_from_done_issues(issues: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in issues or []:
        summary = str(item.get("summary") or "").strip()
        key = str(item.get("key") or "").strip()
        if not summary:
            continue
        out.append(
            {
                "project_key": str(item.get("project_key") or "").strip().upper(),
                "jira_key": key,
                "subject": f"{key}: {summary}" if key else summary,
            }
        )
    return out


def _slice_git_payload(git_payload: Dict[str, Any], project_keys: Sequence[str]) -> Dict[str, Any]:
    wanted = {str(k).strip().upper() for k in project_keys if str(k).strip()}
    by_project = git_payload.get("by_project") or {}
    stats = git_payload.get("stats") or {}
    if not isinstance(by_project, dict):
        by_project = {}
    if not isinstance(stats, dict):
        stats = {}
    return {
        "by_project": {
            k: v for k, v in by_project.items() if str(k).strip().upper() in wanted
        },
        "stats": {k: v for k, v in stats.items() if str(k).strip().upper() in wanted},
        "errors": list(git_payload.get("errors") or []),
    }


def _filter_done_issues(
    issues: Sequence[Dict[str, Any]],
    project_keys: Sequence[str],
) -> List[Dict[str, Any]]:
    wanted = {str(k).strip().upper() for k in project_keys if str(k).strip()}
    out: List[Dict[str, Any]] = []
    for item in issues or []:
        project = str(item.get("project_key") or "").strip().upper()
        key = str(item.get("key") or "").strip().upper()
        if not project and "-" in key:
            project = key.split("-", 1)[0]
        if wanted and project not in wanted:
            continue
        out.append(item)
    return out


def draft_posts(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize stored drafts to a list of per-account posts."""
    raw_posts = payload.get("posts")
    if isinstance(raw_posts, list) and raw_posts:
        posts: List[Dict[str, Any]] = []
        for item in raw_posts:
            if not isinstance(item, dict):
                continue
            account = _account_name(item.get("account"))
            tweets = _normalize_tweets(item.get("tweets"))
            if account and tweets:
                posts.append(
                    {
                        "account": account,
                        "tweets": tweets,
                        "project_keys": [
                            str(k).strip().upper()
                            for k in (item.get("project_keys") or [])
                            if str(k).strip()
                        ],
                    }
                )
        if posts:
            return posts
    tweets = _normalize_tweets(payload.get("tweets"))
    accounts = [_account_name(a) for a in (payload.get("accounts") or []) if _account_name(a)]
    return [{"account": account, "tweets": list(tweets)} for account in accounts if tweets]


def _find_post(posts: Sequence[Dict[str, Any]], account: str) -> Optional[Dict[str, Any]]:
    wanted = _account_name(account).lower()
    for post in posts:
        if _account_name(post.get("account")).lower() == wanted:
            return post
    return None


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

    def _complete_x_draft(
        self,
        *,
        days: int,
        git_commits_text: str,
        git_stats: Dict[str, Any],
        product_label: str,
        jira_features_text: str = "",
        done_issues_text: str = "",
        temperature: float = 0.4,
    ) -> tuple:
        user_prompt = build_x_posts_user_prompt(
            days=days,
            git_commits_text=git_commits_text,
            git_stats=git_stats,
            product_label=product_label,
            jira_features_text=jira_features_text,
            done_issues_text=done_issues_text,
        )
        try:
            content = self._llm_client().complete(
                messages=[
                    {"role": "system", "content": X_POSTS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1600,
                temperature=temperature,
            )
        except Exception as e:
            raise XPostsError(f"LLM request failed: {e}") from e
        parsed = _extract_json(content)
        newsworthy = _normalize_newsworthy(parsed.get("newsworthy"))
        skip = bool(parsed.get("skip"))
        reason = str(parsed.get("reason") or "").strip()
        drafted = _normalize_tweets(parsed.get("tweets"))
        if newsworthy and drafted:
            skip = False
        if not drafted and not skip:
            skip = True
            reason = reason or "No newsworthy user-facing changes this period."
        return skip, reason, drafted, newsworthy

    def _store_or_default(self) -> DraftStore:
        if self._store is None:
            self._store = default_draft_store()
        return self._store

    def _draft_one_account(
        self,
        *,
        account: str,
        project_keys: List[str],
        days: int,
        git_payload: Dict[str, Any],
        done_issues: Sequence[Dict[str, Any]],
        forced_tweets: Optional[List[str]],
    ) -> Dict[str, Any]:
        product_label = product_label_for_project_keys(project_keys)
        if forced_tweets is not None:
            return {
                "account": account,
                "project_keys": project_keys,
                "tweets": list(forced_tweets),
                "newsworthy": [],
                "jira_features": [],
                "done_tickets": [],
                "reason": "Manual tweet override",
                "skip": False,
            }

        sliced = _slice_git_payload(git_payload, project_keys)
        by_project = sliced.get("by_project") or {}
        git_stats = sliced.get("stats") or {}
        git_commits_text = format_commits_for_prompt(by_project, stats=git_stats)
        scoped_done = _filter_done_issues(done_issues, project_keys)
        done_issues_text = format_done_issues_for_x_prompt(scoped_done)
        jira_features = jira_feature_commits(by_project, project_keys=project_keys)
        jira_features_text = format_jira_feature_commits(jira_features)
        if isinstance(git_stats, dict):
            for key in git_stats:
                if isinstance(git_stats.get(key), dict):
                    git_stats[key]["jira_features"] = sum(
                        1
                        for c in jira_features
                        if str(c.get("jira_key") or "").upper().startswith(
                            f"{str(key).upper()}-"
                        )
                    )

        skip, reason, drafted, newsworthy = self._complete_x_draft(
            days=days,
            git_commits_text=git_commits_text,
            git_stats=git_stats,
            product_label=product_label,
            jira_features_text=jira_features_text,
            done_issues_text=done_issues_text,
        )
        shipped_text = done_issues_text or jira_features_text
        if (skip or not drafted) and shipped_text:
            skip, reason, drafted, newsworthy = self._complete_x_draft(
                days=days,
                git_commits_text=shipped_text,
                git_stats=git_stats,
                product_label=product_label,
                jira_features_text=jira_features_text,
                done_issues_text=done_issues_text,
                temperature=0.2,
            )
        fallback_source = jira_features + _features_from_done_issues(scoped_done)
        if drafted:
            skip = False
        elif fallback_source:
            drafted, fallback_news = fallback_tweets_from_jira_features(fallback_source)
            if drafted:
                skip = False
                reason = "Fallback draft from shipped Jira features"
                if not newsworthy:
                    newsworthy = fallback_news
                logger.warning(
                    "X post LLM skipped despite shipped work for @%s; using fallback draft",
                    account,
                )
        return {
            "account": account,
            "project_keys": project_keys,
            "tweets": drafted,
            "newsworthy": newsworthy,
            "jira_features": [
                str(c.get("jira_key") or "")
                for c in jira_features
                if c.get("jira_key")
            ],
            "done_tickets": [
                str(i.get("key") or "")
                for i in scoped_done
                if i.get("key")
            ],
            "reason": reason,
            "skip": skip or not drafted,
        }

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

        requested = [_account_name(a) for a in (accounts or []) if _account_name(a)]
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

        explicit_keys = normalize_project_keys(project_keys)
        mapped: List[Tuple[str, List[str]]] = []
        unmapped: List[str] = []
        for account in target_accounts:
            keys = resolve_account_projects(account, explicit_keys=explicit_keys or None)
            if keys:
                mapped.append((account, keys))
            else:
                unmapped.append(account)
        if not mapped:
            raise XPostsError(
                "No X accounts mapped to a product. Set X_ACCOUNT_PROJECT_MAP "
                "(e.g. bigasmyaiteam:BIG,vcfieldassistan:VFA) or use handles that "
                "match a product. Unmapped: "
                + ", ".join(unmapped or target_accounts)
            )

        git_stats: Dict[str, Any] = {}
        git_errors: List[Any] = []
        all_done: List[Dict[str, Any]] = []
        git_payload: Dict[str, Any] = {"by_project": {}, "stats": {}, "errors": []}
        if forced_tweets is None:
            all_keys = list(dict.fromkeys(k for _, keys in mapped for k in keys))
            if not all_keys:
                all_keys = list(project_repo_map_from_env().keys())
            git_payload = fetch_commits_for_projects(
                project_keys=all_keys,
                days=days,
                token=self._github_token,
                exclude_autofix=True,
            )
            git_stats = git_payload.get("stats") or {}
            if not isinstance(git_stats, dict):
                git_stats = {}
            git_errors = git_payload.get("errors") or []
            all_done = fetch_done_issues_for_projects(days=days, project_keys=all_keys)
        else:
            git_payload = {"by_project": {}, "stats": {}, "errors": []}

        posts: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for account, keys in mapped:
            drafted = self._draft_one_account(
                account=account,
                project_keys=keys,
                days=days,
                git_payload=git_payload,
                done_issues=all_done,
                forced_tweets=forced_tweets,
            )
            if drafted.get("skip") or not drafted.get("tweets"):
                skipped.append(
                    {
                        "account": account,
                        "reason": drafted.get("reason") or "Nothing newsworthy this period.",
                    }
                )
                continue
            posts.append(drafted)

        skip = not posts
        reason = ""
        if skip:
            if skipped:
                reason = "; ".join(
                    f"@{item['account']}: {item['reason']}" for item in skipped
                )
            else:
                reason = "Nothing newsworthy this period."
        elif skipped:
            reason = "; ".join(
                f"@{item['account']}: {item['reason']}" for item in skipped
            )

        model_name = "manual" if forced_tweets is not None else self._model
        all_tweets: List[str] = []
        for post in posts:
            all_tweets.extend(post.get("tweets") or [])
        result: Dict[str, Any] = {
            "ok": True,
            "skip": skip,
            "reason": reason,
            "posts": [
                {
                    "account": p["account"],
                    "project_keys": p.get("project_keys") or [],
                    "tweets": p.get("tweets") or [],
                    "newsworthy": p.get("newsworthy") or [],
                    "jira_features": p.get("jira_features") or [],
                    "done_tickets": p.get("done_tickets") or [],
                }
                for p in posts
            ],
            "skipped_accounts": skipped,
            "tweets": all_tweets,
            "newsworthy": [
                item
                for p in posts
                for item in (p.get("newsworthy") or [])
            ],
            "jira_features": [
                item
                for p in posts
                for item in (p.get("jira_features") or [])
            ],
            "accounts": [p["account"] for p in posts],
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
            "posts": [
                {
                    "account": p["account"],
                    "tweets": p.get("tweets") or [],
                    "project_keys": p.get("project_keys") or [],
                }
                for p in posts
            ],
            "accounts": [p["account"] for p in posts],
            "tweets": posts[0].get("tweets") or [] if len(posts) == 1 else all_tweets,
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

    def _remove_account_post(self, payload: Dict[str, Any], account: str) -> List[Dict[str, Any]]:
        posts = draft_posts(payload)
        wanted = _account_name(account).lower()
        remaining = [
            post for post in posts if _account_name(post.get("account")).lower() != wanted
        ]
        draft_id = str(payload.get("id") or "").strip()
        if remaining:
            payload["posts"] = remaining
            payload["accounts"] = [p["account"] for p in remaining]
            payload["tweets"] = remaining[0].get("tweets") or [] if len(remaining) == 1 else [
                t for p in remaining for t in (p.get("tweets") or [])
            ]
            self._store_or_default().save(draft_id, payload)
        else:
            self._store_or_default().delete(draft_id)
        return remaining

    def approve(
        self,
        draft_id: str,
        tweets: Optional[List[str]] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = self.load_draft(draft_id)
        posts = draft_posts(payload)
        if not posts:
            self._store_or_default().delete(draft_id)
            raise XPostsError("Draft is missing tweets or accounts")
        chosen = _account_name(account)
        if chosen:
            post = _find_post(posts, chosen)
            if post is None:
                raise XPostsError(f"No pending draft for @{chosen}")
        elif len(posts) == 1:
            post = posts[0]
            chosen = post["account"]
        else:
            raise XPostsError("Choose which account to approve")

        if tweets is None:
            to_post = _normalize_tweets(post.get("tweets"))
        else:
            to_post = _normalize_edited_tweets(tweets)
            if not to_post:
                raise XPostsError("Add at least one tweet before approving")
        if not to_post:
            self._store_or_default().delete(draft_id)
            raise XPostsError("Draft is missing tweets or accounts")

        # Drop this account before calling X so a retry cannot duplicate it.
        remaining = self._remove_account_post(payload, chosen)
        posted: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        try:
            ids = self._x.post_thread(chosen, to_post)
        except Exception as e:
            entry: Dict[str, Any] = {"account": chosen, "error": str(e)}
            partial_ids = getattr(e, "posted_ids", None)
            if partial_ids:
                entry["posted_tweet_ids"] = list(partial_ids)
                entry["posted_urls"] = [
                    f"https://x.com/{chosen}/status/{tid}" for tid in partial_ids
                ]
            failed.append(entry)
            logger.error("Failed to post X thread for account %s", chosen, exc_info=True)
        else:
            posted.append(
                {
                    "account": chosen,
                    "tweet_ids": ids,
                    "urls": [f"https://x.com/{chosen}/status/{tid}" for tid in ids],
                }
            )
        return {
            "ok": not failed,
            "posted": posted,
            "failed": failed,
            "account": chosen,
            "remaining": [p["account"] for p in remaining],
        }

    def cleanup_expired_drafts(self, *, max_to_delete: int = 50) -> int:
        return int(
            self._store_or_default().cleanup_expired(
                ttl_hours=_ttl_hours(),
                max_to_delete=max_to_delete,
            )
            or 0
        )

    def decline(self, draft_id: str, account: Optional[str] = None) -> Dict[str, Any]:
        payload = self._store_or_default().load(draft_id)
        if not payload:
            raise XPostsError("Draft not found")
        posts = draft_posts(payload)
        chosen = _account_name(account)
        if chosen:
            if _find_post(posts, chosen) is None:
                raise XPostsError(f"No pending draft for @{chosen}")
            remaining = self._remove_account_post(payload, chosen)
            return {
                "ok": True,
                "declined": True,
                "draft_id": draft_id,
                "account": chosen,
                "remaining": [p["account"] for p in remaining],
            }
        if len(posts) > 1:
            raise XPostsError("Choose which account to skip")
        self._store_or_default().delete(draft_id)
        declined_account = posts[0]["account"] if posts else ""
        return {
            "ok": True,
            "declined": True,
            "draft_id": draft_id,
            "account": declined_account,
            "remaining": [],
        }


def format_discord_message(result: Dict[str, Any]) -> str:
    if result.get("skip"):
        reason = result.get("reason") or "Nothing newsworthy this period."
        return (
            "**Weekly X posts skipped**\n"
            f"{reason}\n"
            "No draft was stored."
        )
    posts = result.get("posts") or []
    if not posts:
        accounts = ", ".join(f"`{a}`" for a in (result.get("accounts") or []))
        tweets = result.get("tweets") or []
        posts = [{"account": a, "tweets": tweets} for a in (result.get("accounts") or [])]
        if not posts and tweets:
            posts = [{"account": accounts or "x", "tweets": tweets}]
    lines = ["**New X post drafts ready for approval**", ""]
    for post in posts:
        account = _account_name(post.get("account"))
        keys = post.get("project_keys") or []
        label = product_label_for_project_keys(keys) if keys else ""
        heading = f"**@{account}**"
        if label and label != "the product":
            heading += f" ({label})"
        lines.append(heading)
        tweets = post.get("tweets") or []
        for i, tweet in enumerate(tweets, start=1):
            prefix = f"{i}/ " if len(tweets) > 1 else ""
            lines.append(prefix + tweet)
        lines.append("")
    skipped = result.get("skipped_accounts") or []
    if skipped:
        lines.append("Skipped:")
        for item in skipped:
            lines.append(f"- @{item.get('account')}: {item.get('reason')}")
        lines.append("")
    review_url_value = (result.get("review_url") or "").strip()
    if review_url_value:
        lines.append(f"👉 [Review, then Approve or Skip each account]({review_url_value})")
    hours = result.get("expires_hours") or DEFAULT_TTL_HOURS
    lines.append(
        f"Draft expires in {hours} hours. Skip deletes that account's draft; "
        "you can still copy the text above to post manually."
    )
    return "\n".join(lines).strip()


def configured_x_accounts() -> List[str]:
    return [c.account for c in load_account_credentials().values()] or parse_account_names()
