"""Setup tooling: connect_repo env planning and doctor checks."""
from __future__ import annotations

import base64
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")

from bigas.setup_check import Check, Project, github_checks, project_config_checks, run_checks
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


def _connect_repo():
    path = Path(__file__).resolve().parents[1] / "scripts" / "connect_repo.py"
    spec = importlib.util.spec_from_file_location("connect_repo_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gh_json_returns_none_instead_of_raising(monkeypatch):
    module = _connect_repo()

    def fake_run(*args, **kwargs):
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(1, args[0], stderr="missing")
        return subprocess.CompletedProcess(args[0], 1, stdout='{"message":"Not Found"}', stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.gh_json(["repos/acme/shop/git/ref/heads/missing"]) is None
    assert module.gh_json(["repos/acme/shop/hooks"], check=True) is None


def test_open_setup_pr_distinguishes_no_change_from_create_failure(monkeypatch):
    module = _connect_repo()
    spec = RepoSpec(key="ACME", repo="acme/shop", branch="main")
    content = "name: review\n"
    files = {".github/workflows/pr-review.yml": content}
    encoded = base64.b64encode(content.encode()).decode()

    def unchanged(args, payload=None, check=False):
        if args[0].endswith("/heads/main"):
            return {"object": {"sha": "abc"}}
        if "contents/" in args[0]:
            return {"content": encoded}
        return {}

    monkeypatch.setattr(module, "gh_json", unchanged)
    assert module.open_setup_pr(spec, files) == ""

    def pushed(args, payload=None, check=False):
        if args[0].endswith("/heads/main"):
            return {"object": {"sha": "abc"}}
        if args[0].endswith("/git/refs"):
            return {"ref": "refs/heads/bigas-setup"}
        if "PUT" in args:
            return {"content": {"sha": "def"}}
        if "/pulls" in args[0] and "POST" in args:
            return None
        if "/pulls" in args[0]:
            return []
        return None

    monkeypatch.setattr(module, "gh_json", pushed)
    assert module.open_setup_pr(spec, files) is None

    def missing_branch(args, payload=None, check=False):
        return None

    monkeypatch.setattr(module, "gh_json", missing_branch)
    try:
        module.open_setup_pr(spec, files)
    except SystemExit as exc:
        assert "branch main not found" in str(exc)
    else:
        raise AssertionError("missing base branch should exit")


def test_apply_reports_pr_and_webhook_failures(monkeypatch, tmp_path, capsys):
    module = _connect_repo()
    env = tmp_path / ".env"
    env.write_text(
        "BIGAS_PUBLIC_URL=https://bigas.example\n"
        "BIGAS_ACCESS_KEYS=secret-key\n"
        "GITHUB_WEBHOOK_SECRET=hook-secret\n"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "connect_repo.py",
            "--key",
            "ACME",
            "--repo",
            "acme/shop",
            "--apply",
            "--webhook",
            "--skip-env",
            "--env-file",
            str(env),
        ],
    )
    monkeypatch.setattr(module.shutil, "which", lambda _name: "gh")
    monkeypatch.setattr(
        module, "gh", lambda args, **kwargs: subprocess.CompletedProcess(["gh", *args], 0, "", "")
    )

    def fake_json(args, payload=None, check=False):
        path = args[0]
        if path.endswith("/heads/main"):
            return {"object": {"sha": "abc"}}
        if path.endswith("/git/refs"):
            return {"ref": "refs/heads/bigas-setup"}
        if "PUT" in args:
            return {"content": {"path": path}}
        if path.endswith("/hooks") and "POST" in args:
            return None
        if path.endswith("/hooks"):
            return {"message": "Must have admin rights to Repository"}
        if "/pulls" in path and "POST" in args:
            return None
        if "/pulls" in path:
            return []
        return None

    monkeypatch.setattr(module, "gh_json", fake_json)
    code = module.main()
    out = capsys.readouterr().out
    assert code == 1
    assert "pull request was not created" in out
    assert "Workflows already on the base branch" not in out
    assert "could not add the workflow_run webhook" in out
    assert "Added workflow_run webhook" not in out


def test_run_checks_counts_unrecognized_status_as_failure(monkeypatch):
    monkeypatch.setattr("bigas.setup_check.load_projects", lambda: [])
    monkeypatch.setattr(
        "bigas.setup_check.instance_checks",
        lambda: [Check("custom", "skipped", "A check was skipped")],
    )
    monkeypatch.setattr("bigas.setup_check.project_config_checks", lambda projects: [])
    report = run_checks(offline=True)
    assert report["ok"] is False
    assert report["counts"]["fail"] == 1
    assert report["counts"]["ok"] == 0
    assert report["checks"][0]["status"] == "fail"
    assert "unrecognized status" in report["checks"][0]["message"]


def test_apply_stops_when_gh_cannot_set_the_secret(monkeypatch, tmp_path, capsys):
    module = _connect_repo()
    env = tmp_path / ".env"
    env.write_text("BIGAS_PUBLIC_URL=https://bigas.example\nBIGAS_ACCESS_KEYS=secret-key\n")
    monkeypatch.setattr(
        sys,
        "argv",
        ["connect_repo.py", "--key", "ACME", "--repo", "acme/shop", "--apply", "--skip-env", "--env-file", str(env)],
    )
    monkeypatch.setattr(module.shutil, "which", lambda _name: "gh")
    calls = []

    def fake_gh(args, **kwargs):
        calls.append(args)
        failed = args[:2] == ["secret", "set"]
        return subprocess.CompletedProcess(
            ["gh", *args], 1 if failed else 0, "", "HTTP 403: Resource not accessible by integration" if failed else ""
        )

    monkeypatch.setattr(module, "gh", fake_gh)
    monkeypatch.setattr(module, "gh_json", lambda *a, **k: pytest.fail("no API calls after a failed secret"))
    assert module.main() == 1
    out = capsys.readouterr().out
    assert "could not set secret BIGAS_API_KEY: HTTP 403" in out
    assert "secret-key" not in out
    assert [c[:2] for c in calls] == [["variable", "set"], ["secret", "set"]]


def test_github_fetcher_reports_network_errors(monkeypatch):
    import requests

    from bigas.setup_check import github_fetcher

    def boom(self, url, timeout=None):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests.Session, "get", boom)
    fetch = github_fetcher("token")
    status, body = fetch("/repos/acme/shop")
    assert status == 0
    assert "ConnectionError" in body["error"]

    project = Project(key="ACME", name="Acme", repo="acme/shop", base_branch="main")
    checks = github_checks([project], fetch)
    assert [(c.id, c.status) for c in checks] == [("gh_repo", "fail")]
    assert "could not reach GitHub" in checks[0].message


