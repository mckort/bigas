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
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")

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


def _gpw_staging_status_client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _gpw_staging_status_headers():
    return {"X-Bigas-Webhook-Secret": os.environ["GITHUB_WEBHOOK_SECRET"]}


def test_gpw_staging_status_route_unauthorized():
    client = _gpw_staging_status_client()
    resp = client.post(
        "/mcp/tools/gpw_staging_status",
        json={"message": "Update staging: ok."},
        headers={"X-Bigas-Webhook-Secret": "wrong-secret"},
    )
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthorized"


def test_gpw_staging_status_route_missing_message():
    client = _gpw_staging_status_client()
    resp = client.post(
        "/mcp/tools/gpw_staging_status",
        json={},
        headers=_gpw_staging_status_headers(),
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "message is required"


def test_gpw_staging_status_route_truncates_long_message(monkeypatch):
    captured = {}

    def _capture(message):
        captured["message"] = message

    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.post_staging_status",
        _capture,
    )
    client = _gpw_staging_status_client()
    long_message = "x" * 600
    resp = client.post(
        "/mcp/tools/gpw_staging_status",
        json={"message": long_message},
        headers=_gpw_staging_status_headers(),
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert captured["message"] == ("x" * 497) + "..."
    assert len(captured["message"]) == 500


def test_gpw_staging_status_posts_activity_and_discord(monkeypatch):
    posted = {}

    def _capture(url, message, **kwargs):
        posted["url"] = url
        posted["message"] = message
        posted["kwargs"] = kwargs
        return True

    monkeypatch.setenv("DISCORD_WEBHOOK_URL_DEVOPS", "https://discord.test/devops")
    monkeypatch.setattr("bigas.discord_webhook.post_to_discord", _capture)
    from bigas.resources.devops.gpw_pipeline import post_staging_status

    post_staging_status("Update staging: building develop image abc1234.")
    assert posted["message"] == "Update staging: building develop image abc1234."
    assert posted["url"] == "https://discord.test/devops"
    assert posted["kwargs"]["mirror_thread"] is False
    assert posted["kwargs"]["chat_agent_id"] == "devops"


def test_update_staging_requires_prepare(monkeypatch):
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="update staging",
    )
    assert result["status"] == "complete"
    assert "prepare staging" in _texts(thread["thread_id"]).lower()


def test_update_staging_follows_develop_when_already_ready(monkeypatch):
    dispatched = {}

    class _Client:
        def get_ref_sha(self, owner, name, branch):
            return "fff123456789"

    monkeypatch.setattr("bigas.resources.devops.gpw_pipeline._github", lambda: _Client())
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.dispatch_gpw_workflow",
        lambda phase, inputs=None, **_kwargs: dispatched.update(phase=phase, inputs=inputs)
        or {
            "workflow": "update-staging.yml",
            "run_id": 8,
            "html_url": "https://example.test/8",
            "phase": phase,
            "inputs": inputs,
        },
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        gpw_rehearsal={
            "project_key": "GPW-PROD",
            "candidate_sha": "def123456789",
            "production_sha": "abc123456789",
            "staging_ready": True,
            "updated_ok": False,
            "updated_sha": "",
        },
    )
    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="update staging",
    )
    assert result.get("deploy_poll_active") is True
    assert dispatched["phase"] == "update_staging"
    assert dispatched["inputs"]["image_sha"] == "fff123456789"
    text = _texts(thread["thread_id"]).lower()
    assert "copying the database again" in text
    assert "run **prepare staging" not in text


