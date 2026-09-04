"""Tests for DevOps chat deploy pipeline progress + confirmation."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")
os.environ.setdefault("GITHUB_TOKEN", "test-github-token")
os.environ.setdefault("BIGAS_JIRA_PROJECT_REPO_MAP", "VFA:mckort/vcfieldassistant")
os.environ.setdefault("BIGAS_DEPLOY_WORKFLOW_MAP", "VFA:deploy-backend.yml,deploy-web.yml")

from bigas.chat.db import get_chat_store
from bigas.resources.devops.pipeline import (
    clear_stale_pending_deploy,
    expire_stale_deploy_poll,
    is_confirm,
    is_deploy_start,
    poll_deploy_postcheck,
    resume_deploy_postcheck_from_workflow,
    run_chat_deploy_pipeline,
    should_run_deploy_pipeline,
)


def test_deploy_start_intent():
    assert is_deploy_start("deploya vcfieldassistant")
    assert is_deploy_start("Deploy VFA please")
    assert is_deploy_start("deploy vcfieldassistant")
    assert is_deploy_start("deploya bigas")
    assert is_deploy_start("deploy bigas")
    assert not is_deploy_start("hur går deployen")
    assert not is_deploy_start("kolla status på run 123")
    assert is_confirm("ja")
    assert is_confirm("Yes, kör")


def test_pipeline_posts_precheck_then_triggers(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    triggered = {"called": False}

    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_deployment_risk",
        lambda **kwargs: {
            "status": "ok",
            "summary": (
                "Currently deployed: prod backend deploy-backend-old. "
                "Compared deploy-backend-old → main on mckort/vcfieldassistant. "
                "12 file(s) changed. No migration or critical config changes detected."
            ),
            "risk_level": "low",
            "findings": {},
            "repo": "mckort/vcfieldassistant",
            "site_urls": ["https://vcfieldassistant.com"],
            "no_prod_version": False,
        },
    )

    def _trigger(**kwargs):
        triggered["called"] = True
        return {
            "status": "ok",
            "summary": "Triggered 2 workflow(s) on mckort/vcfieldassistant @ main.",
            "repo": "mckort/vcfieldassistant",
            "triggered": [
                {
                    "workflow": "deploy-backend.yml",
                    "run_id": 11,
                    "html_url": "https://github.com/mckort/vcfieldassistant/actions/runs/11",
                },
                {
                    "workflow": "deploy-web.yml",
                    "run_id": 12,
                    "html_url": "https://github.com/mckort/vcfieldassistant/actions/runs/12",
                },
            ],
            "errors": [],
            "site_urls": ["https://vcfieldassistant.com"],
        }

    monkeypatch.setattr("bigas.resources.devops.pipeline.trigger_deployment", _trigger)
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_deployment_status",
        lambda **kwargs: {
            "workflow_status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/example/run",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": f"{url} returned HTTP 200 in 40ms."},
    )

    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="deploya vcfieldassistant",
    )
    assert triggered["called"] is True
    assert result["status"] == "in_progress"
    assert result.get("deploy_poll_active") is True
    poll = store.get_thread(thread["thread_id"]).get("pending_deploy_poll")
    assert poll and poll.get("triggered")

    poll_result = poll_deploy_postcheck(thread["thread_id"])
    assert poll_result["status"] == "complete"
    assert store.get_thread(thread["thread_id"]).get("pending_deploy_poll") is None

    contents = [m["content"] for m in store.list_messages(thread["thread_id"])]
    blob = "\n".join(contents)
    assert "Pre-check" in blob
    assert "12 file(s) changed" in blob
    assert "Deploy" in blob
    assert "Triggered 2 workflow(s)" in blob
    assert "Post-check" in blob
    assert "HTTP 200" in blob


def test_pipeline_asks_confirmation_on_high_risk(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    triggered = {"called": False}

    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_deployment_risk",
        lambda **kwargs: {
            "status": "ok",
            "summary": "Compared prod → main. 2 file(s) changed. Warnings: 1 database migration file(s) changed.",
            "risk_level": "high",
            "findings": {"database_migration": ["db/migrations/002.sql"]},
            "repo": "mckort/vcfieldassistant",
            "site_urls": [],
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.trigger_deployment",
        lambda **kwargs: triggered.update(called=True) or {},
    )

    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="deploya VFA",
    )
    assert triggered["called"] is False
    assert result["status"] == "complete"
    pending = store.get_thread(thread["thread_id"]).get("pending_deploy")
    assert pending and pending["risk_level"] == "high"
    contents = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "yes" in contents.lower()

    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.trigger_deployment",
        lambda **kwargs: {
            "status": "ok",
            "summary": "Triggered 1 workflow(s).",
            "repo": "mckort/vcfieldassistant",
            "triggered": [{"workflow": "deploy-backend.yml", "run_id": 99, "html_url": "https://x"}],
            "errors": [],
            "site_urls": [],
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_deployment_status",
        lambda **kwargs: {"workflow_status": "completed", "conclusion": "success", "html_url": "https://x"},
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": "ok"},
    )

    assert should_run_deploy_pipeline("ja", thread["thread_id"]) is True
    confirmed = run_chat_deploy_pipeline(thread_id=thread["thread_id"], user_message="ja")
    assert confirmed["status"] == "in_progress"
    assert store.get_thread(thread["thread_id"]).get("pending_deploy") is None
    poll_deploy_postcheck(thread["thread_id"])


def test_pipeline_requires_project_key(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")

    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="deploy please",
    )
    assert result["status"] == "complete"
    contents = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "VFA" in contents or "project key" in contents.lower()


def test_clear_stale_pending_on_unrelated_message(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    store.patch_thread(
        thread["thread_id"],
        pending_deploy={"project_key": "VFA", "risk_level": "high", "repo": "mckort/vcfieldassistant"},
    )

    assert should_run_deploy_pipeline("what is the status?", thread["thread_id"]) is False
    clear_stale_pending_deploy("what is the status?", thread["thread_id"])
    assert store.get_thread(thread["thread_id"]).get("pending_deploy") is None


def test_pipeline_marks_in_progress_messages_complete(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")

    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_deployment_risk",
        lambda **kwargs: {
            "status": "ok",
            "summary": "Compared prod → main.",
            "risk_level": "low",
            "findings": {},
            "repo": "mckort/vcfieldassistant",
            "site_urls": [],
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.trigger_deployment",
        lambda **kwargs: {
            "status": "ok",
            "summary": "Triggered.",
            "repo": "mckort/vcfieldassistant",
            "triggered": [{"workflow": "deploy-backend.yml", "run_id": 1, "html_url": "https://x"}],
            "errors": [],
            "site_urls": [],
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_deployment_status",
        lambda **kwargs: {"workflow_status": "completed", "conclusion": "success", "html_url": "https://x"},
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": "ok"},
    )

    run_chat_deploy_pipeline(thread_id=thread["thread_id"], user_message="deploya VFA")
    poll_deploy_postcheck(thread["thread_id"])
    messages = store.list_messages(thread["thread_id"])
    progress = [
        m
        for m in messages
        if (m.get("metadata") or {}).get("pipeline")
        and (m.get("metadata") or {}).get("status") == "in_progress"
    ]
    assert progress == []
    completed = [
        m
        for m in messages
        if (m.get("metadata") or {}).get("pipeline")
        and (m.get("metadata") or {}).get("status") == "complete"
    ]
    assert len(completed) >= 2


def test_pipeline_handoffs_failed_web_deploy_to_cto(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    launched = {"called": False, "failures": None}

    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_deployment_risk",
        lambda **kwargs: {
            "status": "ok",
            "summary": "Compared prod → main.",
            "risk_level": "low",
            "findings": {},
            "repo": "mckort/vcfieldassistant",
            "site_urls": ["https://vcfieldassistant.com"],
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.trigger_deployment",
        lambda **kwargs: {
            "status": "ok",
            "summary": "Triggered 2 workflow(s).",
            "repo": "mckort/vcfieldassistant",
            "triggered": [
                {
                    "workflow": "deploy-backend.yml",
                    "run_id": 11,
                    "ref": "main",
                    "html_url": "https://github.com/mckort/vcfieldassistant/actions/runs/11",
                },
                {
                    "workflow": "deploy-web.yml",
                    "run_id": 12,
                    "ref": "main",
                    "html_url": "https://github.com/mckort/vcfieldassistant/actions/runs/12",
                },
            ],
            "errors": [],
            "site_urls": ["https://vcfieldassistant.com"],
        },
    )

    def _status(**kwargs):
        run_id = kwargs["run_id"]
        if run_id == 11:
            return {
                "workflow_status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/mckort/vcfieldassistant/actions/runs/11",
            }
        return {
            "workflow_status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/mckort/vcfieldassistant/actions/runs/12",
        }

    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_deployment_status",
        _status,
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": f"{url} returned HTTP 200 in 40ms."},
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_failed_run_excerpt",
        lambda **kwargs: {
            "excerpt": "Tsconfig not found expo/tsconfig.base",
            "summary": "failed",
        },
    )

    def _launch(**kwargs):
        launched["called"] = True
        launched["failures"] = kwargs.get("failures")
        launched["repo"] = kwargs.get("repo")
        return {
            "launched": True,
            "agent_url": "https://cursor.com/agents/bc-test",
            "agent_id": "bc-test",
            "summary": "launched",
        }

    monkeypatch.setattr(
        "bigas.resources.cto.deploy_hotfix.launch_failed_deploy_fix",
        _launch,
    )

    run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="deploya vcfieldassistant",
    )
    poll_deploy_postcheck(thread["thread_id"])

    assert launched["called"] is True
    assert launched["repo"] == "mckort/vcfieldassistant"
    assert launched["failures"] and launched["failures"][0]["run_id"] == 12
    assert "expo/tsconfig.base" in launched["failures"][0]["excerpt"]
    blob = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "Deploy failed — diagnosis" in blob
    assert "CTO agent launched" in blob
    assert "https://cursor.com/agents/bc-test" in blob


def test_pipeline_does_not_handoff_cancelled_runs(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    launched = {"called": False}

    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_deployment_risk",
        lambda **kwargs: {
            "status": "ok",
            "summary": "Compared prod → main.",
            "risk_level": "low",
            "findings": {},
            "repo": "mckort/vcfieldassistant",
            "site_urls": [],
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.trigger_deployment",
        lambda **kwargs: {
            "status": "ok",
            "summary": "Triggered.",
            "repo": "mckort/vcfieldassistant",
            "triggered": [
                {
                    "workflow": "deploy-web.yml",
                    "run_id": 99,
                    "ref": "main",
                    "html_url": "https://x",
                }
            ],
            "errors": [],
            "site_urls": [],
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_deployment_status",
        lambda **kwargs: {
            "workflow_status": "completed",
            "conclusion": "cancelled",
            "html_url": "https://x",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": "ok"},
    )
    monkeypatch.setattr(
        "bigas.resources.cto.deploy_hotfix.launch_failed_deploy_fix",
        lambda **kwargs: launched.update(called=True) or {},
    )

    run_chat_deploy_pipeline(thread_id=thread["thread_id"], user_message="deploya VFA")
    poll_deploy_postcheck(thread["thread_id"])
    assert launched["called"] is False
    blob = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "CTO agent launched" not in blob


def test_poll_posts_progress_before_postcheck(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    statuses = {
        11: {"workflow_status": "completed", "conclusion": "success", "html_url": "https://x/11"},
        12: {"workflow_status": "in_progress", "conclusion": None, "html_url": "https://x/12"},
    }

    store.patch_thread(
        thread["thread_id"],
        pending_deploy_poll={
            "repo": "mckort/vcfieldassistant",
            "product_repo": "mckort/vcfieldassistant",
            "triggered": [
                {"workflow": "deploy-backend.yml", "run_id": 11, "html_url": "https://x/11"},
                {"workflow": "deploy-web.yml", "run_id": 12, "html_url": "https://x/12"},
            ],
            "site_urls": ["https://vcfieldassistant.com"],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_lines": [],
            "failed_runs": [],
            "done_run_ids": [],
            "ref": "main",
            "project_key": "VFA",
            "release_version": "0.2.0",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_deployment_status",
        lambda **kwargs: statuses[kwargs["run_id"]],
    )
    notes = {"called": False}
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.finalize_versioned_deploy",
        lambda *a, **k: notes.update(called=True),
    )

    first = poll_deploy_postcheck(thread["thread_id"])
    assert first["status"] == "in_progress"
    assert notes["called"] is False
    blob = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "deploy-backend.yml run #11 success" in blob
    assert "Still running: deploy-web.yml #12" in blob
    assert "New X post drafts" not in blob
    assert "Want blog" not in blob

    statuses[12] = {
        "workflow_status": "completed",
        "conclusion": "success",
        "html_url": "https://x/12",
    }
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": f"{url} returned HTTP 200 in 40ms.", "is_healthy": True},
    )
    finalized = {}

    def _finalize(thread_id, poll):
        notes["called"] = True
        finalized.update(poll)

    monkeypatch.setattr(
        "bigas.resources.devops.prepare.finalize_versioned_deploy",
        _finalize,
    )
    second = poll_deploy_postcheck(thread["thread_id"])
    assert second["status"] == "complete"
    assert notes["called"] is True
    assert finalized.get("release_version") == "0.2.0"
    later = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "Post-check" in later
    assert "HTTP 200" in later


def test_unhealthy_site_skips_release_notes(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    store.patch_thread(
        thread["thread_id"],
        pending_deploy_poll={
            "repo": "mckort/vcfieldassistant",
            "triggered": [
                {"workflow": "deploy-backend.yml", "run_id": 11, "html_url": "https://x"}
            ],
            "site_urls": ["https://vcfieldassistant.com"],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_lines": [],
            "failed_runs": [],
            "done_run_ids": [],
            "ref": "main",
            "project_key": "VFA",
            "release_version": "0.2.0",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_deployment_status",
        lambda **kwargs: {
            "workflow_status": "completed",
            "conclusion": "success",
            "html_url": "https://x",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": f"{url} returned HTTP 503.", "is_healthy": False},
    )
    notes = {"called": False}
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.finalize_versioned_deploy",
        lambda *a, **k: notes.update(called=True),
    )

    result = poll_deploy_postcheck(thread["thread_id"])
    assert result["status"] == "complete"
    assert notes["called"] is False
    blob = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "skipping release notes" in blob.lower()
    assert "New X post drafts" not in blob


def test_finalize_timeout_skips_notes_without_nameerror(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    notes = {"called": False}
    monkeypatch.setattr(
        "bigas.resources.devops.prepare.finalize_versioned_deploy",
        lambda *a, **k: notes.update(called=True),
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": f"{url} returned HTTP 200.", "is_healthy": True},
    )

    from bigas.resources.devops.pipeline import _finalize_deploy_postcheck

    _finalize_deploy_postcheck(
        thread["thread_id"],
        {
            "repo": "mckort/vcfieldassistant",
            "triggered": [
                {"workflow": "deploy-backend.yml", "run_id": 11, "html_url": "https://x"}
            ],
            "site_urls": ["https://vcfieldassistant.com"],
            "finished_lines": [],
            "failed_runs": [],
            "done_run_ids": [],
            "ref": "main",
            "project_key": "VFA",
            "release_version": "0.2.0",
        },
        remaining=[{"workflow": "deploy-backend.yml", "run_id": 11}],
    )
    assert notes["called"] is False
    blob = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "Timeout" in blob or "still running" in blob.lower()
    assert "skipping release notes" in blob.lower()


def _pending_poll(run_id=12, started_at=None):
    return {
        "repo": "mckort/vcfieldassistant",
        "product_repo": "mckort/vcfieldassistant",
        "triggered": [
            {
                "workflow": "deploy-web.yml",
                "run_id": run_id,
                "html_url": f"https://x/{run_id}",
            }
        ],
        "site_urls": ["https://vcfieldassistant.com"],
        "started_at": started_at or datetime.now(timezone.utc).isoformat(),
        "finished_lines": [],
        "failed_runs": [],
        "done_run_ids": [],
        "ref": "main",
        "project_key": "VFA",
    }


def test_resume_workflow_completes_postcheck_without_client(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    store.patch_thread(thread["thread_id"], pending_deploy_poll=_pending_poll())
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_deployment_status",
        lambda **kwargs: {
            "workflow_status": "completed",
            "conclusion": "success",
            "html_url": "https://x/12",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": f"{url} returned HTTP 200.", "is_healthy": True},
    )

    result = resume_deploy_postcheck_from_workflow(
        {
            "action": "completed",
            "workflow_run": {"id": 12, "conclusion": "success"},
            "repository": {"name": "vcfieldassistant", "owner": {"login": "mckort"}},
        }
    )
    assert result["resumed"] == 1
    assert thread["thread_id"] in result["thread_ids"]
    assert store.get_thread(thread["thread_id"]).get("pending_deploy_poll") is None
    blob = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "Post-check" in blob


def test_resume_workflow_ignores_unrelated_run():
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    store.patch_thread(thread["thread_id"], pending_deploy_poll=_pending_poll(run_id=12))

    result = resume_deploy_postcheck_from_workflow(
        {
            "action": "completed",
            "workflow_run": {"id": 99, "conclusion": "success"},
            "repository": {"name": "vcfieldassistant", "owner": {"login": "mckort"}},
        }
    )
    assert result["resumed"] == 0
    assert store.get_thread(thread["thread_id"]).get("pending_deploy_poll")


def test_expire_stale_deploy_poll_finalizes(monkeypatch):
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    started = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.patch_thread(
        thread["thread_id"],
        pending_deploy_poll=_pending_poll(started_at=started),
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.get_deployment_status",
        lambda **kwargs: {
            "workflow_status": "in_progress",
            "conclusion": None,
            "html_url": "https://x/12",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.pipeline.check_website_health",
        lambda url: {"summary": f"{url} returned HTTP 200.", "is_healthy": True},
    )

    assert expire_stale_deploy_poll(thread["thread_id"]) is True
    assert store.get_thread(thread["thread_id"]).get("pending_deploy_poll") is None
    blob = "\n".join(m["content"] for m in store.list_messages(thread["thread_id"]))
    assert "Timeout" in blob or "still running" in blob.lower()


def test_expire_stale_deploy_poll_leaves_fresh_poll():
    store = get_chat_store()
    thread = store.create_thread("user-1", "devops")
    store.patch_thread(thread["thread_id"], pending_deploy_poll=_pending_poll())
    assert expire_stale_deploy_poll(thread["thread_id"]) is False
    assert store.get_thread(thread["thread_id"]).get("pending_deploy_poll")
