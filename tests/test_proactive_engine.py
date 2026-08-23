"""Unit tests for the proactive Goal Engine (BIG-12)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "gpt-4.1-mini")
os.environ.setdefault("CHAT_ENABLED", "true")
os.environ.setdefault("CHAT_STORAGE_MODE", "memory")
os.environ.setdefault("CHAT_AUTH_MODE", "dev")

from bigas.agents.proactive_engine import (
    ProactiveGoalEngine,
    _is_duplicate_task,
    _parse_llm_json,
    default_goal_timeframe_days,
    goal_epic_statuses,
    goal_phase_for_status,
    in_progress_status_clause,
    run_evaluation_loop,
)
from bigas.resources.product.create_release_notes.jira_client import JiraClient


class FakeLLM:
    def __init__(self, payload: dict):
        self.payload = payload

    def complete(self, *, messages, max_tokens=2000, temperature=0.5, **kwargs):
        return json.dumps(self.payload)


def _epic(key: str, status: str, summary: str = "Goal") -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
            "description": {"type": "doc", "content": []},
            "project": {"key": key.split("-")[0]},
            "issuetype": {"name": "Epic"},
        },
    }


def _issue(key: str, status: str, summary: str) -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
        },
    }


class FakeJiraClient:
    def __init__(self, epics=None, epic_children=None):
        self.epics = epics or []
        self.epic_children = epic_children or {}

    def get_epics_by_statuses(self, *, statuses, project_keys=None, fields=None):
        wanted = {str(s).strip().lower() for s in (statuses or [])}
        if not wanted:
            return list(self.epics)
        return [
            e for e in self.epics
            if ((e.get("fields") or {}).get("status") or {}).get("name", "").strip().lower() in wanted
        ]

    def get_issue(self, issue_key, *, fields=None, expand=None):
        for epic in self.epics:
            if epic.get("key") == issue_key:
                return epic
        raise RuntimeError(f"not found: {issue_key}")

    def get_issues_for_epic(
        self,
        epic_key,
        *,
        status_clause="",
        updated_since_days=None,
        fields=None,
    ):
        rows = list(self.epic_children.get(epic_key, []))
        clause = (status_clause or "").strip().lower()
        if "status = done" in clause:
            rows = [r for r in rows if r["fields"]["status"]["name"] == "Done"]
        elif "status != done" in clause:
            rows = [r for r in rows if r["fields"]["status"]["name"] != "Done"]
        elif "in progress" in clause:
            rows = [
                r
                for r in rows
                if "in progress" in r["fields"]["status"]["name"].lower()
            ]
        return rows


def test_goal_phase_maps_board_columns():
    assert goal_phase_for_status("Research") == "research"
    assert goal_phase_for_status("Research and describe (AI)") == "research"
    assert goal_phase_for_status("Plan") == "plan"
    assert goal_phase_for_status("Design and plan (AI)") == "plan"
    assert goal_phase_for_status("In Progress") == "in_progress"
    assert goal_phase_for_status("In Progress (AI)") == "in_progress"
    assert goal_phase_for_status("To Do") is None


def test_goal_epic_statuses_include_ai_columns(monkeypatch):
    monkeypatch.delenv("BIGAS_GOAL_EPIC_STATUSES", raising=False)
    statuses = goal_epic_statuses()
    assert "Research and describe (AI)" in statuses
    assert "Design and plan (AI)" in statuses
    assert "In Progress (AI)" in statuses
    assert "In Progress" in statuses
    assert 'status in ("In Progress", "In Progress (AI)")' in in_progress_status_clause()


def test_default_goal_timeframe_days(monkeypatch):
    monkeypatch.delenv("BIGAS_GOAL_DEFAULT_TIMEFRAME_DAYS", raising=False)
    assert default_goal_timeframe_days() == 7
    monkeypatch.setenv("BIGAS_GOAL_DEFAULT_TIMEFRAME_DAYS", "14")
    assert default_goal_timeframe_days() == 14
    monkeypatch.setenv("BIGAS_GOAL_DEFAULT_TIMEFRAME_DAYS", "999")
    assert default_goal_timeframe_days() == 365
    monkeypatch.setenv("BIGAS_GOAL_DEFAULT_TIMEFRAME_DAYS", "nope")
    assert default_goal_timeframe_days() == 7


def test_parse_llm_json_from_fence():
    text = 'Here you go:\n```json\n{"progress_report": "ok", "tasks_to_create": []}\n```'
    data = _parse_llm_json(text)
    assert data["progress_report"] == "ok"


def test_is_duplicate_task_detects_similar_summary():
    open_issues = [{"summary": "Set up GA4 conversion tracking", "key": "BIG-1", "status": "To Do"}]
    assert _is_duplicate_task("Set up GA4 conversion tracking", open_issues)
    assert not _is_duplicate_task("Implement checkout funnel", open_issues)


def test_research_epic_creates_tasks(monkeypatch):
    fake_jira = FakeJiraClient(
        epics=[_epic("BIG-10", "Research", "Launch feature X")],
        epic_children={"BIG-10": []},
    )
    engine = ProactiveGoalEngine(jira_client=fake_jira)
    engine._llm = FakeLLM(
        {
            "analysis": "Need user interviews",
            "tasks_to_create": [
                {
                    "summary": "Interview 5 users",
                    "description": "Run discovery calls",
                    "issue_type": "Task",
                }
            ],
        }
    )

    created = []

    def fake_create(**kwargs):
        created.append(kwargs)
        return {"ok": True, "key": "BIG-99", "url": "https://x/browse/BIG-99"}

    monkeypatch.setattr(
        "bigas.agents.proactive_engine.CreateJiraIssueService.create",
        lambda self, **kw: fake_create(**kw),
    )
    monkeypatch.setattr(engine, "_post_planning_update", lambda **kw: None)

    result = engine.run(timeframe_days=7)
    assert result["ok"] is True
    assert len(result["results"]) == 1
    assert result["results"][0]["tasks_created"][0]["key"] == "BIG-99"
    assert created[0]["parent_epic_key"] == "BIG-10"


def test_research_ai_column_epic_creates_tasks(monkeypatch):
    fake_jira = FakeJiraClient(
        epics=[_epic("BIG-10", "Research and describe (AI)", "Launch feature X")],
        epic_children={"BIG-10": []},
    )
    engine = ProactiveGoalEngine(jira_client=fake_jira)
    engine._llm = FakeLLM(
        {
            "analysis": "Need user interviews",
            "tasks_to_create": [
                {
                    "summary": "Interview 5 users",
                    "description": "Run discovery calls",
                    "issue_type": "Task",
                }
            ],
        }
    )
    created = []

    def fake_create(**kwargs):
        created.append(kwargs)
        return {"ok": True, "key": "BIG-99", "url": "https://x/browse/BIG-99"}

    monkeypatch.setattr(
        "bigas.agents.proactive_engine.CreateJiraIssueService.create",
        lambda self, **kw: fake_create(**kw),
    )
    monkeypatch.setattr(engine, "_post_planning_update", lambda **kw: None)

    result = engine.run(timeframe_days=7)
    assert result["results"][0]["phase"] == "research"
    assert result["results"][0]["tasks_created"][0]["key"] == "BIG-99"


def test_plan_epic_creates_tasks(monkeypatch):
    fake_jira = FakeJiraClient(
        epics=[_epic("BIG-10", "Design and plan (AI)", "Launch feature X")],
        epic_children={"BIG-10": []},
    )
    engine = ProactiveGoalEngine(jira_client=fake_jira)
    engine._llm = FakeLLM(
        {
            "plan_summary": "Break into setup and launch tasks",
            "tasks_to_create": [
                {
                    "summary": "Draft launch checklist",
                    "description": "Todo-ready planning task",
                    "issue_type": "Task",
                }
            ],
        }
    )
    created = []

    def fake_create(**kwargs):
        created.append(kwargs)
        return {"ok": True, "key": "BIG-98", "url": "https://x/browse/BIG-98"}

    monkeypatch.setattr(
        "bigas.agents.proactive_engine.CreateJiraIssueService.create",
        lambda self, **kw: fake_create(**kw),
    )
    monkeypatch.setattr(engine, "_post_planning_update", lambda **kw: None)

    result = engine.run(timeframe_days=7)
    assert result["results"][0]["phase"] == "plan"
    assert result["results"][0]["tasks_created"][0]["key"] == "BIG-98"
    assert created[0]["parent_epic_key"] == "BIG-10"


def test_run_single_epic_key(monkeypatch):
    fake_jira = FakeJiraClient(
        epics=[
            _epic("BIG-10", "Research and describe (AI)"),
            _epic("BIG-11", "In Progress (AI)"),
        ],
        epic_children={"BIG-10": [], "BIG-11": []},
    )
    engine = ProactiveGoalEngine(jira_client=fake_jira)
    engine._llm = FakeLLM({"analysis": "ok", "tasks_to_create": []})
    monkeypatch.setattr(engine, "_post_planning_update", lambda **kw: None)

    result = engine.run(timeframe_days=7, epic_key="BIG-10")
    assert result["epics_found"] == 1
    assert result["results"][0]["epic_key"] == "BIG-10"
    assert result["results"][0]["phase"] == "research"


def test_in_progress_epic_posts_report_and_skips_duplicates(monkeypatch):
    fake_jira = FakeJiraClient(
        epics=[_epic("BIG-20", "In Progress (AI)", "Ship v2")],
        epic_children={
            "BIG-20": [
                _issue("BIG-21", "Done", "Finished API"),
                _issue("BIG-22", "In Progress (AI)", "Build UI"),
                _issue("BIG-23", "To Do", "Write docs"),
            ]
        },
    )
    engine = ProactiveGoalEngine(jira_client=fake_jira)
    engine._llm = FakeLLM(
        {
            "progress_report": "## Weekly progress\n- Shipped API",
            "tasks_to_create": [
                {
                    "summary": "Write docs",
                    "description": "Duplicate of open item",
                    "issue_type": "Task",
                },
                {
                    "summary": "Add monitoring alerts",
                    "description": "New work",
                    "issue_type": "Task",
                },
            ],
        }
    )

    posted = []

    def fake_post(report, *, epic_key):
        posted.append((epic_key, report))

    monkeypatch.setattr(engine, "_post_progress_report", fake_post)
    monkeypatch.setattr(engine, "_delegate_to_expert", lambda agent_id, task: f"{agent_id} says go")
    monkeypatch.setattr(
        "bigas.agents.proactive_engine.fetch_commits_for_projects",
        lambda **kw: {"by_project": {}, "stats": {}, "errors": []},
    )
    monkeypatch.setattr(
        "bigas.agents.proactive_engine.fetch_merged_pull_requests",
        lambda **kw: [],
    )
    monkeypatch.setattr(
        "bigas.agents.proactive_engine.fetch_marketing_snapshot",
        lambda **kw: "(skipped)",
    )

    created_keys = []

    def fake_create(**kwargs):
        created_keys.append(kwargs["summary"])
        return {"ok": True, "key": "BIG-30", "url": "https://x/browse/BIG-30"}

    monkeypatch.setattr(
        "bigas.agents.proactive_engine.CreateJiraIssueService.create",
        lambda self, **kw: fake_create(**kw),
    )

    result = engine.run(timeframe_days=7)
    row = result["results"][0]
    assert row["progress_report_posted"] is True
    assert posted[0][0] == "BIG-20"
    assert created_keys == ["Add monitoring alerts"]


def test_jira_client_epic_jql_clause(monkeypatch):
    monkeypatch.delenv("JIRA_EPIC_JQL_FIELD", raising=False)
    cfg = type("C", (), {"project_keys": ("BIG",), "base_url": "https://x", "email": "a", "api_token": "b"})()
    client = JiraClient(cfg)
    assert client.epic_jql_clause("BIG-1") == 'parent = "BIG-1"'

    monkeypatch.setenv("JIRA_EPIC_JQL_FIELD", "Epic Link")
    assert client.epic_jql_clause("BIG-1") == '"Epic Link" = "BIG-1"'


@pytest.fixture
def client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_evaluate_goals_requires_access_key_in_restricted_mode(client, monkeypatch):
    client.application.config["BIGAS_ACCESS_MODE"] = "restricted"
    client.application.config["BIGAS_ACCESS_KEYS"] = {"scheduler-key"}
    monkeypatch.setattr(
        "bigas.resources.chat.endpoints.run_evaluation_loop",
        lambda timeframe_days: {"ok": True, "results": []},
    )

    denied = client.post("/api/agents/evaluate-goals", json={"timeframe_days": 7})
    assert denied.status_code == 401

    ok = client.post(
        "/api/agents/evaluate-goals",
        json={"timeframe_days": 7},
        headers={"X-Bigas-Access-Key": "scheduler-key"},
    )
    assert ok.status_code == 200
    assert ok.get_json()["ok"] is True


def test_evaluate_goals_runs_synchronously(client, monkeypatch):
    client.application.config["BIGAS_ACCESS_MODE"] = "open"
    client.application.config["BIGAS_ACCESS_KEYS"] = {"scheduler-key"}
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.setattr(
        "bigas.resources.chat.endpoints.run_evaluation_loop",
        lambda timeframe_days: {"ok": True, "results": [], "timeframe_days": timeframe_days},
    )

    denied = client.post("/api/agents/evaluate-goals", json={"timeframe_days": 14})
    assert denied.status_code == 401

    resp = client.post(
        "/api/agents/evaluate-goals",
        json={"timeframe_days": 14},
        headers={"X-Bigas-Access-Key": "scheduler-key"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert resp.get_json()["timeframe_days"] == 14


def test_evaluate_goals_accepts_cron_secret_fallback(client, monkeypatch):
    client.application.config["BIGAS_ACCESS_MODE"] = "open"
    client.application.config["BIGAS_ACCESS_KEYS"] = set()
    monkeypatch.setenv("CRON_SECRET", "legacy-cron-secret")
    monkeypatch.setattr(
        "bigas.resources.chat.endpoints.run_evaluation_loop",
        lambda timeframe_days: {"ok": True, "results": []},
    )

    denied = client.post("/api/agents/evaluate-goals", json={"timeframe_days": 7})
    assert denied.status_code == 401

    ok = client.post(
        "/api/agents/evaluate-goals",
        json={"timeframe_days": 7},
        headers={"Authorization": "Bearer legacy-cron-secret"},
    )
    assert ok.status_code == 200
    assert ok.get_json()["ok"] is True


def test_evaluate_goals_requires_webhook_secret_configuration(client, monkeypatch):
    client.application.config["BIGAS_ACCESS_MODE"] = "open"
    client.application.config["BIGAS_ACCESS_KEYS"] = set()
    monkeypatch.delenv("CRON_SECRET", raising=False)

    resp = client.post("/api/agents/evaluate-goals", json={"timeframe_days": 7})
    assert resp.status_code == 503


def test_ticket_updated_at_accepts_datetime():
    from bigas.tickets.jira_adapter import _ticket_updated_at

    stamp = datetime(2026, 8, 23, 7, 0, tzinfo=timezone.utc)
    assert _ticket_updated_at({"updated_at": stamp}) == stamp
    assert _ticket_updated_at({"updated_at": "2026-08-23T07:00:00+00:00"}) == stamp
    assert _ticket_updated_at({"updated_at": None, "created_at": ""}) is None


def test_engine_reads_internal_epics_when_board_enabled(monkeypatch):
    monkeypatch.setenv("USE_INTERNAL_BOARD", "true")
    monkeypatch.setenv("CHAT_STORAGE_MODE", "memory")
    from bigas.tickets import store as ticket_store_module
    from bigas.tickets.jira_adapter import TicketJiraAdapter
    from bigas.tickets.store import get_ticket_store

    ticket_store_module._store = None
    store = get_ticket_store()
    board = store.create_board("user-1", name="BIG Board", project_key="BIG")
    epic = store.create_ticket(
        board["board_id"],
        title="Launch feature X",
        description="Ship it",
        status="Research and describe (AI)",
        issue_type="Epic",
    )
    store.create_ticket(
        board["board_id"],
        title="Existing discovery",
        description="Already filed",
        status="To Do",
        issue_type="Task",
        parent_key=epic["key"],
    )

    engine = ProactiveGoalEngine()
    assert isinstance(engine._jira, TicketJiraAdapter)
    engine._llm = FakeLLM(
        {
            "analysis": "Need user interviews",
            "tasks_to_create": [
                {
                    "summary": "Interview 5 users",
                    "description": "Run discovery calls",
                    "issue_type": "Task",
                }
            ],
        }
    )
    created = []

    def fake_create(**kwargs):
        created.append(kwargs)
        return {"ok": True, "key": "BIG-99", "url": "/board?ticket=BIG-99"}

    monkeypatch.setattr(
        "bigas.agents.proactive_engine.CreateJiraIssueService.create",
        lambda self, **kw: fake_create(**kw),
    )
    monkeypatch.setattr(engine, "_post_planning_update", lambda **kw: None)

    result = engine.run(timeframe_days=7)
    assert result["ok"] is True
    assert result["epics_found"] == 1
    assert result["results"][0]["epic_key"] == epic["key"]
    assert created[0]["parent_epic_key"] == epic["key"]
    ticket_store_module._store = None


def test_run_evaluation_loop_entrypoint(monkeypatch):
    class FakeEngine:
        def run(self, *, timeframe_days, epic_key=None, status_hint=None):
            return {"ok": True, "epics_found": 0, "results": [], "timeframe_days": timeframe_days}

    monkeypatch.setattr(
        "bigas.agents.proactive_engine.ProactiveGoalEngine",
        FakeEngine,
    )
    out = run_evaluation_loop(timeframe_days=3)
    assert out["ok"] is True
