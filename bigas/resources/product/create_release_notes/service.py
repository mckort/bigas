from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import os
import re
import logging

from bigas.llm.factory import get_llm_client

from bigas.resources.product.create_release_notes.jira_client import JiraClient, JiraConfig, JiraError
from bigas.resources.product.create_release_notes.prompts import (
    RELEASE_NOTES_SYSTEM_PROMPT,
    build_release_notes_user_prompt,
    CUSTOMER_COPY_SYSTEM_PROMPT,
    build_customer_copy_user_prompt,
    COMMS_PACK_SYSTEM_PROMPT,
    build_comms_pack_user_prompt,
)
from bigas.resources.product.create_release_notes.formatter import group_issues, render_customer_markdown

logger = logging.getLogger(__name__)

# Gemini steps: comms pack prompt is very large (blog example + all issues). If
# max_output_tokens is too low, the model can hit MAX_TOKENS before emitting any Part.
RN_COMMS_PACK_MAX_TOKENS = 8192
RN_CUSTOMER_COPY_MAX_TOKENS = 4096
RN_MAIN_SCHEMA_MAX_TOKENS = 4096


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

    # First, try direct JSON parse.
    try:
        return json.loads(t)
    except Exception:
        # Best-effort: extract the first JSON object from within surrounding text.
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = t[start : end + 1]
            try:
                return json.loads(candidate)
            except Exception:
                # Fall through to empty fallback below.
                pass

        # As a last resort, return an empty object rather than failing the entire flow.
        logger.warning(
            "Failed to parse LLM JSON response; returning empty object. "
            "Raw response (first 500 chars): %s",
            (t[:500] + ("..." if len(t) > 500 else "")),
        )
        return {}


