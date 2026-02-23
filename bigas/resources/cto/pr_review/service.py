"""
PR review service: run AI review on a diff and return markdown review text.
Uses gpt-4o by default (chat/completions); override via BIGAS_CTO_PR_REVIEW_MODEL or llm_model.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import openai

from bigas.resources.cto.pr_review.prompts import (
    PR_REVIEW_SYSTEM_PROMPT,
    build_pr_review_user_prompt,
)

logger = logging.getLogger(__name__)

# Max diff size (chars); overridable via BIGAS_CTO_PR_REVIEW_MAX_DIFF_CHARS env.
def _max_diff_chars() -> int:
    raw = os.environ.get("BIGAS_CTO_PR_REVIEW_MAX_DIFF_CHARS", "").strip()
    if not raw:
        return 150_000
    try:
        return max(10_000, min(500_000, int(raw)))
    except ValueError:
        return 150_000


class PRReviewError(RuntimeError):
    pass


class PRReviewService:
    def __init__(
        self,
        *,
        openai_api_key: Optional[str] = None,
        openai_model: Optional[str] = None,
    ):
        api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise PRReviewError("OPENAI_API_KEY environment variable not set.")
        self._openai_client = openai.OpenAI(api_key=api_key)
        # Default gpt-4o (chat/completions); override with BIGAS_CTO_PR_REVIEW_MODEL or OPENAI_MODEL
        self._model = (
            openai_model
            or os.environ.get("BIGAS_CTO_PR_REVIEW_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "gpt-4o"
        )

    def review(
        self,
        diff: str,
        instructions: Optional[str] = None,
    ) -> str:
        """
        Run AI review on the given diff. Returns markdown review text.
        If diff exceeds MAX_DIFF_CHARS, it is truncated and a note is prepended to the review.
        """
        if not diff or not diff.strip():
            raise PRReviewError("diff is required and must be non-empty.")

        max_chars = _max_diff_chars()
        truncated_note = ""
        if len(diff) > max_chars:
            diff = diff[:max_chars] + "\n\n... (diff truncated for length)\n"
            truncated_note = f"_Review is based on the first {max_chars} characters of the diff._\n\n"

        user_prompt = build_pr_review_user_prompt(diff=diff, instructions=instructions)
        try:
            completion = self._openai_client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": PR_REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=4000,
                temperature=0.3,
            )
            content = (completion.choices[0].message.content or "").strip()
        except Exception as e:
            logger.error("PR review OpenAI call failed", exc_info=True)
            raise PRReviewError(f"OpenAI request failed: {e}") from e

        if truncated_note:
            content = truncated_note + content
        return content
