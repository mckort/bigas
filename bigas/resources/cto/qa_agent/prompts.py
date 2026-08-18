"""Prompts for the automated MCP QA agent."""
from __future__ import annotations

import json
from typing import Any, Dict, List

PLAN_SYSTEM_PROMPT = """You are a senior QA engineer for AI-powered backend services.
Given a git diff and a list of MCP tools, decide which tools are most likely affected by the code changes and should be exercised.

Return ONLY valid JSON with this shape:
{
  "tools": [
    {
      "name": "tool_name_from_manifest",
      "arguments": { "param": "value" },
      "rationale": "one sentence why this tool is relevant"
    }
  ]
}

Rules:
- Pick at most 5 tools. Prefer tools whose implementation or behavior clearly changed in the diff.
- Skip tools that are unrelated, destructive, or extremely slow (weekly reports, autofix, PR review).
- Use realistic argument values. Prefer read-only or low-impact operations when possible.
- If the diff does not map to any MCP tool, return {"tools": []}.
"""

EVALUATE_SYSTEM_PROMPT = """You are a senior QA engineer evaluating MCP tool output quality.
Judge whether the output is excellent for its purpose — not merely free of errors.

Return ONLY valid JSON with this shape:
{
  "status": "excellent" | "improvement" | "new_feature",
  "title": "short headline for a Jira issue (when not excellent)",
  "proposal": "markdown research/design proposal (when not excellent; empty when excellent)",
  "summary": "one sentence explaining the verdict"
}

Guidelines:
- "excellent": output is accurate, complete, actionable, and well structured for the tool's purpose.
- "improvement": the tool works but output quality, coverage, clarity, or correctness could be materially better. Include concrete fix suggestions and acceptance criteria in proposal.
- "new_feature": the diff or output reveals a gap that needs a new capability, not just tuning the existing tool. Proposal should describe the feature at a product level.
- Treat runtime errors, empty responses, or obvious hallucinations as "improvement" (not excellent).
- Keep proposal concise but actionable (Brief, findings, suggested approach, test plan).
"""


def build_plan_user_prompt(*, diff: str, tools: List[Dict[str, Any]], pr_url: str = "") -> str:
    slim_tools: List[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        slim_tools.append(
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "parameters": tool.get("parameters"),
            }
        )
    parts = [
        "## Git diff",
        (diff or "").strip()[:120_000] or "(empty diff)",
        "",
        "## Available MCP tools",
        json.dumps(slim_tools, indent=2),
    ]
    if (pr_url or "").strip():
        parts.extend(["", f"## PR context\n{pr_url.strip()}"])
    return "\n".join(parts)


def build_evaluate_user_prompt(
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    diff: str,
    tool_result: Dict[str, Any],
    pr_url: str = "",
) -> str:
    result_preview = json.dumps(tool_result, indent=2, ensure_ascii=False)
    if len(result_preview) > 80_000:
        result_preview = result_preview[:80_000] + "\n...(truncated)"
    parts = [
        f"## Tool\n{tool_name}",
        "",
        "## Arguments",
        json.dumps(arguments or {}, indent=2),
        "",
        "## Relevant diff excerpt",
        (diff or "").strip()[:40_000] or "(empty)",
        "",
        "## Tool output",
        result_preview,
    ]
    if (pr_url or "").strip():
        parts.extend(["", f"## PR\n{pr_url.strip()}"])
    return "\n".join(parts)
