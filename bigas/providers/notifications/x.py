"""X (Twitter) notification / posting provider.

Credentials are loaded from env (Secret Manager in production). Multi-account
support uses X_ACCOUNTS plus per-account or shared OAuth 1.0a keys.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from bigas.providers.notifications.base import NotificationChannel

logger = logging.getLogger(__name__)

TWEET_MAX_CHARS = 280


class XProviderError(RuntimeError):
    pass


class XProviderPartialPostError(XProviderError):
    """Raised when one or more tweets in a thread posted before a later tweet failed."""

    def __init__(self, message: str, *, posted_ids: List[str]) -> None:
        super().__init__(message)
        self.posted_ids = list(posted_ids)


@dataclass(frozen=True)
class XAccountCredentials:
    account: str
    api_key: str
    api_secret: str
    access_token: str
    access_secret: str


def parse_account_names(raw: Optional[str] = None) -> List[str]:
    value = (raw if raw is not None else os.environ.get("X_ACCOUNTS") or "").strip()
    if not value:
        return []
    names: List[str] = []
    seen = set()
    for part in re.split(r"[\s,;]+", value):
        name = part.strip().lstrip("@")
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def account_env_suffix(account: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (account or "").strip().lstrip("@"))
    return cleaned.strip("_").upper()


def _suffixes_for_account(account: str) -> List[str]:
    """Env-name suffixes to try for an X handle.

    Secrets for @vcfieldassistan were first created as VCFIELDASSISAN (missing T).
    Keep that alias so existing Secret Manager names still load.
    """
    name = (account or "").strip().lstrip("@")
    primary = account_env_suffix(name)
    suffixes = [primary]
    if name.lower() == "vcfieldassistan" and "VCFIELDASSISAN" not in suffixes:
        suffixes.append("VCFIELDASSISAN")
    return suffixes


def _env_with_suffixes(prefix: str, suffixes: List[str]) -> str:
    for suffix in suffixes:
        value = _env(f"{prefix}_{suffix}")
        if value:
            return value
    return ""


def clamp_tweet(text: str, *, limit: int = TWEET_MAX_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: limit - 1].rstrip() + "…"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _credentials_from_json() -> Dict[str, XAccountCredentials]:
    raw = _env("X_CREDENTIALS_JSON")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise XProviderError(f"X_CREDENTIALS_JSON is not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise XProviderError("X_CREDENTIALS_JSON must be an object of account → credentials")
    out: Dict[str, XAccountCredentials] = {}
    for account, creds in payload.items():
        name = str(account or "").strip().lstrip("@")
        if not name or not isinstance(creds, dict):
            continue
        item = XAccountCredentials(
            account=name,
            api_key=str(creds.get("api_key") or creds.get("consumer_key") or "").strip(),
            api_secret=str(creds.get("api_secret") or creds.get("consumer_secret") or "").strip(),
            access_token=str(creds.get("access_token") or "").strip(),
            access_secret=str(
                creds.get("access_secret")
                or creds.get("access_token_secret")
                or ""
            ).strip(),
        )
        if all((item.api_key, item.api_secret, item.access_token, item.access_secret)):
            out[name.lower()] = item
    return out


def _credentials_for_account(
    account: str,
    *,
    json_creds: Optional[Dict[str, XAccountCredentials]] = None,
    allow_shared_user_tokens: bool = True,
) -> Optional[XAccountCredentials]:
    name = (account or "").strip().lstrip("@")
    if not name:
        return None
    blob = json_creds if json_creds is not None else _credentials_from_json()
    from_json = blob.get(name.lower())
    if from_json:
        return from_json

    suffixes = _suffixes_for_account(name)
    api_key = _env_with_suffixes("X_API_KEY", suffixes) or _env("X_API_KEY")
    api_secret = _env_with_suffixes("X_API_SECRET", suffixes) or _env("X_API_SECRET")
    access_token = _env_with_suffixes("X_ACCESS_TOKEN", suffixes)
    access_secret = (
        _env_with_suffixes("X_ACCESS_SECRET", suffixes)
        or _env_with_suffixes("X_ACCESS_TOKEN_SECRET", suffixes)
    )
    if allow_shared_user_tokens:
        access_token = access_token or _env("X_ACCESS_TOKEN")
        access_secret = (
            access_secret
            or _env("X_ACCESS_SECRET")
            or _env("X_ACCESS_TOKEN_SECRET")
        )
    if not all((api_key, api_secret, access_token, access_secret)):
        missing = [
            label
            for label, value in (
                ("api_key", api_key),
                ("api_secret", api_secret),
                ("access_token", access_token),
                ("access_secret", access_secret),
            )
            if not value
        ]
        logger.warning(
            "Incomplete X credentials for account %s (missing %s)",
            name,
            ", ".join(missing) or "unknown",
        )
        return None
    return XAccountCredentials(
        account=name,
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_secret=access_secret,
    )


def load_account_credentials(
    accounts: Optional[List[str]] = None,
) -> Dict[str, XAccountCredentials]:
    json_creds = _credentials_from_json()
    names = list(accounts) if accounts is not None else parse_account_names()
    if not names:
        names = [c.account for c in json_creds.values()]
    if not names and _credentials_for_account("default", json_creds=json_creds):
        names = ["default"]

    loaded: Dict[str, XAccountCredentials] = {}
    allow_shared = len(names) <= 1
    for name in names:
        creds = _credentials_for_account(
            name,
            json_creds=json_creds,
            allow_shared_user_tokens=allow_shared,
        )
        if creds:
            loaded[name.lower()] = creds
    logger.info(
        "Loaded X credentials for: %s (from X_ACCOUNTS=%s)",
        ", ".join(loaded.keys()) or "(none)",
        ",".join(parse_account_names()) or "(empty)",
    )
    return loaded


class XProvider(NotificationChannel):
    name = "x"
    display_name = "X (Twitter)"

    def __init__(
        self,
        *,
        credentials: Optional[Dict[str, XAccountCredentials]] = None,
        client_factory: Optional[Callable[[XAccountCredentials], Any]] = None,
    ) -> None:
        self._credentials = credentials if credentials is not None else load_account_credentials()
        self._client_factory = client_factory

    @classmethod
    def is_configured(cls) -> bool:
        try:
            return bool(load_account_credentials())
        except XProviderError:
            logger.warning("X provider credentials are invalid", exc_info=True)
            return False

    def configured_accounts(self) -> List[str]:
        return [c.account for c in self._credentials.values()]

    def send(self, message: str, channel_hint: Optional[str] = None) -> bool:
        text = clamp_tweet(message)
        if not text:
            return False
        account = (channel_hint or "").strip().lstrip("@")
        if not account:
            accounts = self.configured_accounts()
            if not accounts:
                return False
            account = accounts[0]
        try:
            self.post_thread(account, [text])
            return True
        except Exception:
            logger.error("Failed to post to X account %s", account, exc_info=True)
            return False

    def post_thread(self, account_name: str, tweets: List[str]) -> List[str]:
        texts = [clamp_tweet(t) for t in tweets if clamp_tweet(t)]
        if not texts:
            raise XProviderError("No tweet text to post")
        creds = self._credentials.get((account_name or "").strip().lstrip("@").lower())
        if creds is None:
            raise XProviderError(f"No X credentials configured for account '{account_name}'")
        client = self._client(creds)
        posted_ids: List[str] = []
        reply_to: Optional[str] = None
        for text in texts:
            kwargs: Dict[str, Any] = {"text": text}
            if reply_to:
                kwargs["in_reply_to_tweet_id"] = reply_to
            try:
                response = client.create_tweet(**kwargs)
                tweet_id = _tweet_id_from_response(response)
                if not tweet_id:
                    raise XProviderError("X API create_tweet returned no tweet id")
            except XProviderError as e:
                if posted_ids:
                    raise XProviderPartialPostError(str(e), posted_ids=list(posted_ids)) from e
                raise
            except Exception as e:
                if posted_ids:
                    raise XProviderPartialPostError(str(e), posted_ids=list(posted_ids)) from e
                raise XProviderError(str(e)) from e
            posted_ids.append(tweet_id)
            reply_to = tweet_id
        return posted_ids

    def _client(self, creds: XAccountCredentials) -> Any:
        if self._client_factory:
            return self._client_factory(creds)
        try:
            import tweepy
        except ImportError as e:
            raise XProviderError(
                "tweepy is required to post to X. Install it via requirements.txt."
            ) from e
        return tweepy.Client(
            consumer_key=creds.api_key,
            consumer_secret=creds.api_secret,
            access_token=creds.access_token,
            access_token_secret=creds.access_secret,
        )


def _tweet_id_from_response(response: Any) -> str:
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return str(data.get("id") or "").strip()
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return str(first.get("id") or "").strip()
    if isinstance(response, dict):
        inner = response.get("data") or {}
        if isinstance(inner, dict):
            return str(inner.get("id") or "").strip()
    return ""
