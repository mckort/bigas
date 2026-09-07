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
    _enrich_commits_with_pr_keys,
    compare_ahead_count,
    ensure_release_on_main,
    format_git_reconcile_report,
    format_main_ship_report,
    format_version_ticket_report,
    is_prepare_start,
    parse_prepare_command,
    release_commit_title,
    release_pr_body,
    release_pr_title,
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
    text, in_cut, open_tickets = format_version_ticket_report("VFA", "0.1.0")
    assert len(in_cut) == 1
    assert len(open_tickets) == 1
    assert "VFA-1" in text
    assert "VFA-2" in text
    assert "Open tickets" in text


def test_feature_report_includes_final_approval():
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Show last API activity per organization in admin",
        user_id="dev-user",
        key="VFA-48",
        fix_version="0.1.0",
        status="Final approval (manual)",
    )
    text, in_cut, open_tickets = format_version_ticket_report("VFA", "0.1.0")
    assert len(in_cut) == 1
    assert open_tickets == []
    assert "VFA-48" in text
    assert "Final approval" in text
    assert "not in this cut" not in text


def test_git_reconcile_matches_cut_and_flags_extras():
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    cut = store.create_ticket(
        board["board_id"],
        title="Show last API activity",
        user_id="dev-user",
        key="VFA-48",
        fix_version="0.1.0",
        status="Final approval (manual)",
    )
    store.create_ticket(
        board["board_id"],
        title="Next cut work",
        user_id="dev-user",
        key="VFA-99",
        fix_version="0.2.0",
        status="Done",
    )
    report = format_git_reconcile_report(
        project_key="VFA",
        version="0.1.0",
        in_cut=[cut],
        compared=["deploy-backend-abc → staging"],
        commits=[
            {
                "sha": "aaa1111bbbb",
                "message": "VFA-48: Show last API activity per organization in admin",
                "subject": "VFA-48: Show last API activity per organization in admin",
            },
            {
                "sha": "ccc2222dddd",
                "message": "chore: bump eslint",
                "subject": "chore: bump eslint",
            },
            {
                "sha": "eee3333ffff",
                "message": "VFA-99: work for the next cut",
                "subject": "VFA-99: work for the next cut",
            },
        ],
    )
    assert report["needs_confirm"] is True
    assert [row["key"] for row in report["matched"]] == ["VFA-48"]
    assert report["missing_from_git"] == []
    extras = [row["subject"] for row in report["extra_commits"]]
    assert "chore: bump eslint" in extras
    assert any("VFA-99" in row["reason"] for row in report["extra_commits"])
    assert "VFA-48" in report["text"]
    assert "Also shipping" in report["text"]


def test_git_reconcile_matches_cut_ticket_via_sha_in_description():
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    cut = store.create_ticket(
        board["board_id"],
        title="Add cherry-pick hotfix workflow for staging → main",
        user_id="dev-user",
        key="VFA-55",
        fix_version="0.2.2",
        status="Final approval (manual)",
        description="Already on staging (commit 2b45d00).",
    )
    report = format_git_reconcile_report(
        project_key="VFA",
        version="0.2.2",
        in_cut=[cut],
        compared=["deploy-backend-abc → staging"],
        commits=[
            {
                "sha": "2b45d003ea32204b73c935efa4d8cd5ad92bb1e6",
                "message": "Add cherry-pick hotfix workflow so Bigas can land staging fixes on main.",
                "subject": "Add cherry-pick hotfix workflow so Bigas can land staging fixes on main.",
            }
        ],
    )
    assert [row["key"] for row in report["matched"]] == ["VFA-55"]
    assert report["missing_from_git"] == []
    assert report["extra_commits"] == []


