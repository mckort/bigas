"""OKR prototype: Objectives, Key Results, dashboard, and pipeline."""

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

from flask import Flask

from bigas.okr.engine import handle_objective_status_change, propose_key_results
from bigas.okr.model import is_objective, kr_health, kr_progress, promote_objective_type
from bigas.resources.tickets.endpoints import tickets_bp
from bigas.tickets import store as ticket_store_module
from bigas.tickets.attachments import reset_attachment_blob_store_for_tests, set_image_describer
from bigas.tickets.store import get_ticket_store

_JIRA_ENV_KEYS = (
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_PROJECT_KEY",
    "JIRA_PROJECT_KEYS",
    "USE_INTERNAL_BOARD",
)


@pytest.fixture(autouse=True)
def _force_internal_board(monkeypatch):
    for key in _JIRA_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    ticket_store_module._store = None
    reset_attachment_blob_store_for_tests()
    set_image_describer(None)
    yield
    ticket_store_module._store = None
    reset_attachment_blob_store_for_tests()
    set_image_describer(None)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(tickets_bp)
    with app.test_client() as c:
        yield c


def _auth_headers():
    return {"Authorization": "Bearer test-dev-token", "Content-Type": "application/json"}


def _project_board(client):
    boards = client.get("/api/boards", headers=_auth_headers()).get_json()["boards"]
    return next(b for b in boards if b.get("project_key"))


def test_label_objective_promotes_issue_type():
    assert promote_objective_type("Task", ["objective"]) == "Objective"
    assert promote_objective_type("Epic", []) == "Epic"
    assert promote_objective_type("Epic", ["objective"]) == "Epic"
    assert promote_objective_type("Task", ["marketing"]) == "Task"
    assert not is_objective({"issue_type": "Epic"})
    assert is_objective({"issue_type": "Objective"})


def test_kr_progress_and_health():
    kr = {
        "measurable": True,
        "baseline": 0,
        "target": 100,
        "current": 40,
        "direction": "increase",
    }
    assert kr_progress(kr) == pytest.approx(0.4)
    assert kr_health(kr, expected=0.5) == "at_risk"
    assert kr_health({**kr, "current": 80}, expected=0.5) == "on_track"
    assert kr_health({"measurable": False}, expected=0.5) == "unmeasured"


def test_create_objective_and_link_task_to_kr(client):
    board = _project_board(client)
    created = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps(
            {
                "title": "Grow weekly founders",
                "issue_type": "Task",
                "labels": ["objective"],
                "key_results": [
                    {
                        "id": "kr-abcd1234",
                        "title": "40 WAF",
                        "baseline": 10,
                        "target": 40,
                        "current": 12,
                        "source": "ga4",
                        "measurable": True,
                    }
                ],
            }
        ),
    )
    assert created.status_code == 201
    objective = created.get_json()["ticket"]
    assert objective["issue_type"] == "Objective"
    assert is_objective(objective)
    assert objective["key_results"][0]["id"] == "kr-abcd1234"

    task = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps(
            {
                "title": "Instrument WAF event",
                "parent_key": objective["key"],
                "parent_kr_id": "kr-abcd1234",
            }
        ),
    )
    assert task.status_code == 201
    body = task.get_json()["ticket"]
    assert body["parent_key"] == objective["key"]
    assert body["parent_kr_id"] == "kr-abcd1234"


def test_research_proposes_key_results(client):
    board = _project_board(client)
    created = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Acquire more customers", "issue_type": "Objective"}),
    )
    ticket = created.get_json()["ticket"]
    moved = client.put(
        f"/api/tickets/{ticket['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"status": "Research and describe (AI)"}),
    )
    assert moved.status_code == 200
    store = get_ticket_store()
    live = store.get_ticket(ticket["ticket_id"])
    assert len(live.get("key_results") or []) >= 3
    comments = live.get("comments") or []
    assert any("OKR research" in (c.get("body") or "") for c in comments)


def test_design_creates_tasks_linked_to_krs(client):
    board = _project_board(client)
    created = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Reliable AI delivery", "issue_type": "Objective"}),
    )
    ticket = created.get_json()["ticket"]
    client.put(
        f"/api/tickets/{ticket['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"status": "Research and describe (AI)"}),
    )
    planned = client.put(
        f"/api/tickets/{ticket['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"status": "Design and plan (AI)"}),
    )
    assert planned.status_code == 200
    children = [
        t
        for t in client.get(
            f"/api/boards/{board['board_id']}/tickets", headers=_auth_headers()
        ).get_json()["tickets"]
        if t.get("parent_key") == ticket["key"]
    ]
    assert children
    assert all(c.get("parent_kr_id") for c in children)


