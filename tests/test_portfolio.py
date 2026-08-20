"""Tests for portfolio project / GA4 resolution."""
from __future__ import annotations

import pytest

from bigas.agents.chief_of_staff import _enrich_tool_args
from bigas.github_refs import parse_cursor_agent_id, parse_github_pr, resolve_repo_and_pr
from bigas.portfolio import (
    ga4_property_for_project,
    normalize_project_key,
    prompt_block,
    resolve_ga4_property,
    resolve_project,
    scrub_analytics_question,
)


@pytest.fixture
def portfolio_env(monkeypatch):
    monkeypatch.setenv("JIRA_PROJECT_KEY", "VFA,WAYW,BIG,REM,GPWW,FYDA,MYL")
    monkeypatch.setenv(
        "BIGAS_JIRA_PROJECT_REPO_MAP",
        "VFA:mckort/vcfieldassistant,WAYW:mckort/roadpal,BIG:mckort/bigas,"
        "REM:mckort/remotebrief,GPWW:Green-Promo-Wear-Global/greenpromowear-website,"
        "FYDA:mckort/fulfillyourdreamadventure,MYL:mckort/mylifesdeed",
    )
    monkeypatch.setenv("GA4_PROPERTY_ID", "473559548")
    monkeypatch.setenv("BIGAS_GA4_PROPERTY_MAP", "GPWW:473559548")
    monkeypatch.setenv(
        "MONITOR_URLS",
        "https://mylifesdeed.com,https://fyda.today,https://vcfieldassistant.com,"
        "https://remotebrief.com,https://greenpromowear.com",
    )


def test_resolve_project_from_brand_and_repo(portfolio_env):
    assert resolve_project("inbound traffic to greenpromowear") == "GPWW"
    assert resolve_project("review the roadpal PR") == "WAYW"
    assert resolve_project("VFA-12 is stuck") == "VFA"
    assert resolve_project("how is fyda.today doing") == "FYDA"
    assert resolve_project("hello") is None


def test_resolve_project_prefers_longer_alias_over_short_substring(portfolio_env, monkeypatch):
    import bigas.portfolio as portfolio

    monkeypatch.setitem(portfolio.DEFAULT_PROJECT_ALIASES, "VFA", ["road"])
    assert resolve_project("review the roadpal PR") == "WAYW"


def test_scrub_preserves_substring_words(portfolio_env):
    out = scrub_analytics_question("wayward traffic trends", "WAYW")
    assert "wayward" in out.lower()


def test_ga4_only_configured_for_gpww(portfolio_env):
    assert ga4_property_for_project("GPWW") == "473559548"
    prop, key, err = resolve_ga4_property("traffic to greenpromowear")
    assert prop == "473559548"
    assert key == "GPWW"
    assert err is None

    prop, key, err = resolve_ga4_property("traffic", project_key=["GPWW"])
    assert prop == "473559548"
    assert key == "GPWW"
    assert err is None

    prop, key, err = resolve_ga4_property("sessions on vcfieldassistant")
    assert prop is None
    assert key == "VFA"
    assert err and "No GA4 property" in err


def test_scrub_brand_from_analytics_question(portfolio_env):
    out = scrub_analytics_question(
        "How is the inbound traffic to greenpromowear going recently?",
        "GPWW",
    )
    assert "greenpromowear" not in out.lower()
    assert "traffic" in out.lower()


def test_enrich_analytics_args_sets_project_and_scrubs_brand(portfolio_env):
    args = _enrich_tool_args(
        "ask_analytics_question",
        {"question": "How is inbound traffic to greenpromowear going recently?"},
        "How is inbound traffic to greenpromowear going recently?",
    )
    assert args["project_key"] == "GPWW"
    assert "greenpromowear" not in args["question"].lower()
    assert "traffic" in args["question"].lower()


def test_normalize_project_key_accepts_list():
    assert normalize_project_key("vfa") == "VFA"
    assert normalize_project_key(["WAYW", "VFA"]) == "WAYW"
    assert normalize_project_key(None) == ""


def test_prompt_block_lists_all_projects(portfolio_env):
    block = prompt_block()
    for key in ("VFA", "WAYW", "BIG", "REM", "GPWW", "FYDA", "MYL"):
        assert key in block
    assert "mckort/roadpal" in block
    assert "not configured" in block
    assert "pr_url" in block


def test_parse_github_pr_and_cursor_agent():
    assert parse_github_pr(
        "review https://github.com/mckort/vcfieldassistant/pull/121 please"
    ) == ("mckort/vcfieldassistant", 121)
    assert parse_github_pr("https://www.github.com/acme/app/pull/3/files") == ("acme/app", 3)
    assert parse_github_pr("no link here") is None
    assert (
        parse_cursor_agent_id(
            "Agent: https://cursor.com/agents/bc-c71a88db-7821-4e44-9717-9f845ad7406b"
        )
        == "bc-c71a88db-7821-4e44-9717-9f845ad7406b"
    )
    assert parse_cursor_agent_id("bc-c71a88db-7821-4e44-9717-9f845ad7406b") == (
        "bc-c71a88db-7821-4e44-9717-9f845ad7406b"
    )


def test_resolve_repo_and_pr_from_url_or_fields():
    repo, number = resolve_repo_and_pr(
        text="https://github.com/mckort/vcfieldassistant/pull/121"
    )
    assert repo == "mckort/vcfieldassistant"
    assert number == 121
    repo, number = resolve_repo_and_pr(repo="acme/app", pr_number="9")
    assert repo == "acme/app"
    assert number == 9
    repo, number = resolve_repo_and_pr(
        repo="https://github.com/mckort/vcfieldassistant/pull/121"
    )
    assert repo == "mckort/vcfieldassistant"
    assert number == 121


def test_enrich_pr_args_from_github_and_cursor_urls(portfolio_env):
    message = (
        "gör en review av: CTO autofix launched (2/5) "
        "PR: https://github.com/mckort/vcfieldassistant/pull/121 "
        "Agent: https://cursor.com/agents/bc-c71a88db-7821-4e44-9717-9f845ad7406b"
    )
    args = _enrich_tool_args("review_and_comment_pr", {}, message)
    assert args["repo"] == "mckort/vcfieldassistant"
    assert args["pr_number"] == 121
    assert args["pr_url"].endswith("/pull/121")
    assert args["agent_id"] == "bc-c71a88db-7821-4e44-9717-9f845ad7406b"
    assert args["project_key"] == "VFA"


def test_enrich_pr_args_fills_repo_from_project_map(portfolio_env):
    args = _enrich_tool_args("autofix_pr", {"pr_number": 4}, "review the roadpal PR")
    assert args["project_key"] == "WAYW"
    assert args["repo"] == "mckort/roadpal"
    assert args["pr_number"] == 4