def test_git_reconcile_matches_unkeyed_commit_via_pr_keys():
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    cut = store.create_ticket(
        board["board_id"],
        title="Text in IOS still hard to read (headline)",
        user_id="dev-user",
        key="VFA-53",
        fix_version="0.2.2",
        status="Final approval (manual)",
    )
    report = format_git_reconcile_report(
        project_key="VFA",
        version="0.2.2",
        in_cut=[cut],
        compared=["deploy-web-abc → staging"],
        commits=[
            {
                "sha": "93b84ea11111111111111111111111111111111",
                "message": "[bigas-autofix] Use span wrappers for Gmail-safe HTML inside email headings",
                "subject": "[bigas-autofix] Use span wrappers for Gmail-safe HTML inside email headings",
                "pr_keys": ["VFA-53"],
            }
        ],
    )
    assert [row["key"] for row in report["matched"]] == ["VFA-53"]
    assert report["missing_from_git"] == []
    assert report["extra_commits"] == []


def test_enrich_commits_attaches_pr_keys(monkeypatch):
    class FakeClient:
        def list_pulls_for_commit(self, owner, repo, sha):
            assert owner == "mckort"
            assert repo == "vcfieldassistant"
            return [
                {
                    "title": "VFA-53: Fix meeting notes email headline contrast",
                    "body": "",
                    "head": {"ref": "fix/vfa-53-ios-headline"},
                }
            ]

    monkeypatch.setattr(
        "bigas.resources.devops.prepare._github_client",
        lambda: FakeClient(),
    )
    commits = [
        {
            "sha": "93b84ea11111111111111111111111111111111",
            "message": "[bigas-autofix] Use span wrappers",
            "subject": "[bigas-autofix] Use span wrappers",
        },
        {
            "sha": "46c103b11111111111111111111111111111111",
            "message": "VFA-53: Fix meeting notes email headline contrast",
            "subject": "VFA-53: Fix meeting notes email headline contrast",
        },
    ]
    enriched = _enrich_commits_with_pr_keys(
        project_key="VFA",
        repo="mckort/vcfieldassistant",
        commits=commits,
    )
    assert enriched[0]["pr_keys"] == ["VFA-53"]
    assert "pr_keys" not in enriched[1]


def test_git_reconcile_flags_cut_ticket_missing_from_git():
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    cut = store.create_ticket(
        board["board_id"],
        title="Not actually merged",
        user_id="dev-user",
        key="VFA-48",
        fix_version="0.1.0",
        status="Final approval (manual)",
    )
    report = format_git_reconcile_report(
        project_key="VFA",
        version="0.1.0",
        in_cut=[cut],
        compared=["deploy-web-abc → staging"],
        commits=[
            {
                "sha": "fff4444aaaa",
                "message": "docs: update readme",
                "subject": "docs: update readme",
            }
        ],
    )
    assert report["needs_confirm"] is True
    assert [t.get("key") for t in report["missing_from_git"]] == ["VFA-48"]
    assert "not in git" in report["text"]


def test_git_reconcile_skips_match_when_compare_fails():
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    cut = store.create_ticket(
        board["board_id"],
        title="Show last API activity",
        user_id="dev-user",
        key="VFA-48",
        fix_version="0.1.0",
        status="Final approval (manual)",
    )
    report = format_git_reconcile_report(
        project_key="VFA",
        version="0.1.0",
        in_cut=[cut],
        commits=[],
        errors=["GitHub auth failed"],
    )
    assert report["needs_confirm"] is True
    assert report["missing_from_git"] == []
    assert "Skipping ticket" in report["text"]


