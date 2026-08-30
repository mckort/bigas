"""Unit tests for progress-update project/label grouping helpers."""
from __future__ import annotations

from unittest.mock import MagicMock

from bigas.resources.product.progress_updates.prompts import (
    build_progress_updates_user_prompt,
)
from bigas.resources.product.progress_updates.service import (
    MAX_PROGRESS_UPDATES_OUTPUT_TOKENS,
    ProgressUpdatesService,
    UNLABELED_GROUP,
    _aggregate_stats,
    _format_done_issues_for_prompt,
    _normalize_done_issue,
    _project_key_from_issue_key,
    group_issues_by_label,
    normalize_ignore_labels,
)
from bigas.tickets.jira_adapter import TicketJiraAdapter
from bigas.tickets.store import MemoryTicketStore


def test_store_sets_and_clears_done_at_on_status_change():
    store = MemoryTicketStore()
    board = store.create_board("u1", name="VFA Board", project_key="VFA")
    ticket = store.create_ticket(board["board_id"], title="Rank companies")
    assert ticket["status"] != "Done"
    assert not ticket.get("done_at")

    done = store.update_ticket(ticket["ticket_id"], status="Done")
    assert done is not None
    assert done["status"] == "Done"
    assert done["done_at"]

    same = store.update_ticket(ticket["ticket_id"], title="Rank companies (updated)")
    assert same is not None
    assert same["done_at"] == done["done_at"]

    reopened = store.update_ticket(ticket["ticket_id"], status="To Do")
    assert reopened is not None
    assert reopened["status"] == "To Do"
    assert not reopened.get("done_at")



def test_project_key_from_issue_key():
    assert _project_key_from_issue_key("VFA-12") == "VFA"
    assert _project_key_from_issue_key("wayw-3") == "WAYW"


def test_normalize_includes_project_key_and_labels():
    issue = {
        "key": "BIG-9",
        "fields": {
            "summary": "Ship usage",
            "issuetype": {"name": "Story"},
            "assignee": {"displayName": "Ada"},
            "labels": ["Customer request", "Turbine"],
        },
    }
    norm = _normalize_done_issue(issue)
    assert norm["project_key"] == "BIG"
    assert norm["labels"] == ["customer-request", "turbine"]


def test_aggregate_stats_includes_empty_projects():
    normalized = [
        {
            "key": "VFA-1",
            "project_key": "VFA",
            "summary": "A",
            "issue_type": "Task",
            "assignee": "Ada",
            "labels": ["turbine"],
        }
    ]
    stats = _aggregate_stats(normalized, project_keys=["VFA", "WAYW", "BIG"])
    assert stats["total"] == 1
    assert stats["by_project"]["VFA"] == 1
    assert stats["by_project"]["WAYW"] == 0
    assert stats["by_project"]["BIG"] == 0
    assert stats["by_label"]["turbine"] == 1


def test_format_groups_by_project():
    text = _format_done_issues_for_prompt(
        [
            {
                "key": "WAYW-2",
                "project_key": "WAYW",
                "summary": "B",
                "issue_type": "Bug",
                "assignee": "Ada",
                "labels": [],
            },
            {
                "key": "VFA-1",
                "project_key": "VFA",
                "summary": "A",
                "issue_type": "Task",
                "assignee": "Ada",
                "labels": ["turbine"],
            },
        ]
    )
    assert "### VFA" in text
    assert "### WAYW" in text
    assert text.index("### VFA") < text.index("### WAYW")
    assert "labels=turbine" in text
    assert "unlabeled" in text


def test_group_by_label_ignores_customer_request_and_unlabeled():
    issues = [
        {
            "key": "VFA-28",
            "summary": "Rank companies",
            "labels": ["customer-request", "e14-invest"],
        },
        {
            "key": "VFA-18",
            "summary": "Stripe payments",
            "labels": ["customer-request"],
        },
        {
            "key": "VFA-11",
            "summary": "CSV import",
            "labels": ["Turbine", "customer-request"],
        },
        {
            "key": "VFA-6",
            "summary": "Company labels",
            "labels": [],
        },
    ]
    grouped = group_issues_by_label(issues)
    assert set(grouped) == {"e14-invest", "turbine", UNLABELED_GROUP}
    assert [i["key"] for i in grouped["e14-invest"]] == ["VFA-28"]
    assert [i["key"] for i in grouped["turbine"]] == ["VFA-11"]
    assert [i["key"] for i in grouped[UNLABELED_GROUP]] == ["VFA-18", "VFA-6"]


