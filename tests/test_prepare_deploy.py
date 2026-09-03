"""Versioned prepare-deploy chat flow."""
from __future__ import annotations

import os

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")
os.environ.setdefault("GITHUB_TOKEN", "test-github-token")
os.environ.setdefault("BIGAS_JIRA_PROJECT_REPO_MAP", "VFA:mckort/vcfieldassistant")
os.environ.setdefault("PROJECT_BRANCH_MAPPING", "VFA:staging,DEFAULT:main")
os.environ.setdefault("BIGAS_DEPLOY_WORKFLOW_MAP", "VFA:deploy-backend.yml,deploy-web.yml")

from bigas.chat.db import get_chat_store
from bigas.resources.devops.pipeline import (
    is_deploy_start,
    run_chat_deploy_pipeline,
    should_run_deploy_pipeline,
)
from bigas.resources.devops.prepare import (
    format_version_ticket_report,
    is_prepare_start,
    parse_prepare_command,
    run_prepare_deploy,
)
from bigas.tickets.release_store import reset_release_store_for_tests
from bigas.tickets.releases import create_release
from bigas.tickets.store import get_ticket_store
from bigas.tickets import store as ticket_store_module


def setup_function():
    ticket_store_module._store = None
    reset_release_store_for_tests()


def teardown_function():
    ticket_store_module._store = None
    reset_release_store_for_tests()


def test_parse_prepare_command():
    assert is_prepare_start("prepare deploy VFA 0.1.0")
    assert is_prepare_start("Please prepare deploy 0.1.0")
    assert not is_deploy_start("prepare deploy VFA 0.1.0")
    parsed = parse_prepare_command("prepare deploy VFA v0.1.0")
    assert parsed == {"project_key": "VFA", "version": "0.1.0"}
    assert should_run_deploy_pipeline("prepare deploy VFA 0.1.0") is True


def test_feature_report_warns_on_open_tickets():
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Shipped feature",
        user_id="dev-user",
        key="VFA-1",
        fix_version="0.1.0",
        status="Done",
    )
    store.create_ticket(
        board["board_id"],
        title="Still open",
        user_id="dev-user",
        key="VFA-2",
        fix_version="0.1.0",
        status="To Do",
    )
    text, done, open_tickets = format_version_ticket_report("VFA", "0.1.0")
    assert len(done) == 1
    assert len(open_tickets) == 1
    assert "VFA-1" in text
    assert "VFA-2" in text
    assert "Open tickets" in text


def _low_risk(**kwargs):
    return {
        "status": "ok",
        "summary": "Compared prod → main. No risky files.",
        "risk_level": "low",
        "findings": {},
        "repo": "mckort/vcfieldassistant",
        "head_ref": "main",
        "site_urls": ["https://vcfieldassistant.com"],
    }


def _high_risk(**kwargs):
    return {
        **_low_risk(),
        "risk_level": "high",
        "summary": "Compared prod → main. Warnings: 1 database migration.",
        "findings": {"database_migration": ["db/migrations/002.sql"]},
    }


def test_prepare_asks_when_open_tickets_even_if_low_risk(monkeypatch):
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Open work",
        user_id="dev-user",
        key="VFA-9",
        fix_version="0.1.0",
        status="To Do",
    )
    create_release("VFA", name="0.1.0")

    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    triggered = {"called": False}

    monkeypatch.setattr(
        "bigas.resources.devops.prepare.ensure_release_on_main",
        lambda **kwargs: {"status": "already_on_main", "repo": "mckort/vcfieldassistant"},
    )
    monkeypatch.setattr("bigas.resources.devops.prepare.check_deployment_risk", _low_risk)
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.trigger_deployment",
        lambda **kwargs: triggered.update(called=True) or {},
    )

    result = run_prepare_deploy(
        thread_id=thread["thread_id"],
        user_message="prepare deploy VFA 0.1.0",
    )
    assert triggered["called"] is False
    assert result["status"] == "complete"
    pending = chat.get_thread(thread["thread_id"]).get("pending_deploy")
    assert pending and pending.get("kind") == "prepare"
    assert pending.get("version") == "0.1.0"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "VFA-9" in blob
    assert "yes" in blob.lower()


def test_prepare_asks_on_medium_risk(monkeypatch):
    create_release("VFA", name="0.1.0")
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    triggered = {"called": False}

    monkeypatch.setattr(
        "bigas.resources.devops.prepare.ensure_release_on_main",
        lambda **kwargs: {"status": "already_on_main", "repo": "mckort/vcfieldassistant"},
    )
    monkeypatch.setattr("bigas.resources.devops.prepare.check_deployment_risk", _high_risk)
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.trigger_deployment",
        lambda **kwargs: triggered.update(called=True) or {},
    )

    result = run_prepare_deploy(
        thread_id=thread["thread_id"],
        user_message="prepare deploy VFA 0.1.0",
    )
    assert triggered["called"] is False
    assert result["status"] == "complete"
    pending = chat.get_thread(thread["thread_id"]).get("pending_deploy")
    assert pending and pending["risk_level"] == "high"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "high" in blob.lower()


