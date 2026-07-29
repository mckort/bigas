"""Unit tests for Jira AI automation helpers (no network)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bigas.resources.product.create_release_notes.jira_client import (
    adf_to_plain_text,
    markdown_to_adf,
)
from bigas.resources.product.jira_automation.comments import (
    format_human_comments,
    issue_discord_label,
)
from bigas.resources.product.jira_automation.config import (
    BIGAS_COMMENT_MARKER,
    HANDLER_DESIGN,
    HANDLER_RESEARCH,
    JiraAutomationConfig,
)
from bigas.resources.product.jira_automation.description import (
    BRIEF_HEADING,
    PLAN_HEADING,
    RESEARCH_HEADING,
    extract_brief,
    extract_section,
    upsert_plan_section,
    upsert_research_section,
)
from bigas.resources.product.jira_automation.idempotency import IdempotencyCache
from bigas.resources.product.jira_automation.quota import DailyQuota
from bigas.resources.product.jira_automation.research import ResearchHandlerError
from bigas.resources.product.jira_automation import service as jira_automation_service
from bigas.resources.product.jira_automation.service import (
    JiraAutomationService,
    parse_automation_payload,
    verify_webhook_secret,
)


def test_markdown_to_adf_roundtrip_plain():
    md = "## Brief\nHello\n\n## AI Research (Bigas)\n- one\n- two\n"
    adf = markdown_to_adf(md)
    assert adf["type"] == "doc"
    text = adf_to_plain_text(adf)
    assert "## Brief" in text
    assert "Hello" in text
    assert "- one" in text
    assert "- two" in text


def test_adf_heading_roundtrip_preserves_brief_contract():
    """Re-reading ADF must keep ## markers so Brief is not polluted by AI Research."""
    original = (
        f"{BRIEF_HEADING}\n"
        "Ship export from Attio\n\n"
        f"{RESEARCH_HEADING}\n"
        "### Goals\n"
        "old goals\n"
    )
    plain = adf_to_plain_text(markdown_to_adf(original))
    assert extract_brief(plain) == "Ship export from Attio"
    updated = upsert_research_section(plain, research_markdown="### Goals\nnew goals")
    assert extract_brief(updated) == "Ship export from Attio"
    assert "old goals" not in updated
    assert "new goals" in updated
    # Second Jira write/read cycle still preserves Brief
    plain2 = adf_to_plain_text(markdown_to_adf(updated))
    assert extract_brief(plain2) == "Ship export from Attio"


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


def test_upsert_plan_preserves_brief_and_research():
    original = (
        f"{BRIEF_HEADING}\n"
        "Brand reports with logo\n\n"
        f"{RESEARCH_HEADING}\n"
        "### Goals\n"
        "Allow custom logo on PDF reports\n\n"
        f"{PLAN_HEADING}\n"
        "old plan\n"
    )
    updated = upsert_plan_section(original, plan_markdown="### Technical approach\nNew plan")
    assert extract_brief(updated) == "Brand reports with logo"
    assert "Allow custom logo on PDF reports" in extract_section(updated, RESEARCH_HEADING)
    assert "New plan" in extract_section(updated, PLAN_HEADING)
    assert "old plan" not in updated


def test_config_maps_design_status(monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    cfg = JiraAutomationConfig.from_env()
    assert cfg.handler_for_status("Design and plan (AI)") == HANDLER_DESIGN
    assert cfg.status_design_approval == "Design approval (manual)"


def test_format_human_comments_skips_bigas_marker():
    comments = [
        {
            "created": "2026-07-24T10:00:00.000+0200",
            "author": {"displayName": "Marcus"},
            "body": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Use PNG logos only"}]}
            ]},
        },
        {
            "created": "2026-07-24T10:01:00.000+0200",
            "author": {"displayName": "Bigas"},
            "body": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": f"{BIGAS_COMMENT_MARKER} Research complete."}
                ]}
            ]},
        },
    ]
    text = format_human_comments(comments)
    assert "Use PNG logos only" in text
    assert "Marcus" in text
    assert BIGAS_COMMENT_MARKER not in text


def test_issue_discord_label_includes_summary():
    assert issue_discord_label("VFA-14", "Brand reports") == "`VFA-14` — Brand reports"
    assert issue_discord_label("VFA-14", "") == "`VFA-14`"


def test_extract_jira_issue_key_from_pr_texts():
    from bigas.resources.product.jira_automation.final_approval import (
        extract_jira_issue_key,
    )

    assert extract_jira_issue_key("VFA-14: Brand reports", "") == "VFA-14"
    assert extract_jira_issue_key("title", "Jira: WAYW-3\nmore") == "WAYW-3"
    assert extract_jira_issue_key("no key here") is None


