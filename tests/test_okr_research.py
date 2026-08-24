"""OKR research: grounded KRs from evidence + thinking model, no SaaS templates."""

from __future__ import annotations

import json

from bigas.okr.context import format_evidence_pack, resolve_project_key
from bigas.okr.research import (
    OKR_RESEARCH_SYSTEM,
    fallback_key_results,
    run_okr_research,
)
from bigas.portfolio import brand_name, site_urls_for_project


class _FakeLlm:
    def __init__(self, payload, captured=None):
        self._payload = payload
        self.captured = captured if captured is not None else {}

    def complete(self, messages, **kwargs):
        self.captured["messages"] = messages
        self.captured["kwargs"] = kwargs
        return self._payload


def test_gpww_brand_mapping():
    assert brand_name("GPWW") == "Green Promo Wear"
    assert any("greenpromowear.com" in url for url in site_urls_for_project("GPWW"))
    assert resolve_project_key({"key": "GPWW-15", "project_key": ""}) == "GPWW"


def test_prompt_forbids_canned_metrics_and_invented_numbers():
    text = OKR_RESEARCH_SYSTEM.lower()
    assert "do not use canned metrics" in text or "canned metrics" in text
    assert "not in the evidence" in text or "from the evidence" in text
    assert "sibling" in text
    assert "from <baseline> to <target>" in text or "from {baseline} to {target}" in text
    assert "1200" not in text
    assert "sample30" not in text
    assert "20 sample" not in text
    assert "weekly active founders" not in text


def test_fallback_is_not_saas_template():
    krs = fallback_key_results(
        {"title": "10 paying customers"},
        {"brand": "Green Promo Wear"},
    )
    blob = " ".join(
        f"{kr.get('title')} {kr.get('metric')} {kr.get('ai_note')}" for kr in krs
    ).lower()
    assert "weekly active founders" not in blob
    assert "7-day" not in blob
    assert "nps" not in blob
    assert all(not kr.get("measurable") for kr in krs)
    assert any("green promo wear" in (kr.get("measurement_gap") or "").lower() for kr in krs)


def test_run_okr_research_uses_thinking_and_replaces_placeholders():
    captured = {}
    payload = json.dumps(
        {
            "briefing": "Improvements on the path to 10 paying customers.",
            "research_markdown": "Used the wholesale catalog and the SAMPLE30 path.",
            "key_results": [
                {
                    "title": "Raise SAMPLE30 sample-to-paid conversion from 5% to 15%",
                    "metric": "Sample-to-paid conversion",
                    "unit": "%",
                    "baseline": 5,
                    "target": 15,
                    "current": 5,
                    "source": "manual",
                    "measurable": True,
                    "ai_note": "If more samples become paid orders, 10 paying customers is more likely. Code SAMPLE30 is on-site.",
                },
                {
                    "title": "Raise demo-to-paid close rate from 20% to 35%",
                    "metric": "Demo close rate",
                    "unit": "%",
                    "baseline": 20,
                    "target": 35,
                    "current": 20,
                    "source": "manual",
                    "measurable": True,
                    "ai_note": "Book demo is a primary CTA; a higher close rate yields more of the 10 customers from the same demos.",
                },
                {
                    "title": "Cut days from first inquiry to first paid order from 45 to 21",
                    "metric": "Days to first paid order",
                    "unit": "days",
                    "baseline": 45,
                    "target": 21,
                    "current": 45,
                    "source": "manual",
                    "measurable": False,
                    "measurement_gap": "No CRM cycle-time report yet.",
                },
            ],
        }
    )
    evidence = {
        "project_key": "GPWW",
        "brand": "Green Promo Wear",
        "site_urls": "https://greenpromowear.com",
        "repo": "Green-Promo-Wear-Global/greenpromowear-website",
        "cycle_label": "2026-Q3",
        "cycle_start": "2026-07-01",
        "cycle_end": "2026-09-30",
        "days_remaining": "37",
        "days_total": "92",
        "title": "10 paying customers",
        "brief": "Need 10 paying customers before end of 2026.",
        "ga4": "sessions=12000, ecommercePurchases unavailable",
        "website": "Green Promo Wear sustainable promotional apparel",
        "web_snippets": "",
        "repo_context": "Next.js storefront",
        "board_tickets": "- GPWW-1 [Task/To Do]: Update catalog",
    }
    result = run_okr_research(
        {
            "title": "10 paying customers",
            "key": "GPWW-15",
            "project_key": "GPWW",
            "key_results": [
                {
                    "id": "kr-template01",
                    "title": "40 weekly active founders",
                    "status": "proposed",
                    "measurable": True,
                    "baseline": 12,
                    "target": 40,
                }
            ],
        },
        evidence=evidence,
        llm=_FakeLlm(payload, captured),
        model="gemini-3.1-pro-preview",
    )
    assert result.used_llm
    assert captured["kwargs"].get("thinking_budget") == 8192
    prompt = captured["messages"][1]["content"]
    assert "Green Promo Wear" in prompt
    assert "weekly active founders" in prompt
    titles = [kr["title"] for kr in result.key_results]
    assert "40 weekly active founders" not in titles
    assert any("conversion" in t.lower() or "close rate" in t.lower() for t in titles)
    assert "Used the wholesale catalog" in result.research_markdown


def test_llm_failure_does_not_keep_saas_placeholders():
    result = run_okr_research(
        {
            "title": "10 paying customers",
            "project_key": "GPWW",
            "key_results": [
                {
                    "title": "40 weekly active founders",
                    "status": "proposed",
                    "measurable": True,
                    "baseline": 12,
                    "target": 40,
                }
            ],
        },
        evidence={"brand": "Green Promo Wear", "title": "10 paying customers"},
        llm=_FakeLlm("not-json"),
        model="gemini-3.1-pro-preview",
    )
    assert not result.used_llm
    titles = " ".join(kr["title"] for kr in result.key_results).lower()
    assert "weekly active founders" not in titles
    assert all(not kr.get("measurable") for kr in result.key_results)


def test_committed_krs_are_kept():
    payload = json.dumps(
        {
            "briefing": "ok",
            "research_markdown": "ok",
            "key_results": [
                {
                    "title": "New leading KR",
                    "metric": "Leads",
                    "baseline": 0,
                    "target": 5,
                    "current": 0,
                    "source": "manual",
                    "measurable": True,
                }
            ],
        }
    )
    result = run_okr_research(
        {
            "title": "Grow GPWW",
            "key_results": [
                {
                    "id": "kr-keepme01",
                    "title": "10 paying customers",
                    "status": "committed",
                    "measurable": True,
                    "baseline": 0,
                    "target": 10,
                    "current": 0,
                }
            ],
        },
        evidence={"brand": "Green Promo Wear"},
        llm=_FakeLlm(payload),
        model="gpt-4.1",
    )
    titles = [kr["title"] for kr in result.key_results]
    assert "10 paying customers" in titles
    assert "New leading KR" in titles


def test_format_evidence_pack_includes_brand():
    text = format_evidence_pack(
        {
            "project_key": "GPWW",
            "brand": "Green Promo Wear",
            "site_urls": "https://greenpromowear.com",
            "repo": "x/y",
            "cycle_label": "90-day",
            "cycle_start": "2026-08-01",
            "cycle_end": "2026-10-30",
            "days_remaining": "60",
            "days_total": "90",
            "title": "10 paying customers",
            "brief": "Win 10 customers",
            "ga4": "sessions=1",
            "website": "catalog",
            "web_snippets": "",
            "repo_context": "",
            "board_tickets": "",
        }
    )
    assert "Green Promo Wear" in text
    assert "GPWW" in text
