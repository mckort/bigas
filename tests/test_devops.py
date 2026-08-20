"""Tests for DevOps deployment tools (BIG-7)."""
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
os.environ.setdefault("GITHUB_TOKEN", "test-github-token")
os.environ.setdefault(
    "BIGAS_JIRA_PROJECT_REPO_MAP",
    "VFA:mckort/vcfieldassistant",
)
os.environ.setdefault(
    "BIGAS_DEPLOY_WORKFLOW_MAP",
    "VFA:deploy-backend.yml,deploy-web.yml",
)

from app import create_app
from bigas.resources.devops.config import resolve_deploy_target
from bigas.resources.devops.service import (
    _classify_file,
    check_deployment_risk,
    check_website_health,
    get_deployment_status,
    trigger_deployment,
)


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class _FakeGitHubClient:
    def __init__(self, *args, **kwargs):
        self._latest_run_ids: dict[str, int] = {}
        self._pending_new_runs: set[str] = set()
        self.compared: list[tuple[str, str]] = []
        self.dispatches: list[tuple] = []

    def get_default_branch(self, owner, repo):
        return "main"

    def get_latest_release_tag(self, owner, repo):
        return None

    def list_releases(self, owner, repo, *, limit=50):
        return [
            {
                "tag_name": "deploy-backend-20260819-120000-abc1234",
                "created_at": "2026-08-19T12:00:00Z",
                "draft": False,
            },
            {
                "tag_name": "deploy-web-20260819-120100-def5678",
                "created_at": "2026-08-19T12:01:00Z",
                "draft": False,
            },
        ]

    def latest_release_with_prefix(self, owner, repo, prefix):
        for rel in self.list_releases(owner, repo):
            tag = rel.get("tag_name") or ""
            if tag.startswith(prefix):
                return rel
        return None

    def compare_refs(self, owner, repo, base, head):
        self.compared.append((base, head))
        return {
            "files": [
                {"filename": "db/migrations/001_add_users.sql"},
                {"filename": "README.md"},
                {"filename": "requirements.txt"},
            ]
        }

    def trigger_workflow(self, owner, repo, workflow_id, ref, inputs=None):
        self.dispatches.append((owner, repo, workflow_id, ref, inputs))
        self._pending_new_runs.add(workflow_id)

    def list_workflow_runs(self, owner, repo, workflow_id, *, branch=None, limit=5):
        if workflow_id in self._pending_new_runs:
            run_id = self._latest_run_ids.get(workflow_id, 999) + 1
            self._latest_run_ids[workflow_id] = run_id
            self._pending_new_runs.discard(workflow_id)
            return [
                {
                    "id": run_id,
                    "status": "queued",
                    "conclusion": None,
                    "html_url": f"https://github.com/{owner}/{repo}/actions/runs/{run_id}",
                }
            ]
        run_id = self._latest_run_ids.get(workflow_id, 999)
        return [
            {
                "id": run_id,
                "status": "completed",
                "conclusion": "success",
                "html_url": f"https://github.com/{owner}/{repo}/actions/runs/{run_id}",
            }
        ]

    def get_workflow_run(self, owner, repo, run_id):
        return {
            "id": run_id,
            "status": "completed",
            "conclusion": "success",
            "name": "deploy-backend",
            "html_url": f"https://github.com/{owner}/{repo}/actions/runs/{run_id}",
        }

    def list_workflow_jobs(self, owner, repo, run_id):
        return [
            {
                "id": 42,
                "name": "deploy",
                "conclusion": "failure",
            }
        ]

    def get_job_logs(self, owner, repo, job_id):
        return "error during build:\n    Tsconfig not found expo/tsconfig.base\n"


def test_resolve_deploy_target_from_site():
    target = resolve_deploy_target(site_or_text="deploy vcfieldassistant.com")
    assert target is not None
    assert target.project_key == "VFA"
    assert "deploy-backend.yml" in target.workflows
    assert any("vcfieldassistant.com" in u for u in target.site_urls)