def test_demo_seed_and_dashboard(client):
    seeded = client.post("/api/objectives/demo", headers=_auth_headers(), data=json.dumps({}))
    assert seeded.status_code == 201
    dashboard = seeded.get_json()["dashboard"]
    assert dashboard["stats"]["objectives"] >= 3
    assert dashboard["briefing"]["headline"]
    assert any(obj.get("key_results") for obj in dashboard["objectives"])
    assert dashboard.get("boards")
    assert len(dashboard["boards"]) >= 2
    assert any(board.get("objectives") for board in dashboard["boards"])
    listed = client.get("/api/objectives", headers=_auth_headers())
    assert listed.status_code == 200
    assert listed.get_json()["stats"]["objectives"] >= 3


def test_in_progress_does_not_auto_start_tasks(client):
    board = _project_board(client)
    created = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Acquire more customers", "issue_type": "Objective"}),
    )
    ticket = created.get_json()["ticket"]
    client.put(
        f"/api/tickets/{ticket['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"status": "Research and describe (AI)"}),
    )
    client.put(
        f"/api/tickets/{ticket['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"status": "Design and plan (AI)"}),
    )
    client.put(
        f"/api/tickets/{ticket['ticket_id']}",
        headers=_auth_headers(),
        data=json.dumps({"status": "In Progress (AI)"}),
    )
    children = [
        t
        for t in client.get(
            f"/api/boards/{board['board_id']}/tickets", headers=_auth_headers()
        ).get_json()["tickets"]
        if t.get("parent_key") == ticket["key"]
    ]
    assert children
    assert all(c.get("status") == "To Do" for c in children)


def test_epic_stays_epic_on_create(client):
    board = _project_board(client)
    created = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Legacy delivery epic", "issue_type": "Epic"}),
    )
    assert created.status_code == 201
    ticket = created.get_json()["ticket"]
    assert ticket["issue_type"] == "Epic"
    assert not is_objective(ticket)


def test_propose_key_results_without_llm():
    krs = propose_key_results({"title": "Grow the product", "key_results": []})
    assert 3 <= len(krs) <= 5
    assert any(kr.get("measurement_gap") or kr.get("measurable") for kr in krs)


def test_handle_status_skips_plain_tasks():
    result = handle_objective_status_change(
        {"issue_type": "Task", "title": "Fix bug"},
        to_status="Research and describe (AI)",
    )
    assert result.get("skipped")


def test_delete_objective_unlinks_children_by_default(client):
    board = _project_board(client)
    created = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps(
            {
                "title": "Grow weekly founders",
                "issue_type": "Objective",
                "key_results": [
                    {
                        "id": "kr-abcd1234",
                        "title": "40 WAF",
                        "baseline": 10,
                        "target": 40,
                        "current": 12,
                        "source": "ga4",
                        "measurable": True,
                    }
                ],
            }
        ),
    )
    objective = created.get_json()["ticket"]
    task = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps(
            {
                "title": "Instrument WAF event",
                "parent_key": objective["key"],
                "parent_kr_id": "kr-abcd1234",
            }
        ),
    ).get_json()["ticket"]
    deleted = client.delete(
        f"/api/tickets/{objective['ticket_id']}",
        headers=_auth_headers(),
    )
    assert deleted.status_code == 200
    live = client.get(
        f"/api/tickets/{task['ticket_id']}", headers=_auth_headers()
    ).get_json()["ticket"]
    assert not live.get("parent_key")
    assert not live.get("parent_kr_id")


def test_delete_objective_can_remove_children(client):
    board = _project_board(client)
    created = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Grow weekly founders", "issue_type": "Objective"}),
    )
    objective = created.get_json()["ticket"]
    task = client.post(
        f"/api/boards/{board['board_id']}/tickets",
        headers=_auth_headers(),
        data=json.dumps({"title": "Child task", "parent_key": objective["key"]}),
    ).get_json()["ticket"]
    deleted = client.delete(
        f"/api/tickets/{objective['ticket_id']}?delete_children=true",
        headers=_auth_headers(),
    )
    assert deleted.status_code == 200
    gone = client.get(f"/api/tickets/{task['ticket_id']}", headers=_auth_headers())
    assert gone.status_code == 404