def test_prepare_autodeploys_on_low_risk_without_open_tickets(monkeypatch):
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Done feature",
        user_id="dev-user",
        key="VFA-10",
        fix_version="0.1.0",
        status="Done",
    )
    create_release("VFA", name="0.1.0")

    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    triggered = {"called": False, "ref": None}

    monkeypatch.setattr(
        "bigas.resources.devops.prepare.ensure_release_on_main",
        lambda **kwargs: {"status": "already_on_main", "repo": "mckort/vcfieldassistant"},
    )
    monkeypatch.setattr("bigas.resources.devops.prepare.check_deployment_risk", _low_risk)

    def _trigger(**kwargs):
        triggered["called"] = True
        triggered["ref"] = kwargs.get("ref")
        return {
            "status": "ok",
            "summary": "Triggered 1 workflow(s).",
            "repo": "mckort/vcfieldassistant",
            "ref": "main",
            "triggered": [
                {"workflow": "deploy-backend.yml", "run_id": 44, "html_url": "https://x"}
            ],
            "errors": [],
            "site_urls": ["https://vcfieldassistant.com"],
        }

    monkeypatch.setattr("bigas.resources.devops.pipeline.trigger_deployment", _trigger)

    result = run_prepare_deploy(
        thread_id=thread["thread_id"],
        user_message="prepare deploy VFA 0.1.0",
    )
    assert triggered["called"] is True
    assert triggered["ref"] == "main"
    assert result.get("deploy_poll_active") is True
    poll = chat.get_thread(thread["thread_id"]).get("pending_deploy_poll")
    assert poll and poll.get("release_version") == "0.1.0"


def test_prepare_confirm_deploys_main(monkeypatch):
    create_release("VFA", name="0.1.0")
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        pending_deploy={
            "kind": "prepare",
            "project_key": "VFA",
            "version": "0.1.0",
            "risk_level": "high",
            "repo": "mckort/vcfieldassistant",
            "open_ticket_keys": ["VFA-9"],
        },
    )

    monkeypatch.setattr("bigas.resources.devops.pipeline.check_deployment_risk", _low_risk)

    def _trigger(**kwargs):
        return {
            "status": "ok",
            "summary": "Triggered 1 workflow(s).",
            "repo": "mckort/vcfieldassistant",
            "ref": kwargs.get("ref") or "main",
            "triggered": [
                {"workflow": "deploy-backend.yml", "run_id": 55, "html_url": "https://x"}
            ],
            "errors": [],
            "site_urls": [],
        }

    monkeypatch.setattr("bigas.resources.devops.pipeline.trigger_deployment", _trigger)

    assert should_run_deploy_pipeline("ja", thread["thread_id"]) is True
    result = run_chat_deploy_pipeline(thread_id=thread["thread_id"], user_message="ja")
    assert result["status"] == "in_progress"
    poll = chat.get_thread(thread["thread_id"]).get("pending_deploy_poll")
    assert poll and poll.get("release_version") == "0.1.0"
    assert poll.get("ref") == "main"


def test_list_shortcut_projects_only_deploy_targets(monkeypatch):
    monkeypatch.setenv("JIRA_PROJECT_KEYS", "VFA,WAYW,BIG")
    monkeypatch.setenv(
        "BIGAS_DEPLOY_WORKFLOW_MAP",
        "VFA:deploy-backend.yml,deploy-web.yml|BIG:deploy.yml",
    )
    monkeypatch.setenv(
        "BIGAS_JIRA_PROJECT_REPO_MAP",
        "VFA:mckort/vcfieldassistant,WAYW:mckort/roadpal,BIG:mckort/bigas",
    )
    from bigas.resources.devops.prepare import list_shortcut_projects

    keys = [item["key"] for item in list_shortcut_projects()]
    assert keys == ["VFA", "BIG"]
    assert "WAYW" not in keys


def test_prepare_requires_project_and_version():
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    result = run_prepare_deploy(thread_id=thread["thread_id"], user_message="prepare deploy")
    assert result["status"] == "complete"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "project" in blob.lower() or "VFA" in blob


def test_finalize_asks_for_social_then_stores_x_draft(monkeypatch):
    monkeypatch.setattr(
        "bigas.tickets.releases.close_release",
        lambda *a, **k: {
            "already_released": False,
            "moved": [],
            "next_version": None,
            "github_release": {"html_url": "https://github.com/mckort/vcfieldassistant/releases/tag/v0.1.0"},
        },
    )
    class _FakeNotes:
        def create(self, **kwargs):
            return {
                "release_title": "Release 0.1.0",
                "customer_markdown": "- Meeting notes that remember",
                "blog_markdown": "Blog body",
                "social": {
                    "x": "VC Field Assistant 0.1.0 is out.",
                    "linkedin": "LinkedIn copy",
                    "facebook": "",
                    "instagram": "",
                },
            }

    monkeypatch.setattr(
        "bigas.resources.product.create_release_notes.service.CreateReleaseNotesService",
        _FakeNotes,
    )
    generated = {}

    class _FakeX:
        def generate(self, **kwargs):
            generated.update(kwargs)
            return {
                "review_url": "https://example.com/api/x-posts/abc?token=t",
                "posts": [{"account": "vcfieldassistan", "tweets": kwargs.get("tweets") or []}],
                "expires_hours": 48,
            }

    monkeypatch.setattr("bigas.resources.product.x_posts.service.XPostsService", _FakeX)
    monkeypatch.setattr(
        "bigas.resources.product.x_posts.service.format_discord_message",
        lambda result: f"Approve: {result.get('review_url')}",
    )

    from bigas.resources.devops.prepare import finalize_versioned_deploy, handle_release_notes_reply

    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    finalize_versioned_deploy(
        thread["thread_id"],
        {"project_key": "VFA", "release_version": "0.1.0", "ref": "main"},
    )
    pending = chat.get_thread(thread["thread_id"]).get("pending_release_notes")
    assert pending and pending["version"] == "0.1.0"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "Meeting notes that remember" in blob
    assert "social" in blob.lower() or "yes" in blob.lower()

    result = handle_release_notes_reply(thread_id=thread["thread_id"], user_message="ja")
    assert result["status"] == "complete"
    assert generated.get("tweets") == ["VC Field Assistant 0.1.0 is out."]
    later = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "Approve:" in later
    assert "LinkedIn" in later
