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

    def get_default_branch(self, owner, repo):
        return "main"

    def get_latest_release_tag(self, owner, repo):
        return "v1.0.0"

    def compare_refs(self, owner, repo, base, head):
        return {
            "files": [
                {"filename": "db/migrations/001_add_users.sql"},
                {"filename": "README.md"},
                {"filename": "requirements.txt"},
            ]
        }

    def trigger_workflow(self, owner, repo, workflow_id, ref, inputs=None):
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


def test_trigger_deployment(monkeypatch):
    monkeypatch.setattr("bigas.resources.devops.service.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: _FakeGitHubClient(),
    )
    result = trigger_deployment(project_key="VFA")
    assert result["status"] == "ok"
    assert len(result["triggered"]) == 2
    assert "deploy-backend.yml" in result["summary"]
    assert all(item["run_id"] == 1000 for item in result["triggered"])


def test_get_deployment_status(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: _FakeGitHubClient(),
    )
    result = get_deployment_status(repo="mckort/vcfieldassistant", run_id=999)
    assert result["workflow_status"] == "completed"
    assert result["conclusion"] == "success"


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