def _deterministic_release_notes_result(
    *,
    fix_version: str,
    customer_sections: Dict[str, Any],
    social: Dict[str, str],
    blog_markdown: str,
    customer_markdown: str,
    llm_issues: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build API-shaped release notes without the final LLM JSON step.
    Used when the model returns no text or parsing fails for that step.
    """
    highlights: List[str] = []
    for key in ("new_features", "improvements", "bug_fixes"):
        for line in customer_sections.get(key) or []:
            if isinstance(line, str) and line.strip():
                highlights.append(line.strip())
            if len(highlights) >= 5:
                break
        if len(highlights) >= 5:
            break

    return {
        "release_title": f"Release {fix_version}",
        "release_version": fix_version,
        "highlights": highlights,
        "sections": {
            "features": list(customer_sections.get("new_features") or []),
            "improvements": list(customer_sections.get("improvements") or []),
            "bug_fixes": list(customer_sections.get("bug_fixes") or []),
            "breaking_changes": [],
            "known_issues": [],
        },
        "email": {
            "subject": f"Release {fix_version}",
            "body": customer_markdown,
        },
        "social": {
            "linkedin": social.get("linkedin") or "",
            "x": social.get("x") or "",
            "facebook": social.get("facebook") or "",
            "instagram": social.get("instagram") or "",
        },
        "blog_markdown": blog_markdown,
        "customer_markdown": customer_markdown,
        "issues_included": llm_issues,
    }


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

        # Use shared LLM abstraction; ignore openai_api_key in favor of env-based config.
        # Model resolution order:
        #   1) openai_model argument
        #   2) BIGAS_RELEASE_NOTES_MODEL
        #   3) LLM_MODEL
        #   4) "gemini-2.5-pro" (factory default)
        self._llm, self._model = get_llm_client(
            feature="release_notes",
            explicit_model=openai_model,
        )

    def create(self, *, fix_version: str, jql_extra: str = "") -> Dict[str, Any]:
        _validate_fix_version(fix_version)

        try:
            raw_issues = self._jira.search_issues_by_fix_version(
                fix_version=fix_version,
                jql_extra=(jql_extra or "").strip(),
            )
        except JiraError as e:
            raise ReleaseNotesError(str(e))

        normalized = [_normalize_issue(i) for i in raw_issues]

        logger.info(
            "Release notes: fetched %d Jira issue(s) for fix_version=%s",
            len(normalized),
            fix_version,
        )
        if normalized:
            preview = [
                f"{i.get('key','')}:{(i.get('summary','') or '')[:120]}"
                for i in normalized[:2]
            ]
            logger.info("Release notes Jira preview: %s", " | ".join(preview))

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

        # 2) Create a customer communications pack (no omissions).
        grouped_issues_json = json.dumps(grouped_input, ensure_ascii=False)
        customer_sections = {"new_features": [], "improvements": [], "bug_fixes": []}
        social = {"x": "", "linkedin": "", "facebook": "", "instagram": ""}
        blog_markdown = ""

        try:
            comms_prompt = build_comms_pack_user_prompt(
                fix_version=fix_version,
                grouped_issues_json=grouped_issues_json,
            )
            content = self._llm.complete(
                messages=[
                    {"role": "system", "content": COMMS_PACK_SYSTEM_PROMPT},
                    {"role": "user", "content": comms_prompt},
                ],
                max_tokens=RN_COMMS_PACK_MAX_TOKENS,
                temperature=0.3,
            )
            pack = _extract_json(content)
            customer_sections = (pack.get("sections") or customer_sections)
            social = (pack.get("social") or social)
            blog_markdown = (pack.get("blog_markdown") or "")
        except Exception as e:
            logger.warning(f"Falling back to basic customer copy (no comms pack): {e}")

            # Fallback: rewrite as simple bullets without keys.
            try:
                copy_prompt = build_customer_copy_user_prompt(
                    fix_version=fix_version,
                    grouped_issues_json=grouped_issues_json,
                )
                content = self._llm.complete(
                    messages=[
                        {"role": "system", "content": CUSTOMER_COPY_SYSTEM_PROMPT},
                        {"role": "user", "content": copy_prompt},
                    ],
                    max_tokens=RN_CUSTOMER_COPY_MAX_TOKENS,
                    temperature=0.2,
                )
                customer_sections = _extract_json(content)
            except Exception:
                # Last resort: raw summaries (still no keys).
                customer_sections = {
                    k: [i["summary"] for i in grouped_input[k]]
                    for k in ["new_features", "improvements", "bug_fixes"]
                }

        # Hard safety: if the model dropped items, fall back for that section.
        for key in ["new_features", "improvements", "bug_fixes"]:
            if not isinstance(customer_sections.get(key), list) or len(customer_sections.get(key, [])) != len(grouped_input[key]):
                customer_sections[key] = [i["summary"] for i in grouped_input[key]]

        customer_markdown = render_customer_markdown(customer_sections)
        if not blog_markdown:
            blog_markdown = customer_markdown

        # 3) Generate richer multi-channel artifacts (email/social/blog) from the full list.
        # We keep the old schema for compatibility, but ensure sections contain ALL items.
        user_prompt = build_release_notes_user_prompt(
            fix_version=fix_version,
            issues_compact_json=issues_compact_json,
        )
        try:
            content2 = self._llm.complete(
                messages=[
                    {"role": "system", "content": RELEASE_NOTES_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=RN_MAIN_SCHEMA_MAX_TOKENS,
                temperature=0.4,
            )
            result = _extract_json(content2)
        except ReleaseNotesError as e:
            if "Empty LLM response" in str(e):
                logger.warning(
                    "Release notes: empty LLM response for main schema step (model=%s); "
                    "using deterministic payload from Jira + earlier steps.",
                    self._model,
                )
                result = _deterministic_release_notes_result(
                    fix_version=fix_version,
                    customer_sections=customer_sections,
                    social=social,
                    blog_markdown=blog_markdown,
                    customer_markdown=customer_markdown,
                    llm_issues=llm_issues,
                )
            else:
                raise

        # Ensure required top-level fields exist (light validation; keep robust)
        result.setdefault("release_title", f"Release {fix_version}")
        result.setdefault("release_version", fix_version)
        # Keep highlights but don't rely on them for coverage.
        result.setdefault("highlights", [])
        result.setdefault("sections", {})
        # Force full coverage sections from deterministic grouping.
        result["sections"]["features"] = customer_sections["new_features"]
        result["sections"]["improvements"] = customer_sections["improvements"]
        result["sections"]["bug_fixes"] = customer_sections["bug_fixes"]
        for k in ["breaking_changes", "known_issues"]:
            result["sections"].setdefault(k, [])
        result.setdefault("email", {"subject": f"Release {fix_version}", "body": ""})
        # Expand social drafts; keep keys-compatible fields.
        result["social"] = {
            "linkedin": (social.get("linkedin") or ""),
            "x": (social.get("x") or ""),
            "facebook": (social.get("facebook") or ""),
            "instagram": (social.get("instagram") or ""),
        }
        # Provide a customer-ready canonical markdown that won't miss items.
        result["customer_markdown"] = customer_markdown
        # Ensure blog_markdown uses the same headings expectation if caller uses it.
        result["blog_markdown"] = blog_markdown
        result["issues_included"] = llm_issues

        return result