def _low_risk(**kwargs):
    return {
        "status": "ok",
        "summary": "Compared prod → main. No risky files.",
        "risk_level": "low",
        "findings": {},
        "repo": "mckort/vcfieldassistant",
        "head_ref": "main",
        "total_files_changed": 0,
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


def test_prepare_asks_when_final_approval_matches_git(monkeypatch):
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Show last API activity",
        user_id="dev-user",
        key="VFA-48",
        fix_version="0.1.0",
        status="Final approval (manual)",
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
        "bigas.resources.devops.prepare.list_shipping_commits",
        lambda **kwargs: {
            "commits": [
                {
                    "sha": "abc1234deadbeef",
                    "message": "VFA-48: Show last API activity per organization in admin",
                    "subject": "VFA-48: Show last API activity per organization in admin",
                }
            ],
            "compared": ["deploy-backend-old → staging"],
            "truncated": False,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.trigger_deployment",
        lambda **kwargs: triggered.update(called=True, ref=kwargs.get("ref"))
        or {
            "status": "ok",
            "summary": "Triggered 1 workflow(s).",
            "repo": "mckort/vcfieldassistant",
            "ref": "main",
            "triggered": [
                {"workflow": "deploy-backend.yml", "run_id": 46, "html_url": "https://x"}
            ],
            "errors": [],
            "site_urls": ["https://vcfieldassistant.com"],
        },
    )

    result = run_prepare_deploy(
        thread_id=thread["thread_id"],
        user_message="prepare deploy VFA 0.1.0",
    )
    assert triggered["called"] is False
    assert result["status"] == "complete"
    pending = chat.get_thread(thread["thread_id"]).get("pending_deploy")
    assert pending and pending.get("kind") == "prepare"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "VFA-48" in blob
    assert "In this cut and in git" in blob
    assert "yes" in blob.lower()
    assert "Starting deploy" not in blob


def test_prepare_asks_when_extra_commits_lack_tickets(monkeypatch):
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Show last API activity",
        user_id="dev-user",
        key="VFA-48",
        fix_version="0.1.0",
        status="Final approval (manual)",
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
        "bigas.resources.devops.prepare.list_shipping_commits",
        lambda **kwargs: {
            "commits": [
                {
                    "sha": "abc1234deadbeef",
                    "message": "VFA-48: Show last API activity",
                    "subject": "VFA-48: Show last API activity",
                },
                {
                    "sha": "fff9999cafebabe",
                    "message": "chore: leftover from another branch",
                    "subject": "chore: leftover from another branch",
                },
            ],
            "compared": ["deploy-web-old → staging"],
            "truncated": False,
            "errors": [],
        },
    )
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
    assert pending and pending.get("extra_commit_count") == 1
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "chore: leftover" in blob
    assert "yes" in blob.lower()


def test_prepare_asks_when_git_compare_fails(monkeypatch):
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Show last API activity",
        user_id="dev-user",
        key="VFA-48",
        fix_version="0.1.0",
        status="Final approval (manual)",
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
        "bigas.resources.devops.prepare.list_shipping_commits",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("GitHub auth failed")),
    )
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
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "Skipping ticket" in blob
    assert "yes" in blob.lower()


def test_git_reconcile_truncates_missing_list():
    in_cut = [
        {
            "key": f"VFA-{idx}",
            "title": f"Ticket {idx}",
            "status": "Done",
        }
        for idx in range(25)
    ]
    report = format_git_reconcile_report(
        project_key="VFA",
        version="0.1.0",
        in_cut=in_cut,
        compared=["deploy-web-abc → staging"],
        commits=[],
    )
    assert report["needs_confirm"] is True
    assert len(report["missing_from_git"]) == 25
    assert "…and 5 more" in report["text"]


def test_prepare_asks_on_low_risk_without_open_tickets(monkeypatch):
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
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.list_shipping_commits",
        lambda **kwargs: {
            "commits": [
                {
                    "sha": "abc1234deadbeef",
                    "message": "VFA-10: Done feature",
                    "subject": "VFA-10: Done feature",
                }
            ],
            "compared": ["deploy-backend-old → staging"],
            "truncated": False,
            "errors": [],
        },
    )

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
    assert triggered["called"] is False
    assert result["status"] == "complete"
    pending = chat.get_thread(thread["thread_id"]).get("pending_deploy")
    assert pending and pending.get("kind") == "prepare"
    assert pending.get("version") == "0.1.0"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "yes" in blob.lower()
    assert "Starting deploy" not in blob


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
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "Deploy VFA 0.1.0" in blob
    assert "deploy-backend.yml #55" in blob


