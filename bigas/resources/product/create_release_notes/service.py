from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import os
import re
import logging

import openai

from bigas.resources.product.create_release_notes.jira_client import JiraClient, JiraConfig, JiraError
from bigas.resources.product.create_release_notes.prompts import (
    RELEASE_NOTES_SYSTEM_PROMPT,
    build_release_notes_user_prompt,
    CUSTOMER_COPY_SYSTEM_PROMPT,
    build_customer_copy_user_prompt,
)
from bigas.resources.product.create_release_notes.formatter import group_issues, render_customer_markdown

logger = logging.getLogger(__name__)


class ReleaseNotesError(RuntimeError):
    pass


def _extract_json(text: str) -> Dict[str, Any]:
    """
    Best-effort extraction of JSON object from an LLM response.
    """
    t = (text or "").strip()
    if not t:
        raise ReleaseNotesError("Empty LLM response.")

    # Strip code fences if present.
    if "```" in t:
        # Prefer json-fenced block; otherwise first fenced block
        if "```json" in t:
            t = t.split("```json", 1)[1].split("```", 1)[0].strip()
        else:
            t = t.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        return json.loads(t)
    except Exception as e:
        raise ReleaseNotesError(f"LLM returned invalid JSON: {e}")


def _validate_fix_version(fix_version: str) -> None:
    if not fix_version or not isinstance(fix_version, str):
        raise ReleaseNotesError("fix_version is required.")
    if len(fix_version) > 50:
        raise ReleaseNotesError("fix_version too long (max 50 chars).")
    # Semver-ish or common release tags (e.g., 1.1.0, v1.1.0, 2026.02, 1.1.0-rc1)
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._\-+]{0,49}$", fix_version):
        raise ReleaseNotesError("Invalid fix_version format.")


def _normalize_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    fields = issue.get("fields", {}) or {}

    issue_type = (fields.get("issuetype") or {}).get("name") or ""
    priority = (fields.get("priority") or {}).get("name") or ""
    components = [c.get("name", "") for c in (fields.get("components") or []) if isinstance(c, dict)]
    labels = fields.get("labels") or []

    return {
        "key": issue.get("key", ""),
        "summary": fields.get("summary", ""),
        "issue_type": issue_type,
        "priority": priority,
        "components": [c for c in components if c],
        "labels": [l for l in labels if isinstance(l, str)],
        # Keep a few more for internal grouping context, not returned in issues_included:
        "_status": (fields.get("status") or {}).get("name") or "",
        "_resolutiondate": fields.get("resolutiondate") or None,
    }


class CreateReleaseNotesService:
    def __init__(
        self,
        *,
        jira_client: Optional[JiraClient] = None,
        openai_api_key: Optional[str] = None,
        openai_model: Optional[str] = None,
    ):
        if jira_client is None:
            jira_client = JiraClient(JiraConfig.from_env())
        self._jira = jira_client

        api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ReleaseNotesError("OPENAI_API_KEY environment variable not set.")

        self._openai_client = openai.OpenAI(api_key=api_key)
        self._model = openai_model or os.environ.get("OPENAI_MODEL") or "gpt-4"

    def create(self, *, fix_version: str) -> Dict[str, Any]:
        _validate_fix_version(fix_version)

        try:
            raw_issues = self._jira.search_issues_by_fix_version(fix_version=fix_version)
        except JiraError as e:
            raise ReleaseNotesError(str(e))

        normalized = [_normalize_issue(i) for i in raw_issues]

        # Keep the payload compact: exclude internal fields from the JSON passed to the LLM? (keep minimal)
        llm_issues = [
            {
                "key": i["key"],
                "summary": i["summary"],
                "issue_type": i["issue_type"],
                "priority": i["priority"],
                "components": i["components"],
                "labels": i["labels"],
            }
            for i in normalized
        ]

        issues_compact_json = json.dumps(llm_issues, ensure_ascii=False)

        # 1) Deterministically group issues so we never miss anything.
        grouped = group_issues(llm_issues)
        grouped_input = {
            "new_features": [
                {"key": i["key"], "summary": i["summary"], "issue_type": i["issue_type"]}
                for i in grouped["new_features"]
            ],
            "improvements": [
                {"key": i["key"], "summary": i["summary"], "issue_type": i["issue_type"]}
                for i in grouped["improvements"]
            ],
            "bug_fixes": [
                {"key": i["key"], "summary": i["summary"], "issue_type": i["issue_type"]}
                for i in grouped["bug_fixes"]
            ],
        }

        # 2) Use the LLM to rewrite each issue into customer-friendly bullet copy (no omissions).
        customer_lines = {"new_features": [], "improvements": [], "bug_fixes": []}
        try:
            grouped_issues_json = json.dumps(grouped_input, ensure_ascii=False)
            copy_prompt = build_customer_copy_user_prompt(
                fix_version=fix_version,
                grouped_issues_json=grouped_issues_json,
            )
            completion = self._openai_client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": CUSTOMER_COPY_SYSTEM_PROMPT},
                    {"role": "user", "content": copy_prompt},
                ],
                max_tokens=1600,
                temperature=0.2,
            )
            content = (completion.choices[0].message.content or "").strip()
            customer_lines = _extract_json(content)
        except Exception as e:
            logger.warning(f"Falling back to raw summaries for customer copy: {e}")
            # Fallback: ensure we still include everything.
            for key in ["new_features", "improvements", "bug_fixes"]:
                customer_lines[key] = [
                    f"{i['summary']} ({i['key']})"
                    for i in grouped_input[key]
                ]

        # Hard safety: if the model dropped items, fall back for that section.
        for key in ["new_features", "improvements", "bug_fixes"]:
            if not isinstance(customer_lines.get(key), list) or len(customer_lines.get(key, [])) != len(grouped_input[key]):
                customer_lines[key] = [f"{i['summary']} ({i['key']})" for i in grouped_input[key]]

        customer_markdown = render_customer_markdown(customer_lines)

        # 3) Generate richer multi-channel artifacts (email/social/blog) from the full list.
        # We keep the old schema for compatibility, but ensure sections contain ALL items.
        user_prompt = build_release_notes_user_prompt(
            fix_version=fix_version,
            issues_compact_json=issues_compact_json,
        )
        completion2 = self._openai_client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": RELEASE_NOTES_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1800,
            temperature=0.4,
        )
        content2 = (completion2.choices[0].message.content or "").strip()
        result = _extract_json(content2)

        # Ensure required top-level fields exist (light validation; keep robust)
        result.setdefault("release_title", f"Release {fix_version}")
        result.setdefault("release_version", fix_version)
        # Keep highlights but don't rely on them for coverage.
        result.setdefault("highlights", [])
        result.setdefault("sections", {})
        # Force full coverage sections from deterministic grouping.
        result["sections"]["features"] = customer_lines["new_features"]
        result["sections"]["improvements"] = customer_lines["improvements"]
        result["sections"]["bug_fixes"] = customer_lines["bug_fixes"]
        for k in ["breaking_changes", "known_issues"]:
            result["sections"].setdefault(k, [])
        result.setdefault("email", {"subject": f"Release {fix_version}", "body": ""})
        result.setdefault("social", {"linkedin": "", "x": ""})
        # Provide a customer-ready canonical markdown that won't miss items.
        result["customer_markdown"] = customer_markdown
        # Ensure blog_markdown uses the same headings expectation if caller uses it.
        result.setdefault("blog_markdown", customer_markdown)
        result["issues_included"] = llm_issues

        return result

