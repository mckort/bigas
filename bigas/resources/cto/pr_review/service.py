"""
PR review service: run AI review on a diff and return markdown review text.
Uses gpt-4o by default (chat/completions); override via BIGAS_CTO_PR_REVIEW_MODEL or llm_model.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from bigas.llm.factory import get_llm_client

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
        """
        PRReviewService now uses the shared LLM abstraction.

        - If openai_api_key is provided, it is ignored in favor of environment-based
          configuration to keep provider selection consistent.
        - Model resolution order:
            1) openai_model argument (from llm_model request body)
            2) BIGAS_CTO_PR_REVIEW_MODEL
            3) LLM_MODEL
            4) "gpt-4o"
        - Provider is inferred from the model name (gpt-* -> OpenAI, gemini-* -> Gemini),
          falling back to BIGAS_LLM_PROVIDER / OpenAI.
        """
        explicit_model = openai_model
        self._llm, self._model = get_llm_client(
            feature="cto_pr_review",
            explicit_model=explicit_model,
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
            content = self._llm.complete(
                messages=[
                    {"role": "system", "content": PR_REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=4000,
                temperature=0.3,
            )
        except Exception as e:
            logger.error("PR review LLM call failed", exc_info=True)
            raise PRReviewError(f"LLM request failed: {e}") from e

        if truncated_note:
            content = truncated_note + content
        return content
