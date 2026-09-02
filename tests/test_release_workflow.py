"""Tests for staging/main branch mapping and semver helpers (BIG-42)."""
from __future__ import annotations

import pytest

from bigas.resources.product.jira_automation.config import JiraAutomationConfig
from bigas.resources.product.release_workflow import (
    labels_include_hotfix,
    normalize_semver_tag,
    parse_project_branch_mapping,
    resolve_automerge_branch,
    resolve_production_branch,
)


def test_parse_project_branch_mapping():
    parsed = parse_project_branch_mapping("VFA:staging,DEFAULT:main")
    assert parsed == {"VFA": "staging", "DEFAULT": "main"}


def test_resolve_automerge_branch_staging_for_project(monkeypatch):
    monkeypatch.delenv("PROJECT_BRANCH_MAPPING", raising=False)
    branch = resolve_automerge_branch(
        project_key="VFA",
        repo="mckort/vcfieldassistant",
        project_branch_map={"VFA": "staging", "DEFAULT": "main"},
    )
    assert branch == "staging"


def test_resolve_automerge_branch_hotfix_label_to_main():
    branch = resolve_automerge_branch(
        project_key="VFA",
        repo="mckort/vcfieldassistant",
        labels=["hotfix"],
        project_branch_map={"VFA": "staging", "DEFAULT": "main"},
        repo_base_branches={"mckort/vcfieldassistant": "main"},
    )
    assert branch == "main"


def test_labels_include_hotfix():
    assert labels_include_hotfix(["Hotfix"]) is True
    assert labels_include_hotfix(["feature"]) is False


def test_normalize_semver_tag():
    assert normalize_semver_tag("0.9.0") == "v0.9.0"
    assert normalize_semver_tag("v1.0.0") == "v1.0.0"


def test_normalize_semver_tag_invalid():
    with pytest.raises(ValueError):
        normalize_semver_tag("not-a-version")


def test_resolve_production_branch_ignores_staging_map():
    branch = resolve_production_branch(
        project_key="VFA",
        repo="mckort/vcfieldassistant",
        repo_base_branches={"mckort/vcfieldassistant": "main"},
    )
    assert branch == "main"


def test_config_automerge_branch_for_project(monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "VFA:staging,DEFAULT:main")
    cfg = JiraAutomationConfig.from_env()
    assert cfg.automerge_branch_for_project("VFA", "mckort/vcfieldassistant") == "staging"
    assert cfg.automerge_branch_for_project("BIG", "mckort/bigas") == "main"
    assert (
        cfg.automerge_branch_for_project("VFA", "mckort/vcfieldassistant", labels=["hotfix"])
        == "main"
    )