def test_check_deployment_risk_flags_migrations(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: _FakeGitHubClient(),
    )
    result = check_deployment_risk(project_key="VFA")
    assert result["risk_level"] in ("high", "medium")
    assert result["findings"]["database_migration"]
    assert "migration" in result["summary"].lower()
    assert "deploy-backend-" in result["summary"]
    assert "main → main" not in result["summary"]


def test_trigger_deployment(monkeypatch):
    monkeypatch.setattr("bigas.resources.devops.service.time.sleep", lambda _: None)
    fake = _FakeGitHubClient()
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: fake,
    )
    result = trigger_deployment(project_key="VFA")
    assert result["status"] == "ok"
    assert result["repo"] == "mckort/vcfieldassistant"
    assert result["deploy_repo"] == "mckort/vcfieldassistant"
    assert len(result["triggered"]) == 2
    assert "deploy-backend.yml" in result["summary"]
    assert all(item["run_id"] == 1000 for item in result["triggered"])
    assert all(dispatch[1] == "vcfieldassistant" for dispatch in fake.dispatches)
    assert all(dispatch[4] is None for dispatch in fake.dispatches)


def test_resolve_deploy_target_vm_site_uses_infra_repo(monkeypatch):
    monkeypatch.setenv(
        "BIGAS_JIRA_PROJECT_REPO_MAP",
        "VFA:mckort/vcfieldassistant,GPWW:Green-Promo-Wear-Global/greenpromowear-website",
    )
    monkeypatch.setenv(
        "BIGAS_DEPLOY_WORKFLOW_MAP",
        "VFA:deploy-backend.yml,deploy-web.yml|GPWW:deploy.yml",
    )
    monkeypatch.setenv("BIGAS_DEPLOY_REPO_MAP", "GPWW:mckort/gcp-single-vm-webstack")
    target = resolve_deploy_target(project_key="GPWW")
    assert target is not None
    assert target.repo == "Green-Promo-Wear-Global/greenpromowear-website"
    assert target.dispatch_repo == "mckort/gcp-single-vm-webstack"
    assert target.workflows == ["deploy.yml"]
    assert target.workflow_inputs == {"site": "greenpromowear-website"}


def test_trigger_deployment_vm_site_dispatches_infra_repo(monkeypatch):
    monkeypatch.setenv(
        "BIGAS_JIRA_PROJECT_REPO_MAP",
        "VFA:mckort/vcfieldassistant,GPWW:Green-Promo-Wear-Global/greenpromowear-website",
    )
    monkeypatch.setenv(
        "BIGAS_DEPLOY_WORKFLOW_MAP",
        "VFA:deploy-backend.yml,deploy-web.yml|GPWW:deploy.yml",
    )
    monkeypatch.setenv("BIGAS_DEPLOY_REPO_MAP", "GPWW:mckort/gcp-single-vm-webstack")
    monkeypatch.setattr("bigas.resources.devops.service.time.sleep", lambda _: None)
    fake = _FakeGitHubClient()
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: fake,
    )
    result = trigger_deployment(project_key="GPWW")
    assert result["status"] == "ok"
    assert result["repo"] == "Green-Promo-Wear-Global/greenpromowear-website"
    assert result["deploy_repo"] == "mckort/gcp-single-vm-webstack"
    assert result["workflow_inputs"] == {"site": "greenpromowear-website"}
    assert len(fake.dispatches) == 1
    owner, repo, workflow, _ref, inputs = fake.dispatches[0]
    assert owner == "mckort"
    assert repo == "gcp-single-vm-webstack"
    assert workflow == "deploy.yml"
    assert inputs == {"site": "greenpromowear-website"}
    assert "greenpromowear-website" in result["summary"]