def test_format_groups_by_label():
    text = _format_done_issues_for_prompt(
        [
            {
                "key": "VFA-28",
                "summary": "Rank",
                "issue_type": "Task",
                "assignee": "Ada",
                "labels": ["customer-request", "e14-invest"],
            },
            {
                "key": "VFA-18",
                "summary": "Stripe",
                "issue_type": "Task",
                "assignee": "Ada",
                "labels": [],
            },
        ],
        group_by="label",
        ignore_labels=normalize_ignore_labels(None),
    )
    assert "### e14-invest" in text
    assert "### Unlabeled" in text
    assert "customer-request" not in text.split("###")[1]
    assert text.index("### e14-invest") < text.index("### Unlabeled")


def test_label_prompt_forbids_invented_themes():
    prompt = build_progress_updates_user_prompt(
        stats={
            "total": 2,
            "by_type": {"Task": 2},
            "by_project": {"VFA": 2},
            "by_label": {"turbine": 1, "Unlabeled": 1},
        },
        done_issues_text="### turbine\n- [VFA-11] Task: CSV (Ada) labels=turbine",
        days=60,
        group_by="label",
        ignore_labels=["customer-request"],
    )
    assert "invent product-area" in prompt.lower()
    assert "customer-request" in prompt
    assert "Unlabeled" in prompt


def test_internal_board_search_uses_done_at_and_labels():
    store = MemoryTicketStore()
    board = store.create_board("u1", name="VFA Board", project_key="VFA")
    recent = store.create_ticket(
        board["board_id"],
        title="Rank companies",
        labels=["customer-request", "e14-invest"],
    )
    store.update_ticket(recent["ticket_id"], status="Done")
    stale = store.create_ticket(board["board_id"], title="Ancient work")
    store.update_ticket(stale["ticket_id"], status="Done")
    store._tickets[stale["ticket_id"]]["done_at"] = "2020-01-01T00:00:00+00:00"

    adapter = TicketJiraAdapter()
    adapter._store = store
    found = adapter.search_issues_done_in_last_n_days(days=60, project_keys=["VFA"])
    keys = [issue["key"] for issue in found]
    assert recent["key"] in keys
    assert stale["key"] not in keys
    labels = found[0]["fields"]["labels"]
    assert "e14-invest" in labels
    assert "customer-request" in labels
    assert found[0]["fields"]["resolutiondate"]


def test_progress_updates_service_groups_internal_board_by_label(monkeypatch):
    store = MemoryTicketStore()
    board = store.create_board("u1", name="VFA Board", project_key="VFA")
    labeled = store.create_ticket(
        board["board_id"],
        title="CSV import",
        labels=["Turbine", "customer-request"],
    )
    unlabeled = store.create_ticket(board["board_id"], title="Stripe payments")
    store.update_ticket(labeled["ticket_id"], status="Done")
    store.update_ticket(unlabeled["ticket_id"], status="Done")

    adapter = TicketJiraAdapter()
    adapter._store = store
    llm = MagicMock()
    llm.complete.return_value = "Turbine shipped CSV import."
    monkeypatch.setattr(
        "bigas.resources.product.progress_updates.service.get_llm_client",
        lambda **_kwargs: (llm, "fake"),
    )

    service = ProgressUpdatesService(jira_client=adapter, include_git=False)
    result = service.run(
        days=60,
        project_keys=["VFA"],
        group_by="label",
    )
    assert result["group_by"] == "label"
    assert result["ignore_labels"] == ["customer-request"]
    assert result["stats"]["by_label"]["turbine"] == 1
    assert result["stats"]["by_label"][UNLABELED_GROUP] == 1
    prompt = llm.complete.call_args.kwargs["messages"][1]["content"]
    assert llm.complete.call_args.kwargs["max_tokens"] == MAX_PROGRESS_UPDATES_OUTPUT_TOKENS
    assert "### turbine" in prompt
    assert "### Unlabeled" in prompt
    assert "invent product-area" in prompt.lower()
