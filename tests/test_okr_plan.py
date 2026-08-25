"""OKR plan: concrete work items, no KR clones, no GA4 wiring tickets."""

from __future__ import annotations

import json

from bigas.okr.plan import (
    OKR_PLAN_SYSTEM,
    apply_current_updates,
    heuristic_ga4_currents,
    is_mechanical_okr_task,
    run_okr_plan,
)


class _FakeLlm:
    def __init__(self, payload, captured=None):
        self._payload = payload
        self.captured = captured if captured is not None else {}

    def complete(self, messages, **kwargs):
        self.captured["messages"] = messages
        self.captured["kwargs"] = kwargs
        return self._payload


def test_plan_prompt_forbids_kr_clones_and_wiring():
    text = OKR_PLAN_SYSTEM.lower()
    assert "turn a kr into a ticket" in text
    assert "wire weekly" in text
    assert "do not invent currents" in text
    assert "ai_doable" in text


def test_mechanical_task_detection():
    krs = [{"id": "kr-abc", "title": "Increase website sessions from 43 to 1000"}]
    assert is_mechanical_okr_task(
        {"title": "Increase website sessions from 43 to 1000"}, krs
    )
    assert is_mechanical_okr_task({"title": "Wire weekly snapshot for sessions"}, krs)
    assert is_mechanical_okr_task({"title": "Instrument: form submissions"}, krs)
    assert not is_mechanical_okr_task(
        {"title": "Publish a B2B landing page for merch kits"}, krs
    )


def test_heuristic_ga4_currents_from_traffic_and_pages():
    krs = [
        {"id": "kr-sess", "metric": "website sessions", "title": "Increase sessions", "source": "ga4"},
        {
            "id": "kr-store",
            "metric": "/store pageviews",
            "title": "Increase /store pageviews from 27 to 300",
            "source": "ga4",
        },
        {"id": "kr-manual", "metric": "wholesale orders", "title": "Orders", "source": "manual"},
    ]
    ga4 = (
        "Traffic: sessions 80 (prev 43), users 20 (prev 5), pageviews 120 (prev 27).\n"
        "Top pages:\n"
        "  - pagePath=/store, screenPageViews=40, sessions=12\n"
    )
    updates = {item["id"]: item["current"] for item in heuristic_ga4_currents(krs, ga4)}
    assert updates["kr-sess"] == 80
    assert updates["kr-store"] == 40
    assert "kr-manual" not in updates


def test_apply_current_updates_overwrites_measurable_only():
    krs = [
        {"id": "kr-a", "measurable": True, "current": 1},
        {"id": "kr-b", "measurable": False, "current": 0},
    ]
    apply_current_updates(krs, [{"id": "kr-a", "current": 9}, {"id": "kr-b", "current": 3}])
    assert krs[0]["current"] == 9
    assert krs[1]["current"] == 0


def test_run_okr_plan_drops_clones_and_wiring(monkeypatch):
    captured = {}
    payload = json.dumps(
        {
            "briefing": "Work toward traffic and meetings.",
            "plan_markdown": "Sessions are 43. Open a landing page and a meeting CTA.",
            "key_result_updates": [{"id": "kr-sess", "current": 43}],
            "tasks_to_create": [
                {
                    "title": "Increase website sessions from 43 to 1000",
                    "description": "Clone — should be dropped.",
                    "kr_id": "kr-sess",
                    "ai_doable": False,
                },
                {
                    "title": "Wire weekly snapshot for sessions",
                    "description": "Wiring — should be dropped.",
                    "kr_id": "kr-sess",
                    "ai_doable": True,
                },
                {
                    "title": "Publish a wholesale kit landing page on /store",
                    "description": "Give B2B buyers a page that can convert sessions into inquiries.",
                    "kr_id": "kr-sess",
                    "ai_doable": True,
                },
            ],
        }
    )
    ticket = {
        "title": "10 paying customers before the end of the year",
        "key": "GPWW-17",
        "key_results": [
            {
                "id": "kr-sess",
                "title": "Increase website sessions from 43 to 1000",
                "metric": "sessions",
                "source": "ga4",
                "measurable": True,
                "baseline": 43,
                "target": 1000,
                "current": 43,
            }
        ],
    }
    result = run_okr_plan(
        ticket,
        key_results=ticket["key_results"],
        existing_tasks=[],
        evidence={"brand": "Green Promo Wear", "ga4": "(unused)"},
        llm=_FakeLlm(payload, captured),
        model="test-model",
    )
    titles = [t["title"] for t in result.tasks]
    assert titles == ["Publish a wholesale kit landing page on /store"]
    assert result.tasks[0]["kr_id"] == "kr-sess"
    assert result.tasks[0]["ai_doable"] is True
    assert result.used_llm
    prompt = captured["messages"][0]["content"].lower()
    assert "turn a kr into a ticket" in prompt
