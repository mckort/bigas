"""Unit tests for automated MCP QA agent (BIG-5)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from flask import Flask

from bigas.resources.cto.qa_agent.drafts import InMemoryQADraftStore
from bigas.resources.cto.qa_agent.endpoints import qa_proposals_bp
from bigas.resources.cto.qa_agent.service import (
    QAAgentService,
    _extract_json,
    format_cto_discord_message,
)
from bigas.resources.product.x_posts.signing import sign_draft_id, verify_draft_token
from bigas.utils.mcp_client import MCPClient


class _FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        if not self._responses:
            return "{}"
        return self._responses.pop(0)


def test_extract_json_from_markdown_fence():
    raw = '```json\n{"status": "excellent", "title": "ok"}\n```'
    assert _extract_json(raw)["status"] == "excellent"


def test_mcp_client_call_tool_parses_result(monkeypatch):
    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": '{"ok": true}'}],
                    "isError": False,
                    "structuredContent": {"ok": True},
                },
            }

    monkeypatch.setattr(
        "bigas.utils.mcp_client.requests.Session.post",
        lambda self, *a, **k: FakeResp(),
    )
    client = MCPClient("https://example.test", auth_token="secret")
    out = client.call_tool("ask_analytics_question", {"question": "hi"})
    assert out["is_error"] is False
    assert '"ok": true' in out["text"]
    assert out["structured"] == {"ok": True}


def test_qa_agent_routes_excellent_to_qa_channel_only(monkeypatch):
    store = InMemoryQADraftStore()
    service = QAAgentService(draft_store=store)
    service._llm = _FakeLLM(
        [
            json.dumps({"tools": [{"name": "tool_a", "arguments": {}, "rationale": "changed"}]}),
            json.dumps(
                {
                    "status": "excellent",
                    "title": "",
                    "proposal": "",
                    "summary": "Looks great",
                }
            ),
        ]
    )
    service._model = "test-model"

    mock_client = MagicMock()
    mock_client.list_tools.return_value = [{"name": "tool_a", "description": "demo"}]
    mock_client.call_tool.return_value = {"is_error": False, "text": "good output"}

    discord_calls = []

    def fake_post(webhook, message, **_kwargs):
        discord_calls.append((webhook, message))
        return True

    monkeypatch.setenv("DISCORD_WEBHOOK_URL_QA", "https://discord.test/qa")
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "signing-secret")
    monkeypatch.setattr("bigas.resources.cto.qa_agent.service.MCPClient", lambda *a, **k: mock_client)
    monkeypatch.setattr("bigas.resources.cto.qa_agent.service.post_to_discord", fake_post)

    result = service.run(
        diff="diff changes tool_a",
        mcp_endpoint_url="https://mcp.example.test",
    )

    assert result["summary"]["all_excellent"] is True
    assert len(discord_calls) == 1
    assert "QA run passed" in discord_calls[0][1]
    assert store.load("anything") is None


def test_qa_agent_routes_improvement_to_cto_with_proposal(monkeypatch):
    store = InMemoryQADraftStore()
    service = QAAgentService(draft_store=store)
    service._llm = _FakeLLM(
        [
            json.dumps({"tools": [{"name": "tool_b", "arguments": {"x": 1}}]}),
            json.dumps(
                {
                    "status": "improvement",
                    "title": "Improve competitor mapping",
                    "proposal": "## Brief\nTune mapping weights.",
                    "summary": "Output is shallow",
                }
            ),
        ]
    )
    service._model = "test-model"

    mock_client = MagicMock()
    mock_client.list_tools.return_value = [{"name": "tool_b"}]
    mock_client.call_tool.return_value = {"is_error": False, "text": "weak output"}

    posted = []

    def fake_post(webhook, message, **_kwargs):
        posted.append((webhook, message))
        return True

    monkeypatch.setenv("DISCORD_WEBHOOK_URL_CTO", "https://discord.test/cto")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_QA", "https://discord.test/qa")
    monkeypatch.setenv("SERVER_URL", "https://bigas.example.test")
    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "signing-secret")
    monkeypatch.setattr("bigas.resources.cto.qa_agent.service.MCPClient", lambda *a, **k: mock_client)
    monkeypatch.setattr("bigas.resources.cto.qa_agent.service.post_to_discord", fake_post)
    monkeypatch.setattr("bigas.resources.cto.qa_agent.service.post_long_to_discord", lambda *a, **k: None)

    result = service.run(diff="diff", mcp_endpoint_url="https://mcp.example.test")
    routing = result["results"][0]["routing"]
    assert routing["action"] == "discord_cto"
    assert routing["proposal_id"]
    assert any("improvement suggested" in msg for _, msg in posted)


def test_qa_agent_routes_new_feature_to_jira_pm(monkeypatch):
    store = InMemoryQADraftStore()
    service = QAAgentService(draft_store=store)
    service._llm = _FakeLLM(
        [
            json.dumps({"tools": [{"name": "tool_c", "arguments": {}}]}),
            json.dumps(
                {
                    "status": "new_feature",
                    "title": "Add competitor diff view",
                    "proposal": "Users need side-by-side competitor diffs.",
                    "summary": "Missing capability",
                }
            ),
        ]
    )
    service._model = "test-model"

    mock_client = MagicMock()
    mock_client.list_tools.return_value = [{"name": "tool_c"}]
    mock_client.call_tool.return_value = {"is_error": False, "text": "output"}

    monkeypatch.setenv("DISCORD_WEBHOOK_URL_PRODUCT", "https://discord.test/pm")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_QA", "https://discord.test/qa")
    monkeypatch.setenv("JIRA_PM_PROJECT_KEY", "BIG")
    monkeypatch.setattr("bigas.resources.cto.qa_agent.service.MCPClient", lambda *a, **k: mock_client)
    monkeypatch.setattr("bigas.resources.cto.qa_agent.service.post_to_discord", lambda *a, **k: True)
    monkeypatch.setattr("bigas.resources.cto.qa_agent.service.post_long_to_discord", lambda *a, **k: None)
    monkeypatch.setattr(
        "bigas.resources.cto.qa_agent.service.QAAgentService._create_jira_issue",
        lambda self, **kwargs: {"ok": True, "key": "BIG-99", "url": "https://jira/BIG-99"},
    )

    result = service.run(diff="diff", mcp_endpoint_url="https://mcp.example.test")
    routing = result["results"][0]["routing"]
    assert routing["action"] == "jira_pm"
    assert routing["issue_key"] == "BIG-99"


def _recent_created_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_approve_proposal_creates_cto_jira_issue(monkeypatch):
    store = InMemoryQADraftStore()
    proposal_id = "abc-123"
    store.save(
        proposal_id,
        {
            "id": proposal_id,
            "tool_name": "tool_x",
            "title": "Fix mapping",
            "proposal": "Do better",
            "summary": "Needs work",
            "created_at": _recent_created_at(),
            "status": "pending",
        },
    )
    service = QAAgentService(draft_store=store)

    created = {}

    def fake_create(self, **kwargs):
        created.update(kwargs)
        return {"ok": True, "key": "BIG-42", "url": "https://jira/BIG-42"}

    monkeypatch.setenv("JIRA_CTO_PROJECT_KEY", "BIG")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_CTO", "https://discord.test/cto")
    monkeypatch.setattr(
        "bigas.resources.cto.qa_agent.service.QAAgentService._create_jira_issue",
        fake_create,
    )
    monkeypatch.setattr("bigas.resources.cto.qa_agent.service.post_to_discord", lambda *a, **k: True)

    out = service.approve_proposal(proposal_id)
    assert out["issue_key"] == "BIG-42"
    assert created["project_key"] == "BIG"
    assert store.load(proposal_id) is None


def test_qa_proposal_html_approve_flow(monkeypatch):
    store = InMemoryQADraftStore()
    proposal_id = "prop-1"
    store.save(
        proposal_id,
        {
            "id": proposal_id,
            "tool_name": "demo_tool",
            "title": "Improve demo",
            "proposal": "Fix it",
            "summary": "Not excellent",
            "created_at": _recent_created_at(),
            "status": "pending",
        },
    )

    monkeypatch.setenv("JIRA_AUTOMATION_WEBHOOK_SECRET", "secret")
    token = sign_draft_id(proposal_id, secret="secret")
    assert verify_draft_token(proposal_id, token)

    app = Flask(__name__)
    app.register_blueprint(qa_proposals_bp)
    service = QAAgentService(draft_store=store)

    monkeypatch.setattr("bigas.resources.cto.qa_agent.endpoints._service", lambda: service)
    monkeypatch.setattr(
        service,
        "approve_proposal",
        lambda pid: {"ok": True, "issue_key": "BIG-1", "issue_url": "https://jira/BIG-1"},
    )

    client = app.test_client()
    resp = client.get(f"/api/qa-proposals/{proposal_id}?token={token}")
    assert resp.status_code == 200
    assert b"Approve" in resp.data

    approve = client.post(
        f"/api/qa-proposals/{proposal_id}/approve",
        data={"token": token},
    )
    assert approve.status_code == 200
    assert b"Approved" in approve.data


def test_format_cto_discord_message_includes_review_link():
    msg = format_cto_discord_message(
        {"tool_name": "t", "title": "Title", "summary": "Summary"},
        review_url="https://bigas.test/api/qa-proposals/x?token=abc",
    )
    assert "Approve or Decline" in msg
    assert "Title" in msg