def test_prepare_staging_dispatches_after_clean_review(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        lambda env=None, **_kwargs: {
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


def test_production_deploy_success_clears_rehearsal(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service.get_deployment_status",
        lambda **kwargs: {
            "workflow_status": "completed",
            "conclusion": "success",
            "html_url": "https://example.test/11",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline._github",
        lambda: type(
            "Client",
            (),
            {
                "update_branch_ref": staticmethod(lambda *args, **kwargs: None),
            },
        )(),
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        pending_deploy_poll={
            "kind": "gpw",
            "phase": "deploy_production",
            "triggered": [{"workflow": "deploy-production.yml", "run_id": 11}],
            "candidate_sha": "def123456789",
            "started_at": _started(),
        },
        gpw_rehearsal={
            "candidate_sha": "def123456789",
            "production_sha": "abc123456789",
            "staging_ready": True,
            "updated_ok": True,
            "updated_sha": "def123456789",
        },
    )
    poll_gpw(thread["thread_id"])
    rehearsal = chat.get_thread(thread["thread_id"])["gpw_rehearsal"]
    assert rehearsal["updated_ok"] is False
    assert rehearsal["updated_sha"] == ""
    assert rehearsal["staging_ready"] is False


def test_poll_waits_for_run_id_when_dispatch_late(monkeypatch):
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        pending_deploy_poll={
            "kind": "gpw",
            "phase": "prepare_staging",
            "repo": "Green-Promo-Wear-Global/GPW",
            "triggered": [{"workflow": "prepare-staging.yml", "run_id": None}],
            "started_at": _started(),
        },
    )
    result = poll_gpw(thread["thread_id"])
    assert result["active"] is True
    assert chat.get_thread(thread["thread_id"]).get("pending_deploy_poll")


def test_poll_resolves_missing_run_id(monkeypatch):
    class _Client:
        def get_default_branch(self, owner, name):
            return "develop"

        def list_workflow_runs(self, owner, name, workflow, branch=None, limit=5):
            return [{"id": 99, "created_at": _started()}]

    monkeypatch.setattr("bigas.resources.devops.gpw_pipeline._github", lambda: _Client())
    monkeypatch.setattr(
        "bigas.resources.devops.service.get_deployment_status",
        lambda **kwargs: {
            "workflow_status": "completed",
            "conclusion": "success",
            "html_url": "https://example.test/99",
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
            "triggered": [{"workflow": "prepare-staging.yml", "run_id": None}],
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
    poll = chat.get_thread(thread["thread_id"]).get("pending_deploy_poll")
    assert poll is None
    assert chat.get_thread(thread["thread_id"])["gpw_rehearsal"]["staging_ready"] is True


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


_DIRTY = {
    "ok": False,
    "review": "### Blockers\n- Cancel is wrong.\n",
    "pr_number": 9,
    "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/9",
    "production_sha": "abc123456789",
    "candidate_sha": "def123456789",
}
_CLEAN = {
    "ok": True,
    "review": "No issues.",
    "pr_number": 9,
    "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/9",
    "production_sha": "abc123456789",
    "candidate_sha": "fff123456789",
}


class _Autofix:
    def __init__(self, status):
        self.status = status
        self.runs = []

    def run(self, **kwargs):
        self.runs.append(kwargs)
        return {
            "launched": True,
            "skipped": False,
            "agent_id": "agent-1",
            "agent_url": "https://cursor.com/agents/agent-1",
            "run_id": "run-1",
            "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/9",
        }

    def poll_status(self, **kwargs):
        return self.status


def _silence_review_side_effects(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline._post_staging_review_comment",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline._autofix_counts",
        lambda *args, **kwargs: (0, 0),
    )


def test_prepare_staging_dirty_review_waits_for_autofix(monkeypatch):
    _silence_review_side_effects(monkeypatch)
    autofix = _Autofix({"done": False, "ok": False, "status": "RUNNING"})
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        lambda env=None, **_kwargs: dict(_DIRTY),
    )
    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.AutofixService",
        lambda: autofix,
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="prepare staging",
    )
    assert result.get("deploy_poll_active") is True
    text = _texts(thread["thread_id"])
    assert "up to 5 rounds" in text
    assert "Autofix started on the open PR" in text
    poll = chat.get_thread(thread["thread_id"])["pending_deploy_poll"]
    assert poll["phase"] == "review_autofix"
    assert poll["rounds_started"] == 1
    assert poll["pr_number"] == 9
    assert "Cancel is wrong" in (poll.get("pending_review_body") or "")
    assert len(autofix.runs) == 1
    assert "Cancel is wrong" in autofix.runs[0]["review_body"]


def test_prepare_staging_rereviews_after_autofix_and_builds(monkeypatch):
    _silence_review_side_effects(monkeypatch)
    reviews = [dict(_DIRTY), dict(_CLEAN)]
    seen = {}

    def _review(env=None, **kwargs):
        seen["phase"] = kwargs.get("phase")
        seen["previous"] = kwargs.get("previous_review")
        return reviews.pop(0)

    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        _review,
    )
    dispatched = {}
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.dispatch_gpw_workflow",
        lambda phase, inputs=None, **_kwargs: dispatched.update(
            {"phase": phase, "inputs": inputs}
        )
        or {"workflow": "prepare-staging.yml", "run_id": 7, "html_url": "https://example.test/7"},
    )
    autofix = _Autofix(
        {
            "done": True,
            "ok": True,
            "status": "FINISHED",
            "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/9",
        }
    )
    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.AutofixService",
        lambda: autofix,
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    started = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="prepare staging",
    )
    assert started.get("deploy_poll_active") is True
    finished = poll_gpw(thread["thread_id"])
    assert finished.get("deploy_poll_active") is True
    assert dispatched["phase"] == "prepare_staging"
    assert dispatched["inputs"]["candidate_sha"] == "fff123456789"
    assert "Review is clean" in _texts(thread["thread_id"])
    assert seen["phase"] == "post_autofix"
    assert "Cancel is wrong" in (seen["previous"] or "")