def _setup_pr_fake(open_prs, patch_result):
    calls = []

    def fake_json(args, payload=None, check=False):
        calls.append((args, payload))
        path = args[0]
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "base-sha"}}
        if path.endswith("/git/refs") and "POST" in args:
            return None  # bigas-setup already exists
        if "/pulls?head=" in path:
            return open_prs
        if path.endswith("/git/refs/heads/bigas-setup") and "PATCH" in args:
            return patch_result
        if "/contents/" in path and "PUT" in args:
            return {"content": {}}
        if "/pulls" in path and "POST" in args:
            return {"html_url": "https://github.com/acme/shop/pull/9"}
        return None

    return calls, fake_json


def test_existing_setup_branch_is_reset_when_no_pr_is_open(monkeypatch):
    module = _connect_repo()
    calls, fake_json = _setup_pr_fake(open_prs=[], patch_result={"object": {"sha": "base-sha"}})
    monkeypatch.setattr(module, "gh_json", fake_json)
    spec = RepoSpec(key="ACME", repo="acme/shop")
    url = module.open_setup_pr(spec, {".github/workflows/pr-review.yml": "x"})
    assert url == "https://github.com/acme/shop/pull/9"
    patch = [p for a, p in calls if "PATCH" in a]
    assert patch == [{"sha": "base-sha", "force": True}]


def test_existing_setup_branch_with_open_pr_is_kept(monkeypatch):
    module = _connect_repo()
    calls, fake_json = _setup_pr_fake(
        open_prs=[{"html_url": "https://github.com/acme/shop/pull/3"}], patch_result=None
    )
    monkeypatch.setattr(module, "gh_json", fake_json)
    spec = RepoSpec(key="ACME", repo="acme/shop")
    module.open_setup_pr(spec, {".github/workflows/pr-review.yml": "x"})
    assert not [a for a, _ in calls if "PATCH" in a]


def test_setup_branch_that_cannot_move_stops(monkeypatch):
    module = _connect_repo()
    _calls, fake_json = _setup_pr_fake(open_prs=[], patch_result=None)
    monkeypatch.setattr(module, "gh_json", fake_json)
    with pytest.raises(SystemExit, match="could not be moved"):
        module.open_setup_pr(RepoSpec(key="ACME", repo="acme/shop"), {".github/workflows/pr-review.yml": "x"})


def test_open_setup_pr_retargets_an_open_pr_on_another_base(monkeypatch):
    module = _connect_repo()
    calls = []

    def fake_json(args, payload=None, check=False):
        calls.append((args, payload))
        path = args[0]
        if path.endswith("/git/ref/heads/develop"):
            return {"object": {"sha": "dev-sha"}}
        if path.endswith("/git/refs") and "POST" in args:
            return None
        if "/pulls?head=" in path:
            return [{"number": 7, "base": {"ref": "main"}, "html_url": "https://github.com/acme/shop/pull/7"}]
        if path.endswith("/pulls/7") and "PATCH" in args:
            return {"number": 7}
        if "/contents/" in path and "PUT" in args:
            return {"content": {}}
        return None

    monkeypatch.setattr(module, "gh_json", fake_json)
    url = module.open_setup_pr(RepoSpec(key="ACME", repo="acme/shop", branch="develop"), {".github/workflows/pr-review.yml": "x"})
    assert url == "https://github.com/acme/shop/pull/7"
    assert (["repos/acme/shop/pulls/7", "-X", "PATCH"], {"base": "develop"}) in calls
    assert not [a for a, _ in calls if a[0].endswith("/git/refs/heads/bigas-setup")]


def test_unreadable_workflow_file_warns_instead_of_passing():
    project = Project(key="ACME", name="Acme", repo="acme/shop", base_branch="main", deploy_workflows=["deploy.yml"])

    def fetch(path):
        if path in ("/repos/acme/shop", "/repos/acme/shop/branches/main"):
            return 200, {"permissions": {"push": True}}
        if "/contents/" in path:
            return 200, {"content": "%%% not base64 %%%"}
        return 404, None

    checks = {(c.id, c.status) for c in github_checks([project], fetch)}
    assert ("gh_pr_review", "warn") in checks
    assert ("gh_deploy", "warn") in checks


def test_release_load_error_is_not_shown_as_no_version():
    from bigas.cockpit import build_cockpit

    payload = build_cockpit(
        boards=[{"board_id": "b1", "project_key": "VFA"}],
        tickets_by_board={"b1": []},
        threads=[],
        projects=[{"key": "VFA", "name": "VC Field Assistant"}],
        releases_by_project={"VFA": None},
    )
    row = payload["ship"][0]
    assert row["release_error"] is True
    assert row["version"] == ""