def test_prepare_yes_wins_over_leftover_release_notes(monkeypatch):
    create_release("VFA", name="0.2.0")
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        pending_deploy={
            "kind": "prepare",
            "project_key": "VFA",
            "version": "0.2.0",
            "risk_level": "low",
            "repo": "mckort/vcfieldassistant",
        },
        pending_release_notes={
            "project_key": "VFA",
            "version": "0.1.0",
            "social": {"x": "Release 0.1.0 is here!"},
            "blog_markdown": "Old 0.1.0 blog",
        },
    )
    x_called = {"called": False}

    class _FakeX:
        def generate(self, **kwargs):
            x_called["called"] = True
            return {"posts": []}

    monkeypatch.setattr("bigas.resources.devops.pipeline.check_deployment_risk", _low_risk)
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.trigger_deployment",
        lambda **kwargs: {
            "status": "ok",
            "summary": "Triggered 1 workflow(s).",
            "repo": "mckort/vcfieldassistant",
            "ref": "main",
            "triggered": [
                {
                    "workflow": "deploy-backend.yml",
                    "run_id": 77,
                    "html_url": "https://github.com/mckort/vcfieldassistant/actions/runs/77",
                }
            ],
            "errors": [],
            "site_urls": ["https://vcfieldassistant.com"],
        },
    )
    monkeypatch.setattr("bigas.resources.product.x_posts.service.XPostsService", _FakeX)

    result = run_chat_deploy_pipeline(thread_id=thread["thread_id"], user_message="yes")
    assert result["status"] == "in_progress"
    assert x_called["called"] is False
    state = chat.get_thread(thread["thread_id"])
    assert state.get("pending_release_notes") is None
    poll = state.get("pending_deploy_poll")
    assert poll and poll.get("release_version") == "0.2.0"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "Deploy VFA 0.2.0" in blob
    assert "Release 0.1.0 is here" not in blob
    assert "New X post drafts" not in blob


def test_prepare_start_clears_leftover_release_notes(monkeypatch):
    create_release("VFA", name="0.2.0")
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        pending_release_notes={
            "project_key": "VFA",
            "version": "0.1.0",
            "social": {"x": "Release 0.1.0 is here!"},
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.ensure_release_on_main",
        lambda **kwargs: {"status": "already_on_main", "repo": "mckort/vcfieldassistant"},
    )
    monkeypatch.setattr("bigas.resources.devops.prepare.check_deployment_risk", _low_risk)
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.list_shipping_commits",
        lambda **kwargs: {"commits": [], "compared": [], "truncated": False, "errors": []},
    )

    run_prepare_deploy(thread_id=thread["thread_id"], user_message="prepare deploy VFA 0.2.0")
    state = chat.get_thread(thread["thread_id"])
    assert state.get("pending_release_notes") is None
    pending = state.get("pending_deploy")
    assert pending and pending.get("version") == "0.2.0"


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


def test_compare_ahead_count_uses_commits_and_files():
    assert compare_ahead_count({"ahead_by": 3}) == 3
    assert compare_ahead_count({"ahead_by": 0, "total_commits": 7}) == 7
    assert compare_ahead_count({"ahead_by": 0, "commits": [{}, {}]}) == 2
    assert compare_ahead_count({"ahead_by": 0, "files": [{"filename": "a.py"}]}) == 1
    assert compare_ahead_count({}) == 0


def test_release_pr_copy_includes_cut_keys():
    keys = ["VFA-56", "VFA-53"]
    title = release_pr_title("0.2.3", "staging", "main", keys)
    assert title.startswith("Release 0.2.3: merge staging into main")
    assert "VFA-56" in title
    body = release_pr_body("VFA", "0.2.3", "staging", "main", keys)
    assert "VFA-56" in body
    assert "VFA-53" in body
    assert release_commit_title("0.2.3", keys) == (
        "Release 0.2.3: merge into main (VFA-56, VFA-53)"
    )


