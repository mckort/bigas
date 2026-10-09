"""CFO bookkeeping reads stay on the allowlist and never book."""
from __future__ import annotations

import json

import pytest

from bigas.utils.accounted_client import (
    AccountedError,
    NOT_CONFIGURED,
    WRITE_REFUSED,
    clear_live_tool_cache,
    cfo_accounted_tools,
    dispatch_accounted_tool,
)


@pytest.fixture(autouse=True)
def _no_live_cache(monkeypatch):
    monkeypatch.delenv("ACCOUNTED_API_KEY", raising=False)
    monkeypatch.delenv("ACCOUNTED_COMPANY", raising=False)
    monkeypatch.delenv("ACCOUNTED_MCP_URL", raising=False)
    clear_live_tool_cache()
    yield
    clear_live_tool_cache()


def test_write_tool_is_refused_without_calling_accounted(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("Accounted should not be called")

    monkeypatch.setattr("bigas.utils.accounted_client._post_json", boom)
    monkeypatch.setenv("ACCOUNTED_API_KEY", "gnubok_sk_test_example")
    out = dispatch_accounted_tool("accounted_create_voucher", {"amount": 1})
    assert out == WRITE_REFUSED


def test_read_without_key_does_not_call_accounted(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("Accounted should not be called")

    monkeypatch.setattr("bigas.utils.accounted_client._post_json", boom)
    out = dispatch_accounted_tool("accounted_get_vat_report", {})
    assert out == NOT_CONFIGURED


def test_read_tool_returns_text(monkeypatch):
    monkeypatch.setenv("ACCOUNTED_API_KEY", "gnubok_sk_test_example")
    seen = {}

    def fake_post(url, headers, payload, timeout):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        seen["method"] = payload.get("method")
        seen["name"] = (payload.get("params") or {}).get("name")
        body = {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": {
                "content": [{"type": "text", "text": "Ruta 49: 1200"}],
                "isError": False,
            },
        }
        return 200, json.dumps(body)

    monkeypatch.setattr("bigas.utils.accounted_client._post_json", fake_post)
    out = dispatch_accounted_tool("accounted_get_vat_report", {"year": 2026})
    assert out == "Ruta 49: 1200"
    assert seen["method"] == "tools/call"
    assert seen["name"] == "accounted_get_vat_report"
    assert seen["auth"] == "Bearer gnubok_sk_test_example"
    assert "tool_namespace=accounted" in seen["url"]
    assert "gnubok_sk" not in seen["url"]


def test_tools_list_failure_is_negative_cached(monkeypatch):
    monkeypatch.setenv("ACCOUNTED_API_KEY", "gnubok_sk_test_example")
    calls = {"n": 0}

    def fail_list(_url, _headers, payload, timeout):
        if payload["method"] == "tools/list":
            calls["n"] += 1
            assert timeout == 10
            raise AccountedError("Accounted unreachable")
        raise AssertionError("unexpected RPC")

    monkeypatch.setattr("bigas.utils.accounted_client._post_json", fail_list)
    first = cfo_accounted_tools()
    second = cfo_accounted_tools()
    assert calls["n"] == 1
    assert [tool["name"] for tool in first] == [tool["name"] for tool in second]


def test_tools_list_keeps_only_the_read_allowlist(monkeypatch):
    monkeypatch.setenv("ACCOUNTED_API_KEY", "gnubok_sk_test_example")

    def fake_post(_url, _headers, payload, timeout):
        assert payload["method"] == "tools/list"
        body = {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": {
                "tools": [
                    {
                        "name": "accounted_get_vat_report",
                        "description": "Live VAT schema",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"year": {"type": "integer"}},
                            "required": ["year"],
                        },
                    },
                    {
                        "name": "accounted_create_voucher",
                        "description": "Book a voucher",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            },
        }
        return 200, json.dumps(body)

    monkeypatch.setattr("bigas.utils.accounted_client._post_json", fake_post)
    names = [tool["name"] for tool in cfo_accounted_tools()]
    assert "accounted_create_voucher" not in names
    assert "accounted_get_vat_report" in names
    vat = next(tool for tool in cfo_accounted_tools() if tool["name"] == "accounted_get_vat_report")
    assert vat["description"] == "Live VAT schema"
    assert vat["parameters"]["required"] == ["year"]