def test_linked_issue_entries_include_relation_type():
    from bigas.resources.product.jira_automation.research import (
        _format_linked_issues,
        _linked_issue_entries,
        _linked_issue_keys,
    )

    fields = {
        "issuelinks": [
            {
                "type": {
                    "name": "Blocks",
                    "inward": "is blocked by",
                    "outward": "blocks",
                },
                "outwardIssue": {"key": "VFA-10"},
            },
            {
                "type": {
                    "name": "Relates",
                    "inward": "relates to",
                    "outward": "relates to",
                },
                "inwardIssue": {"key": "VFA-8"},
            },
        ],
        "parent": {"key": "VFA-1"},
    }
    entries = _linked_issue_entries(fields)
    assert _linked_issue_keys(fields) == ["VFA-10", "VFA-8", "VFA-1"]
    by_key = {e["key"]: e for e in entries}
    assert by_key["VFA-10"]["relation"] == "blocks"
    assert by_key["VFA-10"]["direction"] == "outward"
    assert by_key["VFA-8"]["relation"] == "relates to"
    assert by_key["VFA-8"]["direction"] == "inward"
    assert by_key["VFA-1"]["relation"] == "parent"

    class FakeJira:
        def get_issue(self, key, fields=None):
            return {
                "fields": {
                    "summary": f"Summary {key}",
                    "status": {"name": "To Do"},
                    "issuetype": {"name": "Story"},
                    "description": None,
                }
            }

    text = _format_linked_issues(
        FakeJira(),  # type: ignore[arg-type]
        [],
        entries=entries,
    )
    assert "VFA-10 [blocks →]" in text
    assert "VFA-8 [relates to ←]" in text
    assert "VFA-1 [parent ←]" in text


def test_build_implement_prompt_forbids_confirmation():
    from bigas.resources.product.jira_automation.implement import build_implement_prompt

    prompt = build_implement_prompt(
        issue_key="VFA-14",
        summary="Brand reports",
        brief="logo",
        research="research",
        plan="plan",
        comments_text="(none)",
        repo="mckort/vcfieldassistant",
    )
    assert "Do NOT ask for confirmation" in prompt
    assert "implement immediately" in prompt
    assert "Update the repository README" in prompt
    assert "Update in-app support/help content" in prompt
    assert "small mobile screen" in prompt
    assert "responsive design" in prompt
    assert "VFA-14:" in prompt
    assert "Jira: VFA-14" in prompt


def test_design_prompts_include_readme_impact():
    from bigas.resources.product.jira_automation.prompts import (
        WORKSTREAM_MARKETING,
        WORKSTREAM_PRODUCT,
        design_prompts_for,
    )

    _, build_product = design_prompts_for(WORKSTREAM_PRODUCT)
    product_plan = build_product(
        issue_key="VFA-1",
        summary="x",
        brief="b",
        research="r",
        linked_issues_text="(none)",
        repo_context="(none)",
    )
    assert "### README / docs impact" in product_plan
    assert "### In-app support / help impact" in product_plan
    assert "### Mobile / responsive considerations (if UI)" in product_plan

    product_system, _ = design_prompts_for(WORKSTREAM_PRODUCT)
    assert "in-app support/help impact" in product_system
    assert "responsive design" in product_system
    assert "small mobile screen" in product_system

    _, build_marketing = design_prompts_for(WORKSTREAM_MARKETING)
    marketing_plan = build_marketing(
        issue_key="WAYW-1",
        summary="x",
        brief="b",
        research="r",
        linked_issues_text="(none)",
        repo_context="(none)",
    )
    assert "### README / docs impact" in marketing_plan
    assert "### In-app support / help impact" not in marketing_plan
    assert "### Mobile / responsive considerations (if UI)" in marketing_plan

    marketing_system, _ = design_prompts_for(WORKSTREAM_MARKETING)
    assert "responsive design" in marketing_system
    assert "small mobile screen" in marketing_system


def test_resolve_workstream_defaults_to_product():
    from bigas.resources.product.jira_automation.prompts import (
        WORKSTREAM_MARKETING,
        WORKSTREAM_PRODUCT,
        implement_prompt_for,
        research_prompts_for,
        resolve_workstream,
    )

    assert resolve_workstream(None) == WORKSTREAM_PRODUCT
    assert resolve_workstream([]) == WORKSTREAM_PRODUCT
    assert resolve_workstream(["bug", "backend"]) == WORKSTREAM_PRODUCT
    assert resolve_workstream(["Marketing"]) == WORKSTREAM_MARKETING
    assert resolve_workstream(["seo", "marketing"]) == WORKSTREAM_MARKETING

    product_system, _ = research_prompts_for(WORKSTREAM_PRODUCT)
    marketing_system, _ = research_prompts_for(WORKSTREAM_MARKETING)
    assert "product engineer" in product_system
    assert "marketing + web content" in marketing_system

    marketing_impl = implement_prompt_for(WORKSTREAM_MARKETING)(
        issue_key="WAYW-1",
        summary="SEO",
        brief="fix titles",
        research="r",
        plan="p",
        comments_text="(none)",
        repo="mckort/roadpal",
    )
    assert "marketing/website" in marketing_impl
    assert "SEO basics" in marketing_impl
    assert "Update the repository README" in marketing_impl
    assert "small mobile screen" in marketing_impl
    assert "responsive design" in marketing_impl
    assert "Do NOT ask for confirmation" in marketing_impl


