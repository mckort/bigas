"""GPW-PROD staging rehearsal and production deploy chat flow."""
from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")
os.environ.setdefault("GITHUB_TOKEN", "test-github-token")

from bigas.chat.db import get_chat_store
from bigas.portfolio import (
    ISSUE_KEY_RE,
    project_key_from_issue_key,
    resolve_project,
)
from bigas.resources.devops.gpw_pipeline import (
    keep_maintenance,
    list_gpw_command_shortcuts,
    parse_gpw_command,
    poll_gpw,
)
from bigas.resources.devops.pipeline import run_chat_deploy_pipeline


def _started() -> str:
    return datetime.now(timezone.utc).isoformat()


def _texts(thread_id: str) -> str:
    return "\n".join(
        m.get("content") or "" for m in get_chat_store().list_messages(thread_id)
    )


def test_issue_key_keeps_gpw_prod_project():
    assert project_key_from_issue_key("GPW-PROD-12") == "GPW-PROD"
    assert project_key_from_issue_key("VFA-12") == "VFA"
    assert ISSUE_KEY_RE.match("GPW-PROD-12")
    assert ISSUE_KEY_RE.match("VFA-12")
    assert resolve_project("traffic to store.greenpromowear.com") == "GPW-PROD"
    assert resolve_project("traffic to greenpromowear.com") == "GPWW"


def test_prepare_deploy_gpw_does_not_use_versioned_prepare(monkeypatch):
    called = {"prepare": False}

    def _boom(**kwargs):
        called["prepare"] = True
        raise AssertionError("versioned prepare deploy should not run")

    monkeypatch.setattr("bigas.resources.devops.prepare.run_prepare_deploy", _boom)
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        gpw_rehearsal={"updated_ok": False},
    )
    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="prepare deploy GPW-PROD",
    )
    assert called["prepare"] is False
    assert result["status"] == "complete"
    assert "update staging" in _texts(thread["thread_id"]).lower()


def test_teardown_waits_for_yes_then_dispatches(monkeypatch):
    dispatched = {}

    def _dispatch(phase, inputs=None, **_kwargs):
        dispatched["phase"] = phase
        dispatched["inputs"] = inputs
        return {"workflow": "teardown-staging.yml", "run_id": 42, "html_url": "https://example.test/42"}

    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.dispatch_gpw_workflow",
        _dispatch,
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    first = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="teardown staging",
    )
    assert first["status"] == "complete"
    assert "phase" not in dispatched
    assert "yes" in _texts(thread["thread_id"]).lower()

    second = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="yes",
    )
    assert second.get("deploy_poll_active") is True
    assert dispatched["phase"] == "teardown"
    assert dispatched["inputs"] == {"confirm": "yes"}


def test_update_staging_requires_prepare(monkeypatch):
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="update staging",
    )
    assert result["status"] == "complete"
    assert "prepare staging" in _texts(thread["thread_id"]).lower()


