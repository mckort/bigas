"""Unit tests for Jira AI automation helpers (no network)."""

from __future__ import annotations

import pytest

from bigas.resources.product.create_release_notes.jira_client import (
    adf_to_plain_text,
    markdown_to_adf,
)
from bigas.resources.product.jira_automation.config import (
    HANDLER_RESEARCH,
    JiraAutomationConfig,
)
from bigas.resources.product.jira_automation.description import (
    BRIEF_HEADING,
    RESEARCH_HEADING,
    extract_brief,
    upsert_research_section,
)
from bigas.resources.product.jira_automation.idempotency import IdempotencyCache
from bigas.resources.product.jira_automation.quota import DailyQuota
from bigas.resources.product.jira_automation.service import (
    parse_automation_payload,
    verify_webhook_secret,
)


def test_markdown_to_adf_roundtrip_plain():
    md = "## Brief\nHello\n\n## AI Research (Bigas)\n- one\n- two\n"
    adf = markdown_to_adf(md)
    assert adf["type"] == "doc"
    text = adf_to_plain_text(adf)
    assert "Brief" in text
    assert "Hello" in text
    assert "one" in text


def test_extract_brief_and_upsert_preserves_plan():
    original = (
        f"{BRIEF_HEADING}\n"
        "Ship export from Attio\n\n"
        f"{RESEARCH_HEADING}\n"
        "old research\n\n"
        "## AI Plan (Bigas)\n"
        "keep this plan\n"
    )
    assert extract_brief(original) == "Ship export from Attio"
    updated = upsert_research_section(original, research_markdown="### Goals\nNew goals")
    assert "Ship export from Attio" in updated
    assert "New goals" in updated
    assert "keep this plan" in updated
    assert "old research" not in updated


def test_extract_brief_without_heading_uses_pre_ai_text():
    text = "Human wrote this briefly.\n\n## AI Research (Bigas)\nAI stuff\n"
    assert extract_brief(text) == "Human wrote this briefly."


def test_verify_webhook_secret_bearer_and_plain():
    assert verify_webhook_secret("s3cret", "s3cret")
    assert verify_webhook_secret("Bearer s3cret", "s3cret")
    assert not verify_webhook_secret("wrong", "s3cret")
    assert not verify_webhook_secret("s3cret", "")


def test_parse_automation_payload_flat_and_nested():
    flat = parse_automation_payload(
        {
            "issue_key": "VFA-9",
            "to_status": "Research and describe (AI)",
            "from_status": "To Do",
        }
    )
    assert flat["issue_key"] == "VFA-9"
    assert flat["project_key"] == "VFA"
    assert flat["to_status"] == "Research and describe (AI)"

    nested = parse_automation_payload(
        {
            "issue": {
                "key": "WAYW-2",
                "fields": {"status": {"name": "Design and plan (AI)"}, "project": {"key": "WAYW"}},
            }
        }
    )
    assert nested["issue_key"] == "WAYW-2"
    assert nested["project_key"] == "WAYW"
    assert nested["to_status"] == "Design and plan (AI)"


def test_config_defaults(monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    monkeypatch.delenv("BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS", raising=False)
    cfg = JiraAutomationConfig.from_env()
    assert cfg.webhook_secret == "abc"
    assert cfg.allowed_projects == ("VFA",)
    assert cfg.repo_for_project("VFA") == "mckort/vcfieldassistant"
    assert cfg.handler_for_status("Research and describe (AI)") == HANDLER_RESEARCH
    assert cfg.daily_quota == 20


def test_quota_blocks_after_limit():
    q = DailyQuota(2)
    assert q.try_acquire()[0] is True
    assert q.try_acquire()[0] is True
    ok, used, limit = q.try_acquire()
    assert ok is False
    assert used == 2
    assert limit == 2


def test_idempotency_cache():
    c = IdempotencyCache(ttl_s=60)
    assert c.already_processed("a") is False
    c.mark_processed("a")
    assert c.already_processed("a") is True
