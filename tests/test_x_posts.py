"""Unit tests for automated X posts (BIG-4)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from bigas.providers.notifications.x import (
    XAccountCredentials,
    XProvider,
    account_env_suffix,
    clamp_tweet,
    load_account_credentials,
    parse_account_names,
)
from bigas.resources.product.x_posts.drafts import GcsDraftStore, InMemoryDraftStore, is_expired
from bigas.resources.product.x_posts.endpoints import x_posts_bp
from bigas.resources.product.x_posts.service import (
    XPostsError,
    XPostsService,
    _extract_json,
    format_discord_message,
)
from bigas.resources.product.x_posts.signing import sign_draft_id, verify_draft_token


def _clear_x_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("X_") or key in {"BIGAS_ACCESS_KEYS", "JIRA_AUTOMATION_WEBHOOK_SECRET"}:
            monkeypatch.delenv(key, raising=False)


def test_parse_account_names_and_suffix():
    assert parse_account_names("bigasmyaiteam, @other") == ["bigasmyaiteam", "other"]
    assert account_env_suffix("@bigas-my-ai") == "BIGAS_MY_AI"
    assert account_env_suffix("vcfieldassistan") == "VCFIELDASSISTAN"


def test_load_vcfieldassistan_legacy_suffix(monkeypatch):
    _clear_x_env(monkeypatch)
    monkeypatch.setenv("X_ACCOUNTS", "bigasmyaiteam,vcfieldassistan")
    monkeypatch.setenv("X_API_KEY_BIGASMYAITEAM", "k1")
    monkeypatch.setenv("X_API_SECRET_BIGASMYAITEAM", "s1")
    monkeypatch.setenv("X_ACCESS_TOKEN_BIGASMYAITEAM", "t1")
    monkeypatch.setenv("X_ACCESS_SECRET_BIGASMYAITEAM", "ts1")
    monkeypatch.setenv("X_API_KEY_VCFIELDASSISAN", "k2")
    monkeypatch.setenv("X_API_SECRET_VCFIELDASSISAN", "s2")
    monkeypatch.setenv("X_ACCESS_TOKEN_VCFIELDASSISAN", "t2")
    monkeypatch.setenv("X_ACCESS_SECRET_VCFIELDASSISAN", "ts2")
    creds = load_account_credentials()
    assert set(creds) == {"bigasmyaiteam", "vcfieldassistan"}
    assert creds["vcfieldassistan"].access_token == "t2"


def test_clamp_tweet():
    assert clamp_tweet("  hi  ") == "hi"
    long = "x" * 300
    out = clamp_tweet(long)
    assert len(out) == 280
    assert out.endswith("…")


def test_load_per_account_env(monkeypatch):
    _clear_x_env(monkeypatch)
    monkeypatch.setenv("X_ACCOUNTS", "bigasmyaiteam")
    monkeypatch.setenv("X_API_KEY_BIGASMYAITEAM", "k")
    monkeypatch.setenv("X_API_SECRET_BIGASMYAITEAM", "s")
    monkeypatch.setenv("X_ACCESS_TOKEN_BIGASMYAITEAM", "t")
    monkeypatch.setenv("X_ACCESS_SECRET_BIGASMYAITEAM", "ts")
    creds = load_account_credentials()
    assert "bigasmyaiteam" in creds
    assert creds["bigasmyaiteam"].api_key == "k"
    assert XProvider.is_configured()


def test_load_credentials_json(monkeypatch):
    _clear_x_env(monkeypatch)
    monkeypatch.setenv(
        "X_CREDENTIALS_JSON",
        json.dumps(
            {
                "bigasmyaiteam": {
                    "api_key": "k",
                    "api_secret": "s",
                    "access_token": "t",
                    "access_secret": "ts",
                }
            }
        ),
    )
    creds = load_account_credentials()
    assert creds["bigasmyaiteam"].access_token == "t"


def test_shared_user_tokens_not_reused_across_accounts(monkeypatch):
    _clear_x_env(monkeypatch)
    monkeypatch.setenv("X_ACCOUNTS", "one,two")
    monkeypatch.setenv("X_API_KEY", "k")
    monkeypatch.setenv("X_API_SECRET", "s")
    monkeypatch.setenv("X_ACCESS_TOKEN", "t")
    monkeypatch.setenv("X_ACCESS_SECRET", "ts")
    assert load_account_credentials() == {}


def test_post_thread_uses_reply_ids():
    calls = []

    class FakeClient:
        def create_tweet(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data={"id": str(len(calls))})

    provider = XProvider(
        credentials={
            "demo": XAccountCredentials(
                account="demo",
                api_key="k",
                api_secret="s",
                access_token="t",
                access_secret="ts",
            )
        },
        client_factory=lambda _creds: FakeClient(),
    )
    ids = provider.post_thread("demo", ["first", "second"])
    assert ids == ["1", "2"]
    assert "in_reply_to_tweet_id" not in calls[0]
    assert calls[1]["in_reply_to_tweet_id"] == "1"


def test_extract_json_and_skip_draft():
    parsed = _extract_json('{"skip": true, "reason": "only chores", "tweets": []}')
    assert parsed["skip"] is True
    assert parsed["reason"] == "only chores"


def test_generate_skip_does_not_store():
    store = InMemoryDraftStore()
    provider = XProvider(
        credentials={"demo": XAccountCredentials("demo", "k", "s", "t", "ts")}
    )
    llm = SimpleNamespace(
        complete=lambda **_kwargs: json.dumps(
            {"skip": True, "reason": "Only minor bug fixes.", "tweets": []}
        )
    )
    service = XPostsService(x_provider=provider, draft_store=store)
    service._llm = llm
    service._model = "test-model"
    with patch(
        "bigas.resources.product.x_posts.service.fetch_commits_for_projects",
        return_value={"by_project": {}, "stats": {"BIG": {"total": 1}}, "errors": []},
    ), patch(
        "bigas.resources.product.x_posts.service.format_commits_for_prompt",
        return_value="- fix typo",
    ), patch(
        "bigas.resources.product.x_posts.service.project_repo_map_from_env",
        return_value={"BIG": "mckort/bigas"},
    ):
        result = service.generate(days=7, dry_run=False)
    assert result["skip"] is True
    assert store.load(result.get("draft_id") or "") is None
    assert "Only minor" in format_discord_message(result)


def test_generate_stores_draft_and_review_url(monkeypatch):
    monkeypatch.setenv("X_POST_SIGNING_SECRET", "secret")
    monkeypatch.setenv("BIGAS_PUBLIC_URL", "https://bigas.example")
    store = InMemoryDraftStore()
    provider = XProvider(
        credentials={"demo": XAccountCredentials("demo", "k", "s", "t", "ts")}
    )
    llm = SimpleNamespace(
        complete=lambda **_kwargs: json.dumps(
            {"skip": False, "reason": "", "tweets": ["We shipped weekly X posts."]}
        )
    )
    service = XPostsService(x_provider=provider, draft_store=store)
    service._llm = llm
    service._model = "test-model"
    with patch(
        "bigas.resources.product.x_posts.service.fetch_commits_for_projects",
        return_value={"by_project": {}, "stats": {}, "errors": []},
    ), patch(
        "bigas.resources.product.x_posts.service.format_commits_for_prompt",
        return_value="- add x posts",
    ), patch(
        "bigas.resources.product.x_posts.service.project_repo_map_from_env",
        return_value={"BIG": "mckort/bigas"},
    ):
        result = service.generate(days=7)
    assert result["skip"] is False
    draft_id = result["draft_id"]
    draft = store.load(draft_id)
    assert draft["tweets"] == ["We shipped weekly X posts."]
    assert "/api/x-posts/" in result["review_url"]
    token = sign_draft_id(draft_id, secret="secret")
    assert token in result["review_url"]
    assert verify_draft_token(draft_id, token, secret="secret")


def test_generate_manual_tweets_skips_llm(monkeypatch):
    monkeypatch.setenv("X_POST_SIGNING_SECRET", "secret")
    monkeypatch.setenv("BIGAS_PUBLIC_URL", "https://bigas.example")
    store = InMemoryDraftStore()
    provider = XProvider(
        credentials={"demo": XAccountCredentials("demo", "k", "s", "t", "ts")}
    )
    service = XPostsService(x_provider=provider, draft_store=store)
    result = service.generate(
        days=7,
        tweets=["Bigas can now draft weekly X updates with Discord approval."],
    )
    assert result["skip"] is False
    assert result["model"] == "manual"
    assert result["tweets"] == [
        "Bigas can now draft weekly X updates with Discord approval."
    ]
    assert store.load(result["draft_id"])["tweets"] == result["tweets"]


class _FakeX:
    def __init__(self):
        self.threads = []

    def configured_accounts(self):
        return ["demo"]

    def post_thread(self, account, tweets):
        self.threads.append((account, tweets))
        return ["99"]


def test_approve_and_decline_flow():
    store = InMemoryDraftStore()
    fake_x = _FakeX()
    service = XPostsService(x_provider=fake_x, draft_store=store)
    store.save(
        "abc",
        {
            "id": "abc",
            "accounts": ["demo"],
            "tweets": ["Hello community"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    posted = service.approve("abc")
    assert posted["ok"] is True
    assert posted["posted"][0]["tweet_ids"] == ["99"]
    assert store.load("abc") is None
    assert fake_x.threads == [("demo", ["Hello community"])]

    store.save(
        "edit",
        {
            "id": "edit",
            "accounts": ["demo"],
            "tweets": ["Hello community"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    edited = service.approve("edit", tweets=["  Edited copy  ", ""])
    assert edited["ok"] is True
    assert fake_x.threads[-1] == ("demo", ["Edited copy"])
    assert store.load("edit") is None

    store.save(
        "empty-edit",
        {
            "id": "empty-edit",
            "accounts": ["demo"],
            "tweets": ["Keep me"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    try:
        service.approve("empty-edit", tweets=["   "])
        assert False, "expected empty-edit to fail"
    except Exception as e:
        assert "at least one tweet" in str(e).lower()
    assert store.load("empty-edit") is not None

    store.save(
        "too-long",
        {
            "id": "too-long",
            "accounts": ["demo"],
            "tweets": ["Keep me"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    try:
        service.approve("too-long", tweets=["x" * 281])
        assert False, "expected too-long to fail"
    except Exception as e:
        assert "280" in str(e)
    assert store.load("too-long") is not None

    store.save(
        "def",
        {
            "id": "def",
            "accounts": ["demo"],
            "tweets": ["Nope"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    declined = service.decline("def")
    assert declined["declined"] is True
    assert store.load("def") is None
    assert len(fake_x.threads) == 2


def test_expired_draft_is_deleted():
    payload = {
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    }
    assert is_expired(payload, ttl_hours=48) is True
    store = InMemoryDraftStore()
    fake_x = _FakeX()
    service = XPostsService(x_provider=fake_x, draft_store=store)
    store.save("old", {**payload, "accounts": ["demo"], "tweets": ["x"]})
    try:
        service.load_draft("old")
        assert False, "expected expiry"
    except Exception as e:
        assert "expired" in str(e).lower()
    assert store.load("old") is None


def test_hitl_http_approve_and_decline(monkeypatch):
    monkeypatch.setenv("X_POST_SIGNING_SECRET", "secret")
    store = InMemoryDraftStore()
    fake_x = _FakeX()
    service = XPostsService(x_provider=fake_x, draft_store=store)
    store.save(
        "hitl1",
        {
            "id": "hitl1",
            "accounts": ["demo"],
            "tweets": ["Ship it"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    token = sign_draft_id("hitl1", secret="secret")
    app = Flask(__name__)
    app.register_blueprint(x_posts_bp)
    app.config["TESTING"] = True
    client = app.test_client()
    with patch("bigas.resources.product.x_posts.endpoints._service", return_value=service):
        preview = client.get(f"/api/x-posts/hitl1?token={token}")
        assert preview.status_code == 200
        assert b"Approve and post" in preview.data
        assert b"Decline" in preview.data
        assert b'<textarea' in preview.data
        assert b"Ship it" in preview.data
        # GET must not publish
        assert fake_x.threads == []

        bad = client.post("/api/x-posts/hitl1/approve?token=nope")
        assert bad.status_code == 403
        assert fake_x.threads == []

        empty = client.post(
            f"/api/x-posts/hitl1/approve?token={token}",
            data={"tweets": "   "},
        )
        assert empty.status_code == 400
        assert store.load("hitl1") is not None
        assert fake_x.threads == []

        missing = client.post(f"/api/x-posts/hitl1/approve?token={token}")
        assert missing.status_code == 400
        assert store.load("hitl1") is not None
        assert fake_x.threads == []

        ok = client.post(
            f"/api/x-posts/hitl1/approve?token={token}",
            data={"tweets": "Edited and approved"},
        )
        assert ok.status_code == 200
        assert b"Posted to X" in ok.data
        assert fake_x.threads == [("demo", ["Edited and approved"])]
        assert store.load("hitl1") is None

        store.save(
            "hitl2",
            {
                "id": "hitl2",
                "accounts": ["demo"],
                "tweets": ["Manual instead"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        token2 = sign_draft_id("hitl2", secret="secret")
        declined = client.post(f"/api/x-posts/hitl2/decline?token={token2}")
        assert declined.status_code == 200
        assert b"Draft declined" in declined.data
        assert store.load("hitl2") is None
        assert len(fake_x.threads) == 1


class _BoomX(_FakeX):
    def post_thread(self, account, tweets):
        self.threads.append((account, tweets))
        raise RuntimeError("402 Payment Required")


class _PartialMultiAccountX:
    def __init__(self):
        self.threads = []

    def configured_accounts(self):
        return ["first", "second"]

    def post_thread(self, account, tweets):
        self.threads.append((account, tweets))
        if account == "second":
            raise RuntimeError("402 Payment Required")
        return ["11", "12"]


def test_approve_deletes_draft_before_post_so_retry_cannot_duplicate():
    store = InMemoryDraftStore()
    fake_x = _BoomX()
    service = XPostsService(x_provider=fake_x, draft_store=store)
    store.save(
        "dup",
        {
            "id": "dup",
            "accounts": ["demo"],
            "tweets": ["Hello once"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    result = service.approve("dup")
    assert result["ok"] is False
    assert result["posted"] == []
    assert result["failed"][0]["account"] == "demo"
    assert "402" in result["failed"][0]["error"]
    assert store.load("dup") is None
    assert fake_x.threads == [("demo", ["Hello once"])]
    try:
        service.approve("dup")
        assert False, "expected missing draft"
    except XPostsError as e:
        assert "not found" in str(e).lower()
    assert fake_x.threads == [("demo", ["Hello once"])]


def test_approve_partial_multi_account_success():
    store = InMemoryDraftStore()
    fake_x = _PartialMultiAccountX()
    service = XPostsService(x_provider=fake_x, draft_store=store)
    store.save(
        "partial",
        {
            "id": "partial",
            "accounts": ["first", "second"],
            "tweets": ["Hello", "World"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    result = service.approve("partial")
    assert result["ok"] is False
    assert result["posted"] == [
        {
            "account": "first",
            "tweet_ids": ["11", "12"],
            "urls": [
                "https://x.com/first/status/11",
                "https://x.com/first/status/12",
            ],
        }
    ]
    assert result["failed"] == [
        {"account": "second", "error": "402 Payment Required"},
    ]
    assert store.load("partial") is None
    assert fake_x.threads == [
        ("first", ["Hello", "World"]),
        ("second", ["Hello", "World"]),
    ]


def test_post_thread_partial_tweet_failure():
    calls = []

    class FailOnSecondClient:
        def create_tweet(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError("rate limited")
            return SimpleNamespace(data={"id": str(len(calls))})

    provider = XProvider(
        credentials={
            "demo": XAccountCredentials(
                account="demo",
                api_key="k",
                api_secret="s",
                access_token="t",
                access_secret="ts",
            )
        },
        client_factory=lambda _creds: FailOnSecondClient(),
    )
    try:
        provider.post_thread("demo", ["first", "second"])
        assert False, "expected partial failure"
    except Exception as e:
        from bigas.providers.notifications.x import XProviderPartialPostError

        assert isinstance(e, XProviderPartialPostError)
        assert e.posted_ids == ["1"]
    assert len(calls) == 2


def test_hitl_http_partial_post(monkeypatch):
    monkeypatch.setenv("X_POST_SIGNING_SECRET", "secret")
    store = InMemoryDraftStore()
    fake_x = _PartialMultiAccountX()
    service = XPostsService(x_provider=fake_x, draft_store=store)
    store.save(
        "partial-http",
        {
            "id": "partial-http",
            "accounts": ["first", "second"],
            "tweets": ["Ship it"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    token = sign_draft_id("partial-http", secret="secret")
    app = Flask(__name__)
    app.register_blueprint(x_posts_bp)
    app.config["TESTING"] = True
    client = app.test_client()
    with patch("bigas.resources.product.x_posts.endpoints._service", return_value=service):
        resp = client.post(
            f"/api/x-posts/partial-http/approve?token={token}",
            data={"tweets": "Ship it"},
        )
    assert resp.status_code == 200
    assert b"Partially posted to X" in resp.data
    assert b"https://x.com/first/status/11" in resp.data
    assert b"@second" in resp.data
    assert b"402 Payment Required" in resp.data
    assert store.load("partial-http") is None


def test_cleanup_expired_drafts_keeps_fresh_ones():
    store = InMemoryDraftStore()
    service = XPostsService(x_provider=_FakeX(), draft_store=store)
    store.save(
        "old",
        {
            "id": "old",
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat(),
        },
    )
    store.save(
        "fresh",
        {
            "id": "fresh",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert service.cleanup_expired_drafts() == 1
    assert store.load("old") is None
    assert store.load("fresh") is not None


def test_gcs_cleanup_expired_uses_blob_age():
    now = datetime.now(timezone.utc)

    class FakeBlob:
        def __init__(self, name, time_created):
            self.name = name
            self.time_created = time_created
            self.deleted = False

        def delete(self):
            self.deleted = True

    old = FakeBlob("x_drafts/old.json", now - timedelta(hours=72))
    fresh = FakeBlob("x_drafts/fresh.json", now - timedelta(hours=1))

    class FakeBucket:
        def list_blobs(self, prefix=""):
            return [old, fresh]

    store = GcsDraftStore(bucket=FakeBucket())
    assert store.cleanup_expired(ttl_hours=48) == 1
    assert old.deleted is True
    assert fresh.deleted is False