def test_prepare_staging_dispatches_after_clean_review(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        lambda env=None: {
            "ok": True,
            "production_sha": "abc123456789",
            "candidate_sha": "def123456789",
            "review": "No issues.",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.dispatch_gpw_workflow",
        lambda phase, inputs=None, **_kwargs: {
            "workflow": "prepare-staging.yml",
            "run_id": 7,
            "html_url": "https://example.test/7",
            "phase": phase,
            "inputs": inputs,
        },
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="prepare staging",
    )
    assert result.get("deploy_poll_active") is True
    rehearsal = chat.get_thread(thread["thread_id"])["gpw_rehearsal"]
    assert rehearsal["production_sha"] == "abc123456789"
    assert rehearsal["staging_ready"] is False


def test_poll_marks_rehearsal_ready(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service.get_deployment_status",
        lambda **kwargs: {
            "workflow_status": "completed",
            "conclusion": "success",
            "html_url": "https://example.test/7",
        },
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        pending_deploy_poll={
            "kind": "gpw",
            "phase": "prepare_staging",
            "repo": "Green-Promo-Wear-Global/GPW",
            "triggered": [{"workflow": "prepare-staging.yml", "run_id": 7}],
            "production_sha": "abc123456789",
            "candidate_sha": "def123456789",
            "started_at": _started(),
        },
        gpw_rehearsal={
            "candidate_sha": "def123456789",
            "production_sha": "abc123456789",
            "staging_ready": False,
            "updated_ok": False,
        },
    )
    result = poll_gpw(thread["thread_id"])
    assert result["active"] is False
    rehearsal = chat.get_thread(thread["thread_id"])["gpw_rehearsal"]
    assert rehearsal["staging_ready"] is True
    assert "staging.greenpromowear.com" in _texts(thread["thread_id"])


def test_failed_production_deploy_keeps_maintenance_message(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service.get_deployment_status",
        lambda **kwargs: {
            "workflow_status": "completed",
            "conclusion": "failure",
            "html_url": "https://example.test/9",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.service.get_failed_run_excerpt",
        lambda **kwargs: {"excerpt": "migrate failed"},
    )
    monkeypatch.setattr(
        "bigas.resources.cto.deploy_hotfix.launch_failed_deploy_fix",
        lambda **kwargs: {"agent_url": "https://cursor.test/agent"},
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        pending_deploy_poll={
            "kind": "gpw",
            "phase": "deploy_production",
            "triggered": [{"workflow": "deploy-production.yml", "run_id": 9}],
            "candidate_sha": "def123456789",
            "started_at": _started(),
        },
    )
    poll_gpw(thread["thread_id"])
    text = _texts(thread["thread_id"])
    assert "Maintenance stays on" in text
    assert "cursor.test/agent" in text


def test_keep_maintenance_until_check_is_green():
    assert keep_maintenance(health_ok=False) is True
    assert keep_maintenance(health_ok=True) is False


def test_command_shortcuts_are_only_gpw_prod():
    groups = list_gpw_command_shortcuts()
    assert [group["key"] for group in groups] == ["GPW-PROD"]
    assert [item["prompt"] for item in groups[0]["commands"]] == [
        "prepare staging GPW-PROD",
        "update staging GPW-PROD",
        "teardown staging GPW-PROD",
        "prepare deploy GPW-PROD",
    ]


def test_bare_staging_command_asks_when_several_projects(monkeypatch):
    from bigas.resources.devops.gpw_pipeline import StagingEnv

    other = StagingEnv(
        project_key="DEMO",
        repo="example/demo",
        candidate_branch="develop",
        production_branch="main",
        staging_url="https://staging.example.test",
        production_url="https://example.test",
        workflows={
            "prepare_staging": "prepare-staging.yml",
            "update_staging": "update-staging.yml",
            "teardown": "teardown-staging.yml",
            "backup": "backup-production.yml",
            "deploy_production": "deploy-production.yml",
        },
    )
    gpw = list_gpw_command_shortcuts()[0]
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.staging_envs",
        lambda: {"GPW-PROD": other, "DEMO": other},
    )
    # The shortcut list follows staging_envs, so two groups appear.
    assert len(list_gpw_command_shortcuts()) == 2
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="prepare staging",
    )
    assert result["status"] == "complete"
    assert "which project" in _texts(thread["thread_id"]).lower()
    assert gpw["key"] == "GPW-PROD"


def test_parse_commands():
    assert parse_gpw_command("prepare staging") == "prepare_staging"
    assert parse_gpw_command("please update staging") == "update_staging"
    assert parse_gpw_command("teardown staging") == "teardown"
    assert parse_gpw_command("prepare deploy GPW-PROD") == "prepare_deploy"
    assert parse_gpw_command("prepare deploy VFA 0.1.0") == ""


def test_staging_env_map_adds_a_project(monkeypatch):
    monkeypatch.setenv(
        "BIGAS_STAGING_ENV_MAP",
        '{"DEMO":{"repo":"example/demo","candidate_branch":"develop",'
        '"production_branch":"main","staging_url":"https://staging.example.test",'
        '"production_url":"https://example.test","workflows":{'
        '"prepare_staging":"prepare-staging.yml","update_staging":"update-staging.yml",'
        '"teardown":"teardown-staging.yml","backup":"backup-production.yml",'
        '"deploy_production":"deploy-production.yml"}}}',
    )
    from bigas.resources.devops.gpw_pipeline import staging_envs

    keys = list(staging_envs())
    assert keys == ["GPW-PROD", "DEMO"]
    assert staging_envs()["DEMO"].repo == "example/demo"
    assert parse_gpw_command("prepare deploy VFA") == ""
