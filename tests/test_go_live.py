"""Go to production: move a project from development to versioned releases."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas import go_live, project_settings
from bigas.tickets import store as ticket_store_module
from bigas.tickets.release_store import reset_release_store_for_tests


class FakeGitHub:
    def __init__(self, files=None, branches=("main",), open_pr=None):
        self.files = dict(files or {})
        self.branches = set(branches)
        self.open_pr = open_pr
        self.calls = []

    def branch_exists(self, owner, repo, branch):
        return branch in self.branches

    def get_file_text(self, owner, repo, path, ref):
        return self.files.get(path)

    def find_open_pull_request(self, owner, repo, *, head, base):
        return self.open_pr

    def list_repo_rulesets(self, owner, repo):
        raise RuntimeError(
            "GitHub auth failed (403): Upgrade to GitHub Pro or make this repository public to enable this feature."
        )

    def get_ref_sha(self, owner, repo, ref):
        return f"sha-{ref}"

    def create_branch_ref(self, owner, repo, branch, sha):
        self.calls.append(("create_branch", branch, sha))
        self.branches.add(branch)

    def update_branch_ref(self, owner, repo, branch, sha, *, force=False):
        self.calls.append(("update_ref", branch, sha, force))

    def get_commit(self, owner, repo, ref):
        return {"commit": {"tree": {"sha": "tree-base"}}}

    def create_blob(self, owner, repo, content):
        self.calls.append(("blob", content[:40]))
        return f"blob-{len(self.calls)}"

    def create_tree(self, owner, repo, base, entries):
        self.calls.append(("tree", [e["path"] for e in entries]))
        return "tree-new"

    def create_git_commit(self, owner, repo, *, message, tree_sha, parent_sha):
        self.calls.append(("commit", message))
        return "commit-new"

    def create_pull_request(self, owner, repo, *, title, body, head, base):
        self.calls.append(("pr", head, base))
        return {"html_url": "https://github.com/acme/shop/pull/1"}

    def ensure_branch_from_ref(self, owner, repo, branch, source):
        self.calls.append(("release_branch", branch, source))
        self.branches.add(branch)
        return source

    def compare_refs(self, owner, repo, base, head):
        return {"ahead_by": 0}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("PROJECT_BRANCH_MAPPING", "BIGAS_PROJECT_BRANCH_MAPPING", "BIGAS_STAGING_ENV_MAP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JIRA_PROJECT_KEYS", "ACME,VFA")
    monkeypatch.setenv("BIGAS_JIRA_PROJECT_REPO_MAP", "ACME:acme/shop")
    monkeypatch.setenv("BIGAS_JIRA_REPO_BASE_BRANCH_MAP", "acme/shop:main")
    project_settings.reset_for_tests()
    reset_release_store_for_tests()
    ticket_store_module._store = None
    yield
    project_settings.reset_for_tests()
    reset_release_store_for_tests()
    ticket_store_module._store = None


def test_caller_template_matches_docs():
    from bigas.setup_repo import PR_REVIEW_CALLER_TEMPLATE

    with open("docs/pr-review.caller.yml", encoding="utf-8") as fh:
        assert PR_REVIEW_CALLER_TEMPLATE == fh.read()


def test_release_state_sources(monkeypatch):
    assert go_live.release_state("ACME")["mode"] == "development"
    assert go_live.release_state("GPW-PROD")["source"] == "staging"
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "VFA:staging,DEFAULT:main")
    vfa = go_live.release_state("VFA")
    assert (vfa["mode"], vfa["source"], vfa["release_prefix"]) == ("production", "env", "staging")
    project_settings.set_release_mode("ACME", "production")
    assert go_live.release_state("ACME")["source"] == "app"


def test_plan_lists_every_step_for_a_development_project():
    gh = FakeGitHub()
    out = go_live.plan("ACME", client=gh)
    assert out["can_apply"] and out["version"] == "0.1.0" and out["release_branch"] == "staging-0.1.0"
    status = {s["id"]: s["status"] for s in out["steps"]}
    assert status == {"workflows": "todo", "release": "todo", "branch": "todo", "mode": "todo"}
    assert "Prepare deploy" in out["steps"][-1]["detail"]
    assert "staging-0.1.0" in out["steps"][-1]["detail"]
    assert "pr-review.yml, cherry_pick.yml, rebase_release.yml" in out["steps"][0]["detail"]


def test_plan_rejects_a_version_that_is_not_semver():
    out = go_live.plan("ACME", version="abc", client=FakeGitHub())
    assert out["can_apply"] is False
    assert out["steps"] == []
    assert "is not a version like" in out["blocked"]


def test_plan_hides_a_missing_github_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    out = go_live.plan("ACME")
    assert out["can_apply"] is False
    assert "GITHUB_TOKEN" not in out["blocked"]
    assert "GitHub is not connected" in out["blocked"]


def test_firestore_settings_store_discovers_a_blank_project(monkeypatch):
    seen = {}

    class _Client:
        def __init__(self, project=None):
            seen["project"] = project

        def collection(self, name):
            seen["collection"] = name
            return object()

    monkeypatch.setattr("google.cloud.firestore.Client", _Client)
    store = project_settings.FirestoreProjectSettingsStore("")
    assert seen == {"project": None, "collection": "bigas_project_settings"}
    assert store._col is not None
    project_settings.FirestoreProjectSettingsStore("my-proj")
    assert seen["project"] == "my-proj"


def test_plan_is_blocked_when_already_in_production(monkeypatch):
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "ACME:staging")
    out = go_live.plan("ACME", client=FakeGitHub())
    assert not out["can_apply"]
    assert "PROJECT_BRANCH_MAPPING" in out["blocked"]


def test_apply_wires_the_repo_and_switches_the_project():
    from bigas.resources.product.release_workflow import (
        project_branch_mapping_from_env,
        resolve_automerge_branch,
    )
    from bigas.tickets.releases import list_releases

    gh = FakeGitHub()
    out = go_live.apply("ACME", by="marcus@example.com", client=gh)
    assert [r["status"] for r in out["results"]] == ["ok"] * 4
    assert out["in_production"] is True

    kinds = [c[0] for c in gh.calls]
    assert ("create_branch", "bigas-production", "sha-main") in gh.calls
    assert ("tree", [".github/workflows/pr-review.yml", ".github/workflows/cherry_pick.yml", ".github/workflows/rebase_release.yml"]) in gh.calls
    assert ("pr", "bigas-production", "main") in gh.calls
    assert ("release_branch", "staging-0.1.0", "main") in gh.calls
    assert "protect" not in kinds

    releases = list_releases("ACME")
    assert [(r["name"], r["is_default"]) for r in releases] == [("0.1.0", True)]
    assert project_branch_mapping_from_env()["ACME"] == "staging"
    assert (
        resolve_automerge_branch(
            project_key="ACME", repo="acme/shop", repo_base_branches={"acme/shop": "main"}, fix_version="0.1.0"
        )
        == "staging-0.1.0"
    )


def test_apply_skips_done_steps_and_keeps_an_open_version():
    from bigas.tickets.releases import create_release

    create_release("ACME", name="0.4.0", is_default=True)
    gh = FakeGitHub(
        files={p: c for p, c in go_live.wanted_files().items()},
        branches=("main", "staging-0.4.0"),
    )
    out = go_live.apply("ACME", version="0.1.0", client=gh)
    assert out["version"] == "0.4.0"
    by_id = {r["id"]: r["status"] for r in out["results"]}
    assert by_id == {"workflows": "skipped", "release": "skipped", "branch": "skipped", "mode": "ok"}
    assert not [c for c in gh.calls if c[0] in ("pr", "release_branch")]


def test_apply_does_not_switch_when_the_branch_fails():
    class Broken(FakeGitHub):
        def ensure_branch_from_ref(self, *a, **k):
            raise RuntimeError("403 Resource not accessible")

    out = go_live.apply("ACME", client=Broken())
    by_id = {r["id"]: r for r in out["results"]}
    assert by_id["branch"]["status"] == "error" and "403" in by_id["branch"]["detail"]
    assert by_id["mode"]["status"] == "skipped"
    assert out["in_production"] is False


def test_back_to_development_only_for_app_setting(monkeypatch):
    project_settings.set_release_mode("ACME", "production")
    assert go_live.back_to_development("ACME")["mode"] == "development"
    monkeypatch.setenv("PROJECT_BRANCH_MAPPING", "ACME:staging")
    with pytest.raises(go_live.GoLiveError):
        go_live.back_to_development("ACME")


def test_production_endpoints(monkeypatch):
    from app import create_app

    gh = FakeGitHub()
    monkeypatch.setattr(go_live, "_client", lambda: gh)
    app = create_app()
    app.config["TESTING"] = True
    auth = {"Authorization": "Bearer test-dev-token", "Content-Type": "application/json"}
    with app.test_client() as client:
        client.get("/api/boards", headers=auth)  # creates the ACME board
        assert client.get("/api/projects/ACME/production").status_code == 401
        assert client.get("/api/projects/NOPE/production", headers=auth).status_code == 404
        plan = client.get("/api/projects/ACME/production", headers=auth).get_json()
        assert plan["can_apply"] is True
        done = client.post("/api/projects/ACME/production", headers=auth, data=json.dumps({"version": "0.1.0"}))
        assert done.status_code == 200 and done.get_json()["in_production"] is True
        again = client.post("/api/projects/ACME/production", headers=auth, data=json.dumps({}))
        assert again.status_code == 400
        back = client.delete("/api/projects/ACME/production", headers=auth)
        assert back.get_json()["mode"] == "development"
        cockpit = client.get("/api/cockpit", headers=auth).get_json()
        assert all(row.get("release_mode") for row in cockpit["ship"])
