"""VC Field Assistant living-analysis eval adapter (return-only; no customer writes)."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Tuple

import requests

from bigas.eval.base import (
    BaseUseCaseEvaluator,
    EvalFixture,
    EvalUsage,
    register_use_case,
    reject_customer_identifiers,
)

logger = logging.getLogger(__name__)


def _default_company() -> str:
    return (os.environ.get("EVAL_VFA_DEFAULT_COMPANY") or "VC Field Assistant").strip()


def _default_url() -> str:
    return (os.environ.get("EVAL_VFA_DEFAULT_URL") or "https://vcfieldassistant.com").strip()


def _vfa_endpoint() -> str:
    base = (os.environ.get("EVAL_VFA_ENDPOINT") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "EVAL_VFA_ENDPOINT is not configured. "
            "Point it at the VFA eval-only API (POST /eval/living-analysis)."
        )
    return base


def _vfa_auth_headers() -> Dict[str, str]:
    token = (os.environ.get("EVAL_VFA_AUTH_TOKEN") or "").strip()
    if not token:
        return {}
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


@register_use_case
class VCFieldAssistantEvaluator(BaseUseCaseEvaluator):
    use_case_id = "vc-field-assistant"
    display_name = "VC Field Assistant living analysis"

    def default_fixture(self) -> EvalFixture:
        return EvalFixture(
            company_name=_default_company(),
            website_url=_default_url(),
        )

    def get_judge_rubric(self) -> str:
        return (
            "Score VC Field Assistant living-analysis output on a 0-100 scale:\n"
            "1. Accuracy — no invented funding, revenue, or team facts\n"
            "2. Source fidelity — claims grounded in the public website/fixture\n"
            "3. Structure — executive summary, market, product, team, risks, etc.\n"
            "4. Investment relevance — actionable for a VC analyst\n"
            "5. Consistency — sections align and do not contradict\n"
        )

    def run(self, fixture: EvalFixture, model_id: str) -> Tuple[Dict[str, Any], EvalUsage]:
        reject_customer_identifiers(fixture.to_dict())
        payload = {
            "fixture": fixture.to_dict(),
            "model": model_id,
        }
        reject_customer_identifiers(payload)

        url = f"{_vfa_endpoint()}/eval/living-analysis"
        timeout = int(os.environ.get("EVAL_VFA_TIMEOUT_SECONDS", "600"))
        resp = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", **_vfa_auth_headers()},
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"VFA eval adapter returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError("VFA eval adapter returned non-object JSON")

        reject_customer_identifiers(data)
        if data.get("wrote_to_workspace") or data.get("company_doc_written"):
            raise RuntimeError(
                "VFA eval adapter attempted customer workspace writes — eval aborted."
            )

        output = data.get("sections") or data.get("output") or data
        usage_raw = data.get("usage") or {}
        usage = EvalUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens") or 0),
            output_tokens=int(usage_raw.get("output_tokens") or usage_raw.get("completion_tokens") or 0),
            cached_tokens=int(usage_raw.get("cached_tokens") or 0),
            total_tokens=int(usage_raw.get("total_tokens") or 0),
        )
        if usage.total_tokens <= 0:
            usage.total_tokens = usage.prompt_tokens + usage.output_tokens

        return output if isinstance(output, dict) else {"sections": output}, usage
