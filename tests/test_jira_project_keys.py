"""Unit tests for multi-project Jira key parsing (no network)."""

import pytest

from bigas.resources.product.create_release_notes.jira_client import (
    JiraError,
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