def test_format_main_ship_report_lists_commits():
    text = format_main_ship_report(
        commits=[
            {
                "sha": "abc1234deadbeef",
                "subject": "Release 0.2.3: merge into main (VFA-56)",
            }
        ],
        compared=["deploy-backend-old → main"],
        files_changed=4,
    )
    assert "yes deploys this" in text
    assert "VFA-56" in text
    assert "4 file(s) changed" in text


class _FakeGitHub:
    def __init__(self, *, ahead_by=7, staging_ahead=7, created=None):
        self.ahead_by = ahead_by
        self.staging_ahead = staging_ahead
        self.created = created or {
            "number": 200,
            "html_url": "https://github.com/mckort/vcfieldassistant/pull/200",
        }
        self.created_prs = []
        self.compared = []

    def compare_refs(self, owner, repo, base, head):
        self.compared.append((base, head))
        ahead = self.staging_ahead if head == "staging" else self.ahead_by
        return {"ahead_by": ahead, "total_commits": ahead, "commits": [{}] * ahead}

    def find_open_pull_request(self, owner, repo, *, head, base):
        return None

    def create_pull_request(self, owner, repo, *, title, body, head, base):
        self.created_prs.append(
            {"title": title, "body": body, "head": head, "base": base}
        )
        return self.created


def test_ensure_release_opens_pr_with_cut_keys(monkeypatch):
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Meeting notes email still cramped",
        user_id="dev-user",
        key="VFA-56",
        fix_version="0.2.3",
        status="Final approval (manual)",
    )
    create_release("VFA", name="0.2.3")
    fake = _FakeGitHub()
    merged = {}

    monkeypatch.setattr(
        "bigas.resources.devops.prepare._project_repo",
        lambda project_key: "mckort/vcfieldassistant",
    )
    monkeypatch.setattr(
        "bigas.resources.devops.prepare._branch_pair",
        lambda project_key, repo: ("staging", "main"),
    )
    monkeypatch.setattr("bigas.resources.devops.prepare._github_client", lambda: fake)
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.review_and_merge_release_pr",
        lambda **kwargs: merged.update(kwargs) or {"status": "merged", **kwargs},
    )

    result = ensure_release_on_main(project_key="VFA", version="0.2.3")
    assert result["status"] == "merged"
    assert fake.created_prs
    pr = fake.created_prs[0]
    assert pr["head"] == "staging"
    assert pr["base"] == "main"
    assert "VFA-56" in pr["title"]
    assert "VFA-56" in pr["body"]
    assert merged["cut_keys"] == ["VFA-56"]


def test_ensure_release_falls_back_to_staging_when_mapped_to_main(monkeypatch):
    create_release("VFA", name="0.2.3")
    fake = _FakeGitHub(ahead_by=0, staging_ahead=4)
    opened = {}

    monkeypatch.setattr(
        "bigas.resources.devops.prepare._project_repo",
        lambda project_key: "mckort/vcfieldassistant",
    )
    monkeypatch.setattr(
        "bigas.resources.devops.prepare._branch_pair",
        lambda project_key, repo: ("main", "main"),
    )
    monkeypatch.setattr("bigas.resources.devops.prepare._github_client", lambda: fake)
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.review_and_merge_release_pr",
        lambda **kwargs: opened.update(kwargs) or {"status": "merged", **kwargs},
    )

    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    result = ensure_release_on_main(
        project_key="VFA", version="0.2.3", thread_id=thread["thread_id"]
    )
    assert result["status"] == "merged"
    assert fake.created_prs[0]["head"] == "staging"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "staging" in blob
    assert "ahead" in blob