def test_prepare_staging_starts_another_round_when_review_stays_dirty(monkeypatch):
    _silence_review_side_effects(monkeypatch)
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        lambda env=None, **_kwargs: dict(_DIRTY),
    )
    autofix = _Autofix(
        {"done": True, "ok": True, "status": "FINISHED", "pr_url": ""}
    )
    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.AutofixService",
        lambda: autofix,
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="prepare staging",
    )
    result = poll_gpw(thread["thread_id"])
    assert result.get("deploy_poll_active") is True
    poll = chat.get_thread(thread["thread_id"])["pending_deploy_poll"]
    assert poll["rounds_started"] == 2
    assert "round 2/5" in _texts(thread["thread_id"])
    assert len(autofix.runs) == 2


def test_prepare_staging_stops_at_five_autofix_rounds(monkeypatch):
    _silence_review_side_effects(monkeypatch)
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        lambda env=None, **_kwargs: dict(_DIRTY),
    )
    autofix = _Autofix(
        {"done": True, "ok": True, "status": "FINISHED", "pr_url": ""}
    )
    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.AutofixService",
        lambda: autofix,
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        pending_deploy_poll={
            "kind": "gpw",
            "phase": "review_autofix",
            "project_key": "GPW-PROD",
            "repo": "Green-Promo-Wear-Global/GPW",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "pr_number": 9,
            "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/9",
            "follows_new_pr": False,
            "rounds_started": 5,
            "started_at": _started(),
            "triggered": [],
        },
        has_pending_deploy_poll=True,
    )
    result = poll_gpw(thread["thread_id"])
    assert result["active"] is False
    assert autofix.runs == []
    text = _texts(thread["thread_id"])
    assert "Autofix stopped after 5 rounds" in text
    assert "manual handling" in text
    assert chat.get_thread(thread["thread_id"]).get("pending_deploy_poll") is None


def test_prepare_staging_accepts_leftover_nits_at_round_cap(monkeypatch):
    _silence_review_side_effects(monkeypatch)
    nits = {
        "ok": False,
        "review": (
            "### Blockers\nNone.\n\n### Important\nNone.\n\n"
            "### Minor\n- Rename the helper.\n"
        ),
        "production_sha": "abc123456789",
        "candidate_sha": "fff123456789",
    }
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        lambda env=None, **_kwargs: dict(nits),
    )
    dispatched = {}
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.dispatch_gpw_workflow",
        lambda phase, inputs=None, **_kwargs: dispatched.update({"phase": phase})
        or {"workflow": "prepare-staging.yml", "run_id": 12, "html_url": "https://example.test/12"},
    )

    class _Done:
        def poll_status(self, **kwargs):
            return {"done": True, "ok": True, "status": "FINISHED", "pr_url": ""}

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.AutofixService",
        lambda: _Done(),
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    chat.patch_thread(
        thread["thread_id"],
        pending_deploy_poll={
            "kind": "gpw",
            "phase": "review_autofix",
            "project_key": "GPW-PROD",
            "repo": "Green-Promo-Wear-Global/GPW",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "follows_new_pr": False,
            "rounds_started": 5,
            "started_at": _started(),
            "triggered": [],
        },
        has_pending_deploy_poll=True,
    )
    result = poll_gpw(thread["thread_id"])
    assert result.get("deploy_poll_active") is True
    assert dispatched["phase"] == "prepare_staging"
    assert "Review is clean" in _texts(thread["thread_id"])
    assert "manual handling" not in _texts(thread["thread_id"])


def test_prepare_staging_reports_a_skipped_autofix(monkeypatch):
    _silence_review_side_effects(monkeypatch)
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        lambda env=None, **_kwargs: dict(_DIRTY),
    )

    class _Skipped:
        def run(self, **kwargs):
            return {
                "skipped": True,
                "launched": False,
                "loop_protection": True,
                "reason": "Exceeded autofix limit of 5 (found 5 `[bigas-autofix]` commits on this PR).",
            }

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.AutofixService",
        lambda: _Skipped(),
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    result = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="prepare staging",
    )
    assert result["status"] == "complete"
    text = _texts(thread["thread_id"])
    assert "Autofix did not start" in text
    assert "Autofix started" not in text