def test_extract_pr_and_branch_from_cursor_payload():
    from bigas.resources.cto.autofix.cursor_client import extract_pr_and_branch

    pr, branch = extract_pr_and_branch(
        {
            "target": {
                "branchName": "cursor/vfa-14-x",
                "prUrl": "https://github.com/mckort/vcfieldassistant/pull/99",
            }
        }
    )
    assert branch == "cursor/vfa-14-x"
    assert "pull/99" in pr


def test_evaluate_implementation_outcome_finished_no_pr(monkeypatch):
    from bigas.resources.product.jira_automation import implement as impl

    monkeypatch.setattr(impl, "lookup_pr_url_for_branch", lambda **_k: "")
    out = impl.evaluate_implementation_outcome(
        {
            "status": "FINISHED",
            "pr_url": "",
            "branch_name": "cursor/gone",
            "agent_url": "https://cursor.com/agents/abc",
        },
        repo="mckort/vcfieldassistant",
    )
    assert out["kind"] == "finished_no_pr"
    assert "confirmation" in out["detail"].lower()


def test_evaluate_implementation_outcome_pr_opened(monkeypatch):
    from bigas.resources.product.jira_automation import implement as impl

    monkeypatch.setattr(impl, "lookup_pr_url_for_branch", lambda **_k: "")
    out = impl.evaluate_implementation_outcome(
        {
            "status": "FINISHED",
            "pr_url": "https://github.com/mckort/vcfieldassistant/pull/12",
            "branch_name": "cursor/ok",
            "agent_url": "https://cursor.com/agents/abc",
        },
        repo="mckort/vcfieldassistant",
    )
    assert out["kind"] == "pr_opened"
    assert out["pr_url"].endswith("/pull/12")


def test_config_maps_implement_status(monkeypatch):
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "abc")
    from bigas.resources.product.jira_automation.config import HANDLER_IMPLEMENT

    cfg = JiraAutomationConfig.from_env()
    assert cfg.handler_for_status("In Progress (AI)") == HANDLER_IMPLEMENT
    assert cfg.default_base_branch == "main"


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
    assert cfg.allowed_projects == ("VFA", "WAYW", "BIG", "REM", "GPWW", "FYDA", "MYL")
    assert cfg.repo_for_project("VFA") == "mckort/vcfieldassistant"
    assert cfg.repo_for_project("MYL") == "mckort/mylifesdeed"
    assert cfg.repo_for_project("GPWW") == "Green-Promo-Wear-Global/greenpromowear-website"
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


def test_quota_release_restores_slot():
    q = DailyQuota(1)
    assert q.try_acquire()[0] is True
    assert q.try_acquire()[0] is False
    used, limit = q.release()
    assert used == 0
    assert limit == 1
    assert q.try_acquire()[0] is True


def test_idempotency_cache():
    c = IdempotencyCache(ttl_s=60)
    assert c.already_processed("a") is False
    c.mark_processed("a")
    assert c.already_processed("a") is True


def test_idempotency_try_claim_and_clear():
    c = IdempotencyCache(ttl_s=60)
    assert c.try_claim("k") is True
    assert c.try_claim("k") is False
    c.clear("k")
    assert c.try_claim("k") is True


def test_handle_event_failure_clears_idempotency_and_quota(monkeypatch):
    jira_automation_service._IDEMPOTENCY = IdempotencyCache(ttl_s=60)
    jira_automation_service._QUOTA = DailyQuota(5)

    cfg = JiraAutomationConfig(
        webhook_secret="s",
        allowed_projects=("VFA",),
        project_repos={"VFA": "mckort/vcfieldassistant"},
        status_handlers={"research and describe (ai)": HANDLER_RESEARCH},
        status_description_approval="Description approval (manual)",
        status_design_approval="Design approval (manual)",
        status_final_approval="Final approval (manual)",
        daily_quota=5,
        default_base_branch="main",
        discord_pm_env="DISCORD_WEBHOOK_URL_PRODUCT",
        discord_cto_env="DISCORD_WEBHOOK_URL_CTO",
    )
    jira = MagicMock()
    jira.add_comment = MagicMock()

    class BoomHandler:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, **kwargs):
            raise ResearchHandlerError("llm down")

    monkeypatch.setattr(
        jira_automation_service,
        "ResearchDescribeHandler",
        BoomHandler,
    )
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_PRODUCT", "")

    svc = JiraAutomationService(config=cfg, jira=jira)
    result = svc.handle_event(
        issue_key="VFA-1",
        to_status="Research and describe (AI)",
        from_status="To Do",
        project_key="VFA",
        idempotency_key="stable-1",
    )
    assert result["ok"] is False
    # Failure must be retryable
    assert not jira_automation_service._IDEMPOTENCY.already_processed(
        f"{HANDLER_RESEARCH}:stable-1"
    )
    used, limit, _day = jira_automation_service._QUOTA.snapshot()
    assert used == 0
    assert limit == 5