def test_ensure_release_posts_when_already_on_main(monkeypatch):
    create_release("VFA", name="0.2.3")
    fake = _FakeGitHub(ahead_by=0, staging_ahead=0)

    monkeypatch.setattr(
        "bigas.resources.devops.prepare._project_repo",
        lambda project_key: "mckort/vcfieldassistant",
    )
    monkeypatch.setattr(
        "bigas.resources.devops.prepare._branch_pair",
        lambda project_key, repo: ("staging", "main"),
    )
    monkeypatch.setattr("bigas.resources.devops.prepare._github_client", lambda: fake)

    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    result = ensure_release_on_main(
        project_key="VFA", version="0.2.3", thread_id=thread["thread_id"]
    )
    assert result["status"] == "already_on_main"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "not ahead" in blob
    assert "staging" in blob


def test_prepare_stops_when_merge_skipped_and_cut_missing(monkeypatch):
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Meeting notes email still cramped",
        user_id="dev-user",
        key="VFA-56",
        fix_version="0.2.3",
        status="Final approval (manual)",
    )
    create_release("VFA", name="0.2.3")
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")

    monkeypatch.setattr(
        "bigas.resources.devops.prepare.ensure_release_on_main",
        lambda **kwargs: {"status": "already_on_main", "repo": "mckort/vcfieldassistant"},
    )
    monkeypatch.setattr("bigas.resources.devops.prepare.check_deployment_risk", _low_risk)
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.list_shipping_commits",
        lambda **kwargs: {
            "commits": [],
            "compared": ["deploy-backend-old → main"],
            "truncated": False,
            "errors": [],
        },
    )

    result = run_prepare_deploy(
        thread_id=thread["thread_id"],
        user_message="prepare deploy VFA 0.2.3",
    )
    assert result["status"] == "complete"
    state = chat.get_thread(thread["thread_id"])
    assert state.get("pending_deploy") is None
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "will not ask to deploy" in blob
    assert "On `main` now" in blob
    assert "Reply **yes**" not in blob


def test_prepare_after_merge_shows_main_commits(monkeypatch):
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="Meeting notes email still cramped",
        user_id="dev-user",
        key="VFA-56",
        fix_version="0.2.3",
        status="Final approval (manual)",
    )
    create_release("VFA", name="0.2.3")
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")

    monkeypatch.setattr(
        "bigas.resources.devops.prepare.ensure_release_on_main",
        lambda **kwargs: {"status": "merged", "repo": "mckort/vcfieldassistant"},
    )
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.check_deployment_risk",
        lambda **kwargs: {**_low_risk(), "total_files_changed": 6},
    )
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.list_shipping_commits",
        lambda **kwargs: {
            "commits": [
                {
                    "sha": "cafe1234deadbeef",
                    "message": "Release 0.2.3: merge into main (VFA-56)",
                    "subject": "Release 0.2.3: merge into main (VFA-56)",
                }
            ],
            "compared": ["deploy-web-old → main"],
            "truncated": False,
            "errors": [],
        },
    )

    result = run_prepare_deploy(
        thread_id=thread["thread_id"],
        user_message="prepare deploy VFA 0.2.3",
    )
    assert result["status"] == "complete"
    pending = chat.get_thread(thread["thread_id"]).get("pending_deploy")
    assert pending and pending.get("kind") == "prepare"
    blob = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "On `main` now" in blob
    assert "VFA-56" in blob
    assert "6 file(s) changed" in blob
    assert "yes" in blob.lower()


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
    assert "VFA 0.1.0" in blob
    assert "social" in blob.lower() or "yes" in blob.lower()

    result = handle_release_notes_reply(thread_id=thread["thread_id"], user_message="ja")
    assert result["status"] == "complete"
    assert generated.get("tweets") == ["VC Field Assistant 0.1.0 is out."]
    later = "\n".join(m["content"] for m in chat.list_messages(thread["thread_id"]))
    assert "Approve:" in later
    assert "LinkedIn" in later
