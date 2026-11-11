"""Setup tooling: connect_repo env planning and doctor checks."""
from __future__ import annotations

import base64
import os

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")

from bigas.setup_check import Project, github_checks, project_config_checks
from bigas.setup_repo import RepoSpec, caller_workflow, plan_env_changes, update_env_text


def test_plan_env_changes_for_a_new_repo():
    env = {
        "JIRA_PROJECT_KEY": "VFA,BIG",
        "BIGAS_JIRA_PROJECT_REPO_MAP": "VFA:mckort/vcfieldassistant",
        "BIGAS_DEPLOY_WORKFLOW_MAP": "VFA:deploy-backend.yml,deploy-web.yml|BIG:deploy.yml",
    }
    spec = RepoSpec(
        key="ACME",
        repo="acme/shop",
        branch="develop",
        name="Acme Shop",
        deploy_workflows=["deploy.yml"],
        release_prefix="staging",
    )
    changes = plan_env_changes(env, spec)
    assert changes["JIRA_PROJECT_KEY"] == "VFA,BIG,ACME"
    assert "JIRA_PROJECT_KEYS" not in changes
    assert changes["BIGAS_JIRA_PROJECT_REPO_MAP"] == "VFA:mckort/vcfieldassistant,ACME:acme/shop"
    assert changes["BIGAS_JIRA_REPO_BASE_BRANCH_MAP"] == "acme/shop:develop"
    assert changes["BIGAS_DEPLOY_WORKFLOW_MAP"] == "VFA:deploy-backend.yml,deploy-web.yml|BIG:deploy.yml|ACME:deploy.yml"
    assert changes["BIGAS_PROJECT_NAME_MAP"] == "ACME:Acme Shop"
    assert changes["PROJECT_BRANCH_MAPPING"] == "DEFAULT:main,ACME:staging"


def test_plan_env_changes_is_idempotent():
    spec = RepoSpec(key="ACME", repo="acme/shop", deploy_workflows=["deploy.yml"])
    env = {}
    for _ in range(2):
        env.update(plan_env_changes(env, spec))
    assert plan_env_changes(env, spec) == {}
    assert env["JIRA_PROJECT_KEY"] == "ACME"


def test_spec_validation():
    assert RepoSpec(key="acme shop", repo="nope").validate()
    assert RepoSpec(key="ACME", repo="a/b", deploy_workflows=["deploy"]).validate()
    assert RepoSpec(key="GPW-PROD", repo="a/b", deploy_workflows=["deploy.yml"]).validate() == []


def test_update_env_text_replaces_and_appends():
    text = "# comment\nJIRA_PROJECT_KEY=VFA\n# BIGAS_DEPLOY_WORKFLOW_MAP=old\nOTHER=1"
    out = update_env_text(text, {"JIRA_PROJECT_KEY": "VFA,ACME", "BIGAS_DEPLOY_WORKFLOW_MAP": "ACME:deploy.yml"})
    assert "JIRA_PROJECT_KEY=VFA,ACME\n" in out
    assert "# BIGAS_DEPLOY_WORKFLOW_MAP=old" in out  # comments untouched
    assert out.rstrip().endswith("BIGAS_DEPLOY_WORKFLOW_MAP=ACME:deploy.yml")
    assert "OTHER=1" in out


def test_caller_workflow_points_at_fork():
    template = open("docs/pr-review.caller.yml").read()
    out = caller_workflow(template, "acme/bigas@v2")
    assert "uses: acme/bigas/.github/workflows/pr-review.yml@v2" in out
    assert "mckort/bigas/.github/workflows" not in out


def _content(text):
    return {"content": base64.b64encode(text.encode()).decode()}


def test_github_checks_report_missing_wiring():
    project = Project(
        key="ACME",
        name="Acme",
        repo="acme/shop",
        base_branch="main",
        deploy_workflows=["deploy.yml"],
        release_branch="staging",
    )
    files = {
        "/repos/acme/shop/contents/.github/workflows/pr-review.yml?ref=main": _content(
            "jobs:\n  bigas:\n    uses: mckort/bigas/.github/workflows/pr-review.yml@main\n"
        ),
        "/repos/acme/shop/contents/.github/workflows/deploy.yml?ref=HEAD": _content("on:\n  push:\n"),
    }

    def fetch(path):
        if path == "/repos/acme/shop":
            return 200, {"permissions": {"push": True}}
        if path == "/repos/acme/shop/branches/main":
            return 200, {}
        if path in files:
            return 200, files[path]
        if path.endswith("/actions/variables/BIGAS_URL"):
            return 200, {"value": "https://old.example"}
        if path.endswith("/hooks"):
            return 200, []
        return 404, None

    checks = {(c.id, c.status) for c in github_checks([project], fetch, bigas_url="https://bigas.example")}
    assert ("gh_pr_review", "ok") in checks
    assert ("gh_var", "warn") in checks  # wrong URL
    assert ("gh_secret", "fail") in checks
    assert ("gh_webhook", "warn") in checks
    assert ("gh_deploy", "fail") in checks  # no workflow_dispatch
    assert ("gh_versioned", "fail") in checks


def test_project_config_checks_flag_unmapped_repo():
    checks = project_config_checks([Project(key="ACME", name="Acme", repo="", base_branch="")])
    assert checks[0].status == "fail"
    assert "connect_repo.py --key ACME" in checks[0].fix