def test_cfo_lists_accounted_tools_and_other_agents_do_not(monkeypatch):
    from bigas.agents.chief_of_staff import _execute_listed_tool, _with_accounted_tools

    def boom(*_args, **_kwargs):
        raise AssertionError("Accounted should not be called")

    monkeypatch.setattr("bigas.utils.accounted_client._post_json", boom)
    base = [{"name": "fetch_ai_usage", "description": "AI spend", "parameters": {}}]
    cfo_names = [tool["name"] for tool in _with_accounted_tools("cfo", base)]
    cto_names = [tool["name"] for tool in _with_accounted_tools("cto", base)]
    assert "accounted_get_income_statement" in cfo_names
    assert "fetch_ai_usage" in cfo_names
    assert cto_names == ["fetch_ai_usage"]

    class FakeClient:
        def call_tool(self, name, arguments):
            raise AssertionError(f"Bigas MCP should not run {name}")

    refused = _execute_listed_tool(
        FakeClient(),
        "accounted_create_voucher",
        {},
        agent_id="cfo",
        user_message="book this",
    )
    assert refused == WRITE_REFUSED
    missing = _execute_listed_tool(
        FakeClient(),
        "accounted_get_balance_sheet",
        {},
        agent_id="cfo",
        user_message="balance",
    )
    assert missing == NOT_CONFIGURED
    handed_off = _execute_listed_tool(
        FakeClient(),
        "accounted_get_vat_report",
        {},
        agent_id="chief",
        user_message="moms",
    )
    assert "CFO" in handed_off


def test_supplier_question_prefetches_invoices_and_journal(monkeypatch):
    from bigas.utils.accounted_client import bookkeeping_prefetch, supplier_name_from_question

    assert supplier_name_from_question("hur mycket betalar jag för speedledger varje år?") == "speedledger"
    assert bookkeeping_prefetch("what is the weather") == ""

    monkeypatch.setenv("ACCOUNTED_API_KEY", "gnubok_sk_test_example")
    calls = []

    def fake_post(_url, _headers, payload, timeout):
        calls.append(payload)
        body = {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": {"content": [{"type": "text", "text": "12 000 kr"}], "isError": False},
        }
        return 200, json.dumps(body)

    monkeypatch.setattr("bigas.utils.accounted_client._post_json", fake_post)
    text = bookkeeping_prefetch("hur mycket betalar jag för speedledger per år?")
    names = [item["params"]["name"] for item in calls]
    assert names == ["accounted_list_supplier_invoices", "accounted_query_journal"]
    assert calls[0]["params"]["arguments"]["supplier_name"] == "speedledger"
    assert "12 000 kr" in text
    assert "public" not in text.lower()


def test_supplier_prefetch_without_key_does_not_call_accounted(monkeypatch):
    from bigas.utils.accounted_client import NOT_CONFIGURED, bookkeeping_prefetch

    def boom(*_args, **_kwargs):
        raise AssertionError("Accounted should not be called")

    monkeypatch.setattr("bigas.utils.accounted_client._post_json", boom)
    assert bookkeeping_prefetch("hur mycket betalar jag för speedledger per år?") == NOT_CONFIGURED


def test_cfo_playbook_mentions_books_and_ai_spend():
    from bigas.agents.chief_of_staff import _specialist_native_extra

    cfo = _specialist_native_extra("cfo")
    assert "Numbers first" in cfo
    assert "fetch_ai_usage" in cfo
    assert "accounted_get_vat_report" in cfo
    assert "preliminary" in cfo
    cto = _specialist_native_extra("cto")
    assert "accounted_" not in cto
