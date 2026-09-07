"""Versioned staging branches and post-ship rebase."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bigas.resources.devops.github_actions import GitHubActionsError, GitHubMergeConflict
from bigas.tickets import store as ticket_store_module
from bigas.tickets.release_store import reset_release_store_for_tests
from bigas.resources.product.release_branches import (
    ensure_versioned_release_branch,
    newer_release_branch_names,
    rebase_newer_release_branches,
    should_inherit_legacy_staging,
)
from bigas.tickets.releases import create_release, unreleased_versions_after


@pytest.fixture(autouse=True)
def _reset_stores():
    ticket_store_module._store = None
    reset_release_store_for_tests()
    yield
    ticket_store_module._store = None
    reset_release_store_for_tests()


def test_should_inherit_legacy_staging_only_oldest(monkeypatch):
    create_release("VFA", name="0.2.3")
    create_release("VFA", name="0.3.0")
    assert should_inherit_legacy_staging("VFA", "0.2.3") is True
    assert should_inherit_legacy_staging("VFA", "0.3.0") is False


def test_unreleased_versions_after_orders_newer(monkeypatch):
    create_release("VFA", name="0.2.3")
    create_release("VFA", name="0.3.0")
    create_release("VFA", name="0.4.0")
    from bigas.tickets.release_store import get_release_store

    item = get_release_store().get_release_by_name("VFA", "0.2.3")
    get_release_store().update_release(item["release_id"], released=True)
    assert unreleased_versions_after("VFA", "0.2.3") == ["0.3.0", "0.4.0"]


def test_ensure_creates_from_main_when_no_legacy_staging():
    client = MagicMock()
    client.branch_exists.return_value = False
    client.list_versioned_feature_branches.return_value = []
    client.ensure_branch_from_ref.return_value = "main"

    result = ensure_versioned_release_branch(
        repo="mckort/vcfieldassistant",
        branch="staging-0.3.0",
        production="main",
        prefix="staging",
        client=client,
    )
    assert result["created"] is True
    assert result["source"] == "main"
    client.ensure_branch_from_ref.assert_called_once_with(
        "mckort", "vcfieldassistant", "staging-0.3.0", "main"
    )


def test_ensure_migrates_from_unversioned_staging_for_oldest_cut():
    client = MagicMock()
    client.branch_exists.side_effect = lambda owner, repo, branch: branch == "staging"
    client.compare_refs.return_value = {"ahead_by": 4, "commits": [1, 2, 3, 4]}
    client.ensure_branch_from_ref.return_value = "staging"

    result = ensure_versioned_release_branch(
        repo="mckort/vcfieldassistant",
        branch="staging-0.2.3",
        production="main",
        prefix="staging",
        client=client,
        inherit_legacy_prefix=True,
    )
    assert result["source"] == "staging"
    client.ensure_branch_from_ref.assert_called_once_with(
        "mckort", "vcfieldassistant", "staging-0.2.3", "staging"
    )


def test_ensure_newer_version_always_from_main():
    client = MagicMock()
    client.branch_exists.side_effect = lambda owner, repo, branch: branch == "staging"
    client.ensure_branch_from_ref.return_value = "main"

    result = ensure_versioned_release_branch(
        repo="mckort/vcfieldassistant",
        branch="staging-0.3.0",
        production="main",
        prefix="staging",
        client=client,
        inherit_legacy_prefix=False,
    )
    assert result["source"] == "main"
    client.compare_refs.assert_not_called()
    client.ensure_branch_from_ref.assert_called_once_with(
        "mckort", "vcfieldassistant", "staging-0.3.0", "main"
    )


def test_newer_release_branch_names_from_board_and_github(monkeypatch):
    create_release("VFA", name="0.2.3")
    create_release("VFA", name="0.3.0")
    from bigas.tickets.release_store import get_release_store

    item = get_release_store().get_release_by_name("VFA", "0.2.3")
    get_release_store().update_release(item["release_id"], released=True)

    client = MagicMock()
    client.list_versioned_feature_branches.return_value = [
        "staging-0.3.0",
        "staging-0.4.0",
    ]
    client.branch_exists.side_effect = lambda owner, repo, branch: branch in {
        "staging-0.3.0",
        "staging-0.4.0",
    }

    names = newer_release_branch_names(
        project_key="VFA",
        repo="mckort/vcfieldassistant",
        shipped_version="0.2.3",
        prefix="staging",
        production="main",
        client=client,
    )
    assert names == ["staging-0.3.0", "staging-0.4.0"]


def test_rebase_dispatches_workflow(monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "VFA:staging,DEFAULT:main")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    create_release("VFA", name="0.3.0")

    client = MagicMock()
    client.list_versioned_feature_branches.return_value = ["staging-0.3.0"]
    client.branch_exists.return_value = True

    monkeypatch.setattr(
        "bigas.resources.product.release_branches._github_client",
        lambda: client,
    )

    result = rebase_newer_release_branches(
        project_key="VFA",
        shipped_version="0.2.3",
        repo="mckort/vcfieldassistant",
    )
    assert result["ok"] is True
    assert result["branches"] == ["staging-0.3.0"]
    client.trigger_workflow.assert_called_once()
    args, kwargs = client.trigger_workflow.call_args
    assert args[2] == "rebase_release.yml"
    assert kwargs["inputs"]["release_branch"] == "staging-0.3.0"


def test_rebase_falls_back_to_merge_then_conflict_agent(monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "VFA:staging,DEFAULT:main")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-key")
    create_release("VFA", name="0.3.0")

    client = MagicMock()
    client.list_versioned_feature_branches.return_value = ["staging-0.3.0"]
    client.branch_exists.return_value = True
    client.trigger_workflow.side_effect = GitHubActionsError("workflow not found")
    client.merge_branches.side_effect = GitHubMergeConflict("conflict")

    monkeypatch.setattr(
        "bigas.resources.product.release_branches._github_client",
        lambda: client,
    )
    launched = {"agent_id": "bc-1", "agent_url": "https://cursor.com/agents/bc-1"}
    monkeypatch.setattr(
        "bigas.resources.product.release_branches._launch_conflict_agent",
        lambda **kwargs: {"ok": True, **launched},
    )

    result = rebase_newer_release_branches(
        project_key="VFA",
        shipped_version="0.2.3",
        repo="mckort/vcfieldassistant",
    )
    assert result["ok"] is True
    assert result["results"][0]["conflict"] is True
    assert result["results"][0]["agent_id"] == "bc-1"
