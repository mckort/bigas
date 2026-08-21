"""Unit tests for multi-project Jira key parsing (no network)."""

import pytest

from bigas.resources.product.create_release_notes.jira_client import (
    JiraClient,
    JiraConfig,
    JiraError,
    is_invalid_parent_error,
    normalize_parent_epic_key,
    normalize_project_keys,
    parse_project_keys,
    project_jql_clause,
)


def test_parse_project_keys_comma_separated():
    assert parse_project_keys("VFA,WAYW") == ["VFA", "WAYW"]


def test_parse_project_keys_dedupes_and_uppercases():
    assert parse_project_keys(" vfa ; wayw, VFA ") == ["VFA", "WAYW"]


def test_normalize_from_list():
    assert normalize_project_keys(["vfa", "WAYW"]) == ["VFA", "WAYW"]


def test_project_jql_single():
    assert project_jql_clause(["VFA"]) == 'project = "VFA"'


def test_project_jql_multi():
    assert project_jql_clause(["VFA", "WAYW"]) == 'project in ("VFA", "WAYW")'


def test_project_jql_empty_raises():
    with pytest.raises(JiraError):
        project_jql_clause([])


def test_normalize_parent_epic_key_allows_issue_keys_only():
    assert normalize_parent_epic_key(None) is None
    assert normalize_parent_epic_key("") is None
    assert normalize_parent_epic_key("none") is None
    assert normalize_parent_epic_key("GPWW", project_key="GPWW") is None
    assert normalize_parent_epic_key("gpww-2") == "GPWW-2"


def test_is_invalid_parent_error():
    err = JiraError(
        'Jira API error 400: {"errors":{"parent":"Please select valid parent issue."}}'
    )
    assert is_invalid_parent_error(err) is True
    assert is_invalid_parent_error(JiraError("Jira API error 404: missing")) is False


def test_create_issue_retries_without_invalid_parent(monkeypatch):
    cfg = JiraConfig(
        base_url="https://example.atlassian.net",
        email="a@b.c",
        api_token="t",
        project_keys=("GPWW",),
    )
    client = JiraClient(cfg)
    calls = []

    def fake_request(method, url, *, json=None, params=None, expect_json=True):
        import copy

        calls.append(copy.deepcopy(json))
        fields = (json or {}).get("fields") or {}
        if "parent" in fields:
            raise JiraError(
                'Jira API error 400: {"errors":{"parent":"Please select valid parent issue."}}'
            )
        return {"key": "GPWW-10", "id": "1"}

    monkeypatch.setattr(client, "_request_with_retry_429", fake_request)
    result = client.create_issue(
        summary="Fix tracking",
        description_markdown="Body",
        project_key="GPWW",
        parent_epic_key="GPWW-9",
    )
    assert result["key"] == "GPWW-10"
    assert result.get("parent_dropped") is True
    assert "parent_epic_key" not in result
    assert len(calls) == 2
    assert calls[0]["fields"]["parent"]["key"] == "GPWW-9"
    assert "parent" not in calls[1]["fields"]


def test_create_issue_without_parent_does_not_send_parent(monkeypatch):
    cfg = JiraConfig(
        base_url="https://example.atlassian.net",
        email="a@b.c",
        api_token="t",
        project_keys=("GPWW",),
    )
    client = JiraClient(cfg)
    calls = []

    def fake_request(method, url, *, json=None, params=None, expect_json=True):
        calls.append(json)
        return {"key": "GPWW-12", "id": "2"}

    monkeypatch.setattr(client, "_request_with_retry_429", fake_request)
    result = client.create_issue(
        summary="Standalone",
        description_markdown="Body",
        project_key="GPWW",
    )
    assert result["key"] == "GPWW-12"
    assert "parent_dropped" not in result
    assert len(calls) == 1
    assert "parent" not in calls[0]["fields"]
