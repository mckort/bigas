"""Prompts for the CTO PR review (Codex) model."""
from __future__ import annotations

from typing import Optional

PR_REVIEW_SYSTEM_PROMPT = """You are a senior engineer performing a pull request review.
Your role is to give concise, actionable feedback that helps maintain quality and catch issues early.

Guidelines:
- Focus on logic, correctness, security, maintainability, and clear naming. Mention style only when it affects readability.
- Be specific: reference file/line or code snippets where relevant. Avoid vague comments.
- Prioritize: call out blockers and important issues first; minor suggestions can be brief.
- Keep the review scannable: use short paragraphs or bullets. Use markdown (headers, code fences) where it helps.
- Do not invent problems. If the diff looks good, say so briefly and add one or two optional improvements if any.
- Return only the review text—no meta-commentary, no "Here is my review" wrapper. Start directly with the review content."""


def build_pr_review_user_prompt(diff: str, instructions: Optional[str] = None) -> str:
    """Build the user prompt with the PR diff and optional custom instructions."""
    parts = ["Review the following pull request diff."]
    if instructions and instructions.strip():
        parts.append(f"\nAdditional instructions from the team:\n{instructions.strip()}")
    parts.append("\n\nDiff:\n")
    parts.append(diff)
    return "".join(parts)
