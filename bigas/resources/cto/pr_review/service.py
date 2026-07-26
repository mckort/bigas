"""
PR review service: run AI review on a diff and return markdown review text.
Uses Gemini by default (GEMINI_API_KEY); override via BIGAS_CTO_PR_REVIEW_MODEL or llm_model.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from bigas.llm.completion import LLMCompletion
from bigas.llm.factory import get_llm_client

from bigas.resources.cto.pr_review.prompts import (
    ReviewPhase,
    build_pr_review_user_prompt,
    system_prompt_for_phase,
)

logger = logging.getLogger(__name__)

MIN_REVIEW_TOKENS = 1_000
# Gemini Pro-class models support large outputs; thinking tokens share this budget.
MAX_REVIEW_TOKENS = 65_536
DEFAULT_MAX_REVIEW_TOKENS = 8_000
DEFAULT_MAX_CONTINUATIONS = 3
# Leave headroom for visible review text when the model thinks internally.
DEFAULT_THINKING_BUDGET = 8_192

_CONTINUE_PROMPT = (
    "Continue the PR review exactly where you left off. "
    "Do not repeat any prior text. Finish all remaining sections "
    "(Blockers, Important, Minor) and end cleanly."
)


# Max diff size (chars); overridable via BIGAS_CTO_PR_REVIEW_MAX_DIFF_CHARS env.
def _max_diff_chars() -> int:
    raw = os.environ.get("BIGAS_CTO_PR_REVIEW_MAX_DIFF_CHARS", "").strip()
    if not raw:
        return 150_000
    try:
        return max(10_000, min(500_000, int(raw)))
    except ValueError:
        return 150_000


def _max_review_tokens() -> int:
    """
    Max tokens for the LLM review response; overridable via BIGAS_CTO_PR_REVIEW_MAX_TOKENS env.

    On Gemini thinking models this budget is shared between thinking and visible text.
    """
    raw = (os.environ.get("BIGAS_CTO_PR_REVIEW_MAX_TOKENS") or "").strip()
    if not raw:
        return DEFAULT_MAX_REVIEW_TOKENS
    try:
        return max(MIN_REVIEW_TOKENS, min(MAX_REVIEW_TOKENS, int(raw)))
    except ValueError:
        return DEFAULT_MAX_REVIEW_TOKENS


def _max_continuations() -> int:
    raw = (os.environ.get("BIGAS_CTO_PR_REVIEW_MAX_CONTINUATIONS") or "").strip()
    if not raw:
        return DEFAULT_MAX_CONTINUATIONS
    try:
        return max(0, min(5, int(raw)))
    except ValueError:
        return DEFAULT_MAX_CONTINUATIONS


def _thinking_budget() -> Optional[int]:
    """
    Optional Gemini thinking budget. Empty/0 disables sending thinking_config.
    Default caps thinking so more of max_output_tokens goes to the review body.
    """
    raw = (os.environ.get("BIGAS_CTO_PR_REVIEW_THINKING_BUDGET") or "").strip()
    if raw.lower() in {"", "default"}:
        return DEFAULT_THINKING_BUDGET
    if raw.lower() in {"0", "none", "off", "false"}:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except ValueError:
        return DEFAULT_THINKING_BUDGET


def _looks_incomplete(text: str) -> bool:
    """Heuristic fallback when finish_reason is missing: cut mid-bullet/sentence."""
    t = (text or "").rstrip()
    if not t:
        return True
    last_line = t.splitlines()[-1].strip()
    if not last_line:
        return True
    # Unclosed bold/code markers on the last line.
    if last_line.count("**") % 2 == 1 or last_line.count("`") % 2 == 1:
        return True
    # Bullet clearly cut mid-phrase (no terminal punctuation).
    if re.match(r"^([-*]|\d+\.)\s+\S", last_line) and last_line[-1] not in ".!?:;`\"')]":
        return True
    # Dangling connector words are a strong truncation signal.
    if re.search(r"\b(and|or|the|a|an|to|for|with|of|in)\s*$", last_line, flags=re.I):
        return True
    return False


def _should_continue(result: LLMCompletion) -> bool:
    if result.truncated:
        return True
    return _looks_incomplete(result.text)


def _complete_detailed(llm: Any, **kwargs: Any) -> LLMCompletion:
    """Call complete_detailed when available; fall back to complete()."""
    detailed = getattr(llm, "complete_detailed", None)
    if callable(detailed):
        return detailed(**kwargs)
    text = llm.complete(**kwargs)
    return LLMCompletion(text=(text or "").strip(), finish_reason=None)


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
            4) "gemini-3.1-pro-preview" (factory default)
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
        *,
        phase: ReviewPhase = "initial",
        previous_review: Optional[str] = None,
    ) -> str:
        """
        Run AI review on the given diff. Returns markdown review text.
        If diff exceeds MAX_DIFF_CHARS, it is truncated and a note is prepended to the review.
        Continues generation when the model hits MAX_TOKENS mid-review.
        """
        if not diff or not diff.strip():
            raise PRReviewError("diff is required and must be non-empty.")

        max_chars = _max_diff_chars()
        truncated_note = ""
        if len(diff) > max_chars:
            diff = diff[:max_chars] + "\n\n... (diff truncated for length)\n"
            truncated_note = f"_Review is based on the first {max_chars} characters of the diff._\n\n"

        if phase not in {"initial", "post_autofix"}:
            phase = "initial"

        user_prompt = build_pr_review_user_prompt(
            diff=diff,
            instructions=instructions,
            phase=phase,
            previous_review=previous_review,
        )
        system_prompt = system_prompt_for_phase(phase)
        # Slightly lower temperature for more consistent, checklist-driven reviews.
        temperature = 0.1 if phase == "initial" else 0.0
        max_tokens = _max_review_tokens()
        max_continuations = _max_continuations()
        thinking_budget = _thinking_budget()

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        chunks: List[str] = []

        try:
            for attempt in range(max_continuations + 1):
                call_kwargs: Dict[str, Any] = {
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                if thinking_budget is not None and str(self._model).lower().startswith("gemini"):
                    call_kwargs["thinking_budget"] = thinking_budget

                result = _complete_detailed(self._llm, **call_kwargs)
                piece = (result.text or "").strip()
                if not piece:
                    if attempt == 0:
                        raise PRReviewError(
                            "LLM returned empty review "
                            f"(finish_reason={result.finish_reason!r})."
                        )
                    logger.warning(
                        "PR review continuation %d returned empty text (finish_reason=%s); stopping.",
                        attempt,
                        result.finish_reason,
                    )
                    break

                chunks.append(piece)
                if attempt >= max_continuations or not _should_continue(result):
                    if result.truncated:
                        logger.warning(
                            "PR review still truncated after %d continuation(s) "
                            "(finish_reason=%s).",
                            attempt,
                            result.finish_reason,
                        )
                    break

                logger.info(
                    "PR review truncated (finish_reason=%s, incomplete=%s); continuing (%d/%d).",
                    result.finish_reason,
                    _looks_incomplete(piece),
                    attempt + 1,
                    max_continuations,
                )
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": "\n".join(chunks)},
                    {"role": "user", "content": _CONTINUE_PROMPT},
                ]
        except PRReviewError:
            raise
        except Exception as e:
            logger.error("PR review LLM call failed", exc_info=True)
            raise PRReviewError(f"LLM request failed: {e}") from e

        content = "\n".join(chunks).strip()
        if truncated_note:
            content = truncated_note + content
        return content