def test_get_deployment_status(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: _FakeGitHubClient(),
    )
    result = get_deployment_status(repo="mckort/vcfieldassistant", run_id=999)
    assert result["workflow_status"] == "completed"
    assert result["conclusion"] == "success"


def test_get_failed_run_excerpt(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: _FakeGitHubClient(),
    )
    from bigas.resources.devops.service import get_failed_run_excerpt

    result = get_failed_run_excerpt(repo="mckort/vcfieldassistant", run_id=12)
    assert "expo/tsconfig.base" in result["excerpt"]
    assert result["failed_job_names"] == ["deploy"]


def test_check_website_health(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service._check_http_status",
        lambda url: (200, None, False),
    )
    result = check_website_health("https://example.com")
    assert result["is_healthy"] is True
    assert result["http_status"] == 200


def test_check_website_health_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.endpoints.check_website_health",
        lambda url: {
            "status": "ok",
            "summary": "ok",
            "url": url,
            "http_status": 200,
            "is_healthy": True,
        },
    )
    resp = client.post(
        "/mcp/tools/check_website_health",
        data=json.dumps({"url": "https://vcfieldassistant.com"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["is_healthy"] is True


def test_manifest_includes_devops_tools(client):
    resp = client.get("/mcp/manifest")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.get_json()["tools"]}
    assert "check_deployment_risk" in names
    assert "trigger_deployment" in names
    assert "get_deployment_status" in names
    assert "check_website_health" in names
    assert "fix_failed_deployment" in names


def test_list_agents_includes_devops(client):
    resp = client.get(
        "/api/agents",
        headers={"Authorization": "Bearer test-dev-token"},
    )
    assert resp.status_code == 200
    ids = {a["agent_id"] for a in resp.get_json()["agents"]}
    assert "devops" in ids


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("db/schema.sql", "database_migration"),
        ("package-lock.json", "dependency_change"),
        ("pnpm-lock.yaml", "dependency_change"),
    ],
)
def test_classify_file_matches_risky_patterns(path, category):
    assert _classify_file(path) == category


def test_get_deployment_status_invalid_run_id(client):
    resp = client.post(
        "/mcp/tools/get_deployment_status",
        data=json.dumps({"repo": "mckort/vcfieldassistant", "run_id": "not-a-number"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "integer" in resp.get_json()["error"].lower()


class _NoProdGitHubClient(_FakeGitHubClient):
    def list_releases(self, owner, repo, *, limit=50):
        return []

    def latest_release_with_prefix(self, owner, repo, prefix):
        return None

    def get_latest_release_tag(self, owner, repo):
        return None


def test_check_deployment_risk_without_prod_version_does_not_compare_main_to_main(monkeypatch):
    fake = _NoProdGitHubClient()
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: fake,
    )
    result = check_deployment_risk(project_key="VFA")
    assert result["no_prod_version"] is True
    assert result["total_files_changed"] == 0
    assert "main → main" not in result["summary"]
    assert "no production deploy version" in result["summary"].lower()
    assert fake.compared == []


def test_latest_release_with_prefix_paginates(monkeypatch):
    from bigas.resources.devops.github_actions import GitHubActionsClient

    pages = [
        [{"tag_name": f"other-{i}", "draft": False} for i in range(100)],
        [{"tag_name": "deploy-web-20260819-120100-def5678", "draft": False}],
    ]
    calls = {"n": 0}

    class _Resp:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            idx = calls["n"]
            calls["n"] += 1
            return pages[idx]

    def _fake_get(url, headers=None, params=None, timeout=None):
        assert params.get("page") in (1, 2)
        return _Resp()

    monkeypatch.setattr("bigas.resources.devops.github_actions.requests.get", _fake_get)
    client = GitHubActionsClient("test-token")
    rel = client.latest_release_with_prefix("mckort", "vcfieldassistant", "deploy-web-")
    assert rel is not None
    assert rel["tag_name"].startswith("deploy-web-")
    assert calls["n"] == 2