def test_prepare_staging_follows_a_new_fix_pr(monkeypatch):
    _silence_review_side_effects(monkeypatch)
    reviews = [
        {
            "ok": False,
            "review": "### Blockers\n- Cancel is wrong.\n",
            "production_sha": "abc123456789",
            "candidate_sha": "def123456789",
        },
        dict(_CLEAN),
    ]
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        lambda env=None, **_kwargs: reviews.pop(0),
    )
    monkeypatch.setattr(
        "bigas.resources.cto.deploy_hotfix.launch_failed_deploy_fix",
        lambda **kwargs: {
            "launched": True,
            "agent_id": "agent-9",
            "agent_url": "https://cursor.com/agents/agent-9",
            "run_id": "run-9",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline._review_fix_pull_request",
        lambda env, pr_number, phase="post_autofix": {
            "ok": True,
            "review": "No issues.",
            "pr_number": pr_number,
            "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/4",
        },
    )
    merged = {}

    class _GitHub:
        def merge_pull_request(self, owner, repo, pr_number, merge_method="squash"):
            merged["pr_number"] = pr_number
            merged["method"] = merge_method
            return {"merged": True}

    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline._github_pr_client",
        lambda: _GitHub(),
    )
    dispatched = {}
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.dispatch_gpw_workflow",
        lambda phase, inputs=None, **_kwargs: dispatched.update({"phase": phase, "inputs": inputs})
        or {"workflow": "prepare-staging.yml", "run_id": 8, "html_url": "https://example.test/8"},
    )

    class _Done:
        def poll_status(self, **kwargs):
            return {
                "done": True,
                "ok": True,
                "status": "FINISHED",
                "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/4",
            }

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.AutofixService",
        lambda: _Done(),
    )
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    started = run_chat_deploy_pipeline(
        thread_id=thread["thread_id"],
        user_message="prepare staging",
    )
    assert started.get("deploy_poll_active") is True
    poll = chat.get_thread(thread["thread_id"])["pending_deploy_poll"]
    assert poll["follows_new_pr"] is True
    assert poll["pr_number"] is None
    finished = poll_gpw(thread["thread_id"])
    assert finished.get("deploy_poll_active") is True
    assert merged == {"pr_number": 4, "method": "squash"}
    assert dispatched["inputs"]["candidate_sha"] == "fff123456789"
    assert "Merged the fix PR" in _texts(thread["thread_id"])


def test_merged_fix_pr_followup_posts_once_while_rereview_runs(monkeypatch):
    _silence_review_side_effects(monkeypatch)
    chat = get_chat_store()
    thread = chat.create_thread("user-1", "devops")
    thread_id = thread["thread_id"]
    nested = {}

    def review_candidate(env=None, **kwargs):
        nested["phase"] = kwargs.get("phase")
        nested["result"] = poll_gpw(thread_id)
        return dict(_CLEAN)

    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.review_candidate",
        review_candidate,
    )
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline._review_fix_pull_request",
        lambda env, pr_number, phase="post_autofix": {
            "ok": False,
            "merged": True,
            "review": "",
            "pr_number": pr_number,
            "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/122",
        },
    )
    monkeypatch.setattr(
        "bigas.resources.devops.gpw_pipeline.dispatch_gpw_workflow",
        lambda phase, inputs=None, **_kwargs: {
            "workflow": "prepare-staging.yml",
            "run_id": 11,
            "html_url": "https://example.test/11",
        },
    )

    class _Done:
        def poll_status(self, **kwargs):
            return {
                "done": True,
                "ok": True,
                "status": "FINISHED",
                "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/122",
            }

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.AutofixService",
        lambda: _Done(),
    )
    chat.patch_thread(
        thread_id,
        pending_deploy_poll={
            "kind": "gpw",
            "phase": "review_autofix",
            "project_key": "GPW-PROD",
            "repo": "Green-Promo-Wear-Global/GPW",
            "agent_id": "agent-1",
            "run_id": "run-1",
            "pr_number": 122,
            "pr_url": "https://github.com/Green-Promo-Wear-Global/GPW/pull/122",
            "follows_new_pr": True,
            "rounds_started": 2,
            "started_at": _started(),
            "triggered": [],
        },
        has_pending_deploy_poll=True,
    )
    result = poll_gpw(thread_id)
    text = _texts(thread_id)
    assert text.count("Fix PR already merged") == 1
    assert nested["result"]["active"] is True
    assert nested["phase"] == "post_autofix"
    assert result.get("deploy_poll_active") is True
    assert "Review is clean" in text
