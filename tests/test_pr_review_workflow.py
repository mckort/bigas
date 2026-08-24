"""Guardrails so every product repo can share the same PR review flow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/pr-review.yml").read_text()
CALLER = (ROOT / "docs/pr-review.caller.yml").read_text()


def test_canonical_workflow_is_reusable_and_moves_tickets_on_merge():
    assert "workflow_call:" in WORKFLOW
    assert "closed" in WORKFLOW
    assert "notify_merged:" in WORKFLOW
    assert "notify_pr_merged" in WORKFLOW
    assert "github.token" in WORKFLOW
    assert "retrying with GH_PAT_FOR_BIGAS" in WORKFLOW
    assert "permissions:" in WORKFLOW


def test_product_repos_should_call_canonical_workflow_not_copy_it():
    assert "uses: mckort/bigas/.github/workflows/pr-review.yml@main" in CALLER
    assert "notify_merged:" not in CALLER
    assert "pull_request:" in CALLER
    assert "closed" in CALLER
