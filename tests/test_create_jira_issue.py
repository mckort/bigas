"""Unit tests for create_jira_issue MCP tool (no network)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")
os.environ.setdefault("CHAT_DEV_TOKEN", "test-dev-token")

from bigas.resources.product.create_jira_issue.service import (
    ALLOWED_ISSUE_TYPES,
    CreateJiraIssueError,
    CreateJiraIssueService,
    _format_jira_error,
)
from bigas.resources.product.create_release_notes.jira_client import JiraError


def test_format_jira_error_project_not_found():
    err = JiraError('Jira API error 404: {"errorMessages":["Project not found"]}')
    assert "BIG" in _format_jira_error(err, project_key="BIG")
    assert "not found" in _format_jira_error(err, project_key="BIG").lower()


def test_create_jira_issue_validates_required_fields():
    service = CreateJiraIssueService()
    with pytest.raises(CreateJiraIssueError, match="project_key"):
        service.create(
            project_key="",
            summary="Title",
            description="Body",
        )
    with pytest.raises(CreateJiraIssueError, match="summary"):
        service.create(
            project_key="BIG",
            summary="  ",
            description="Body",
        )
    with pytest.raises(CreateJiraIssueError, match="description"):
        service.create(
            project_key="BIG",
            summary="Title",
            description="",
        )


def test_create_jira_issue_validates_issue_type():
    service = CreateJiraIssueService()
    with pytest.raises(CreateJiraIssueError, match="issue_type"):
        service.create(
            project_key="BIG",
            summary="Title",
            description="Body",
            issue_type="Epic",
        )


def test_create_jira_issue_success(monkeypatch):
    captured = {}

    def fake_create_issue(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "key": "BIG-99",
            "url": "https://example.atlassian.net/browse/BIG-99",
        }

    class FakeClient:
        def __init__(self, config):
            pass

        create_issue = staticmethod(lambda **kw: fake_create_issue(**kw))

    class FakeConfig:
        @staticmethod
        def from_env():
            return object()

    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraConfig",
        FakeConfig,
    )

    service = CreateJiraIssueService()
    result = service.create(
        project_key="big",
        summary="New task",
        description="Details here",
        issue_type="Bug",
        marketing=True,
        parent_epic_key="BIG-10",
    )

    assert result["ok"] is True
    assert result["key"] == "BIG-99"
    assert result["issue_type"] == "Bug"
    assert result["project_key"] == "BIG"
    assert result["labels"] == ["marketing"]
    assert result["parent_epic_key"] == "BIG-10"
    assert captured["parent_epic_key"] == "BIG-10"
    assert captured["labels"] == ["marketing"]


def test_create_jira_issue_no_marketing_label_by_default(monkeypatch):
    captured = {}

    def fake_create_issue(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "key": "VFA-1", "url": "https://x/browse/VFA-1"}

    class FakeClient:
        def __init__(self, config):
            pass

        create_issue = staticmethod(lambda **kw: fake_create_issue(**kw))

    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraConfig",
        type("C", (), {"from_env": staticmethod(lambda: object())})(),
    )

    result = CreateJiraIssueService().create(
        project_key="VFA",
        summary="Task",
        description="Desc",
    )
    assert result["ok"] is True
    assert "labels" not in result
    assert captured.get("labels") is None


def test_create_jira_issue_wraps_jira_error(monkeypatch):
    class FakeClient:
        def __init__(self, config):
            pass

        def create_issue(self, **kwargs):
            raise JiraError('Jira API error 404: project missing')

    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraConfig",
        type("C", (), {"from_env": staticmethod(lambda: object())})(),
    )

    with pytest.raises(CreateJiraIssueError, match="WAYW"):
        CreateJiraIssueService().create(
            project_key="WAYW",
            summary="Title",
            description="Body",
        )


def test_manifest_includes_create_jira_issue():
    from bigas.resources.product.endpoints import get_manifest

    tools = {t["name"]: t for t in get_manifest()["tools"]}
    assert "create_jira_issue" in tools
    tool = tools["create_jira_issue"]
    assert tool["path"] == "/mcp/tools/create_jira_issue"
    params = tool["parameters"]
    assert set(params["required"]) == {"project_key", "summary", "description"}
    assert params["properties"]["issue_type"]["default"] == "Task"
    assert set(params["properties"]["issue_type"]["enum"]) == ALLOWED_ISSUE_TYPES
    assert "marketing" in tool["description"].lower()
    assert "every chat agent" in tool["description"].lower()
    parent_desc = params["properties"]["parent_epic_key"]["description"].lower()
    assert "omit" in parent_desc
    assert "standalone" in parent_desc
    lookup = tools["lookup_jira"]
    assert lookup["path"] == "/mcp/tools/lookup_jira"
    assert "parent" in lookup["description"].lower()
    assert "standalone" in lookup["description"].lower()
    assert "range" in lookup["description"].lower()
    assert "issue_keys" in lookup["parameters"]["properties"]
    search = tools["search_jira"]
    assert search["path"] == "/mcp/tools/search_jira"
    assert search["parameters"]["required"] == ["jql"]
    assert "jql" in search["description"].lower()


@pytest.fixture
def client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_create_jira_issue_endpoint_validation(client, monkeypatch):
    def fake_create(**kwargs):
        return {"ok": True, "key": "BIG-1", "url": "https://x/browse/BIG-1"}

    monkeypatch.setattr(CreateJiraIssueService, "create", lambda self, **kw: fake_create(**kw))

    resp = client.post("/mcp/tools/create_jira_issue", json={})
    assert resp.status_code == 400

    resp = client.post(
        "/mcp/tools/create_jira_issue",
        json={
            "project_key": "BIG",
            "summary": "Title",
            "description": "Body",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["key"] == "BIG-1"


def test_create_jira_issue_requires_access_key_in_restricted_mode(client, monkeypatch):
    def fake_create(**kwargs):
        return {"ok": True, "key": "BIG-1", "url": "https://x/browse/BIG-1"}

    monkeypatch.setattr(CreateJiraIssueService, "create", lambda self, **kw: fake_create(**kw))

    client.application.config["BIGAS_ACCESS_MODE"] = "restricted"
    client.application.config["BIGAS_ACCESS_KEYS"] = {"scheduler-key"}
    client.application.config["BIGAS_ACCESS_HEADER"] = "X-Bigas-Access-Key"

    payload = {
        "project_key": "BIG",
        "summary": "Title",
        "description": "Body",
    }
    denied = client.post("/mcp/tools/create_jira_issue", json=payload)
    assert denied.status_code == 401

    allowed = client.post(
        "/mcp/tools/create_jira_issue",
        json=payload,
        headers={"X-Bigas-Access-Key": "scheduler-key"},
    )
    assert allowed.status_code == 200


def test_create_jira_issue_endpoint_rejects_invalid_issue_type(client):
    resp = client.post(
        "/mcp/tools/create_jira_issue",
        json={
            "project_key": "BIG",
            "summary": "Title",
            "description": "Body",
            "issue_type": 123,
        },
    )
    assert resp.status_code == 400
    assert "issue_type" in resp.get_json()["error"]


def test_create_jira_issue_endpoint_coerces_types(client, monkeypatch):
    captured = {}

    def fake_create(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "key": "BIG-2", "url": "https://x/browse/BIG-2"}

    monkeypatch.setattr(CreateJiraIssueService, "create", fake_create)

    resp = client.post(
        "/mcp/tools/create_jira_issue",
        json={
            "project_key": "BIG",
            "summary": "Title",
            "description": "Body",
            "issue_type": "task",
            "marketing": "false",
        },
    )
    assert resp.status_code == 200
    assert captured["issue_type"] == "Task"
    assert captured["marketing"] is False


def test_create_jira_issue_accepts_case_insensitive_issue_type(monkeypatch):
    captured = {}

    def fake_create_issue(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "key": "BIG-3", "url": "https://x/browse/BIG-3"}

    class FakeClient:
        def __init__(self, config):
            pass

        create_issue = staticmethod(lambda **kw: fake_create_issue(**kw))

    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraConfig",
        type("C", (), {"from_env": staticmethod(lambda: object())})(),
    )

    result = CreateJiraIssueService().create(
        project_key="BIG",
        summary="Title",
        description="Body",
        issue_type="bug",
    )
    assert result["issue_type"] == "Bug"
    assert captured["issue_type"] == "Bug"


def test_create_jira_issue_omits_invalid_parent_key(monkeypatch):
    captured = {}

    def fake_create_issue(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "key": "GPWW-10", "url": "https://x/browse/GPWW-10"}

    class FakeClient:
        def __init__(self, config):
            pass

        create_issue = staticmethod(lambda **kw: fake_create_issue(**kw))

    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraConfig",
        type("C", (), {"from_env": staticmethod(lambda: object())})(),
    )

    result = CreateJiraIssueService().create(
        project_key="GPWW",
        summary="Fix tracking",
        description="GA4 key events",
        parent_epic_key="GPWW",
    )
    assert result["ok"] is True
    assert "parent_epic_key" not in result
    assert captured["parent_epic_key"] is None


def test_create_jira_issue_passes_through_dropped_parent(monkeypatch):
    class FakeClient:
        def __init__(self, config):
            pass

        def create_issue(self, **kwargs):
            return {
                "ok": True,
                "key": "GPWW-11",
                "url": "https://x/browse/GPWW-11",
                "parent_dropped": True,
            }

    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "bigas.resources.product.create_jira_issue.service.JiraConfig",
        type("C", (), {"from_env": staticmethod(lambda: object())})(),
    )

    result = CreateJiraIssueService().create(
        project_key="GPWW",
        summary="Fix tracking",
        description="GA4 key events",
        parent_epic_key="GPWW-9",
    )
    assert result["key"] == "GPWW-11"
    assert "parent_epic_key" not in result
    assert result["parent_dropped"] is True
