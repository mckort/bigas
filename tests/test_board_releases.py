"""Board releases, carry-forward, and deploy close (BIG-43)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas.tickets import store as ticket_store_module
from bigas.tickets.release_store import get_release_store, reset_release_store_for_tests
from bigas.tickets.releases import (
    ReleaseError,
    close_release,
    close_release_from_deploy_ref,
    create_release,
    delete_release,
    maybe_close_board_release_from_workflow,
    ship_release,
)
from bigas.tickets.semver import next_product_release, version_from_git_ref
from bigas.tickets.store import get_ticket_store
from bigas.tickets.jira_adapter import TicketJiraAdapter


@pytest.fixture(autouse=True)
def _reset_stores():
    ticket_store_module._store = None
    reset_release_store_for_tests()
    yield
    ticket_store_module._store = None
    reset_release_store_for_tests()


def test_semver_product_bump():
    assert next_product_release("0.9.0") == "0.10.0"
    assert next_product_release("0.9.1") == "0.10.0"
    assert next_product_release("1.0.0") == "1.1.0"
    assert version_from_git_ref("v0.9.0") == "0.9.0"
    assert version_from_git_ref("main") is None


def test_create_list_delete_release():
    created = create_release("VFA", name="0.9.0", is_default=True)
    assert created["name"] == "0.9.0"
    assert created["is_default"] is True
    assert created["released"] is False
    listed = get_release_store().list_releases("VFA")
    assert [item["name"] for item in listed] == ["0.9.0"]
    assert delete_release("VFA", created["release_id"]) is True
    assert get_release_store().list_releases("VFA") == []


def test_close_moves_open_tickets_to_next_minor(monkeypatch):
    monkeypatch.setattr("bigas.tickets.releases._publish_github_release", lambda *a, **k: None)
    posted = []
    monkeypatch.setattr(
        "bigas.chat.activity.post_to_agent_thread",
        lambda agent_id, content, **kwargs: posted.append((agent_id, content)),
    )

    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    open_ticket = store.create_ticket(
        board["board_id"],
        title="Still open",
        user_id="dev-user",
        key="VFA-201",
        fix_version="0.9.0",
        status="To Do",
    )
    done_ticket = store.create_ticket(
        board["board_id"],
        title="Shipped",
        user_id="dev-user",
        key="VFA-202",
        fix_version="0.9.0",
        status="Done",
    )
    create_release("VFA", name="0.9.0")

    result = close_release("VFA", "0.9.0", create_github=False)
    assert result["next_version"] == "0.10.0"
    assert [item["key"] for item in result["moved"]] == ["VFA-201"]
    assert store.get_ticket(open_ticket["ticket_id"])["fix_version"] == "0.10.0"
    assert store.get_ticket(done_ticket["ticket_id"])["fix_version"] == "0.9.0"
    assert posted and posted[0][0] == "devops"
    assert "VFA-201" in posted[0][1]
    assert "0.10.0" in posted[0][1]


def test_close_from_semver_deploy_ref(monkeypatch):
    publish_calls = []
    monkeypatch.setattr(
        "bigas.tickets.releases._publish_github_release",
        lambda *a, **k: publish_calls.append((a, k)) or None,
    )
    monkeypatch.setattr("bigas.chat.activity.post_to_agent_thread", lambda *a, **k: None)
    create_release("VFA", name="0.9.0")
    assert close_release_from_deploy_ref("VFA", "main") is None
    closed = close_release_from_deploy_ref("VFA", "v0.9.0")
    assert closed["release"]["released"] is True
    assert publish_calls == []


def test_close_release_already_released_skips_github(monkeypatch):
    publish_calls = []
    monkeypatch.setattr(
        "bigas.tickets.releases._publish_github_release",
        lambda *a, **k: publish_calls.append(1),
    )
    monkeypatch.setattr("bigas.chat.activity.post_to_agent_thread", lambda *a, **k: None)
    create_release("VFA", name="0.9.0")
    close_release("VFA", "0.9.0", create_github=False)
    result = close_release("VFA", "0.9.0", create_github=True)
    assert result["already_released"] is True
    assert publish_calls == []


def test_list_releases_newest_first():
    create_release("VFA", name="0.9.0")
    create_release("VFA", name="0.10.0")
    create_release("VFA", name="0.8.0")
    names = [item["name"] for item in get_release_store().list_releases("VFA")]
    assert names == ["0.10.0", "0.9.0", "0.8.0"]


def test_ship_release_retries_deploy_after_partial_failure(monkeypatch):
    publish_calls = []
    deploy_calls = []

    def _publish(*args, **kwargs):
        publish_calls.append(1)
        return {"tag_name": "v0.9.0", "html_url": "https://github.example/release"}

    def _deploy(**kwargs):
        deploy_calls.append(kwargs)
        if len(deploy_calls) == 1:
            from bigas.resources.devops.service import DevOpsError

            raise DevOpsError("deploy failed")
        return {"workflow_run_id": 42}

    monkeypatch.setattr("bigas.tickets.releases._publish_github_release", _publish)
    monkeypatch.setattr("bigas.resources.devops.service.trigger_deployment", _deploy)
    create_release("VFA", name="0.9.0")

    with pytest.raises(ReleaseError, match="deploy failed"):
        ship_release("VFA", "0.9.0")

    assert len(publish_calls) == 1
    assert len(deploy_calls) == 1

    result = ship_release("VFA", "0.9.0")
    assert len(publish_calls) == 1
    assert len(deploy_calls) == 2
    assert result["deploy"]["workflow_run_id"] == 42
    assert result["github_release"]["tag_name"] == "v0.9.0"


def test_adapter_prefers_board_default_over_env(monkeypatch):
    monkeypatch.setenv("BIGAS_PROJECT_ACTIVE_FIX_VERSION", "VFA:0.8.0")
    create_release("VFA", name="0.9.0", is_default=True)
    store = get_ticket_store()
    board = store.create_board("dev-user", name="VFA Board", project_key="VFA")
    store.create_ticket(
        board["board_id"],
        title="New work",
        user_id="dev-user",
        key="VFA-210",
    )
    applied = TicketJiraAdapter().ensure_issue_fix_version("VFA-210", project_key="VFA")
    assert applied == "0.9.0"


def test_workflow_success_on_tag_closes_release(monkeypatch):
    monkeypatch.setattr("bigas.tickets.releases._publish_github_release", lambda *a, **k: None)
    monkeypatch.setattr("bigas.chat.activity.post_to_agent_thread", lambda *a, **k: None)
    create_release("VFA", name="0.9.0")
    payload = {
        "action": "completed",
        "workflow_run": {
            "conclusion": "success",
            "name": "Deploy backend",
            "path": ".github/workflows/deploy-backend.yml",
            "head_branch": "v0.9.0",
        },
        "repository": {"owner": {"login": "mckort"}, "name": "vcfieldassistant"},
    }
    closed = maybe_close_board_release_from_workflow(payload)
    assert closed is not None
    assert closed["release"]["released"] is True
    assert maybe_close_board_release_from_workflow({**payload, "action": "requested"}) is None
