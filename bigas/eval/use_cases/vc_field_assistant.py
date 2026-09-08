"""VC Field Assistant living-analysis eval via a YAML pack (no product HTTP API)."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from bigas.eval.base import (
    BaseUseCaseEvaluator,
    EvalFixture,
    EvalUsage,
    register_use_case,
    register_use_case_alias,
    reject_customer_identifiers,
)
from bigas.eval.complete import complete_eval_model
from bigas.eval.pack import (
    EvalPack,
    flatten_step_output,
    load_pack,
    render_step_prompt,
)
from bigas.eval.research import fetch_page_text, run_web_research

PACK_ID = "vfa-living-analysis"


def _usage_from_completion(usage: EvalUsage, completion: Any) -> None:
    raw = getattr(completion, "usage", None)
    prompt = int(getattr(raw, "prompt_tokens", None) or 0)
    output = int(getattr(raw, "candidates_tokens", None) or getattr(raw, "output_tokens", None) or 0)
    total = int(getattr(raw, "total_tokens", None) or 0)
    usage.prompt_tokens += prompt
    usage.output_tokens += output
    if total:
        usage.total_tokens += total
    else:
        usage.total_tokens = usage.prompt_tokens + usage.output_tokens


class PackEvaluator(BaseUseCaseEvaluator):
    """Run a pack: resolve prompts, optional Bigas-side web research, complete locally."""

    pack_id: str = ""
    _pack: Optional[EvalPack] = None

    def __init__(self, pack: Optional[EvalPack] = None):
        self._pack = pack

    @property
    def pack(self) -> EvalPack:
        if self._pack is None:
            self._pack = load_pack(self.pack_id or PACK_ID)
        return self._pack

    def default_fixture(self) -> EvalFixture:
        fixtures = self.default_fixtures()
        return fixtures[0]

    def default_fixtures(self) -> List[EvalFixture]:
        raws = list(self.pack.fixtures or [])
        if not raws and self.pack.fixture:
            raws = [self.pack.fixture]
        if not raws:
            raws = [{"company": "VC Field Assistant", "url": "https://vcfieldassistant.com"}]
        fixtures: List[EvalFixture] = []
        for index, raw in enumerate(raws):
            data = dict(raw)
            if index == 0:
                company = (os.environ.get("EVAL_VFA_DEFAULT_COMPANY") or "").strip()
                url = (os.environ.get("EVAL_VFA_DEFAULT_URL") or "").strip()
                if company:
                    data["company"] = company
                if url:
                    data["url"] = url
            fixtures.append(EvalFixture.from_dict(data))
        return fixtures

    def get_judge_rubric(self) -> str:
        if self.pack.rubric:
            return (
                "Score the model output on a 0-100 scale.\n"
                "This evaluates the product prompt suite plus Bigas-side web research "
                "(not the product's full backend pipeline).\n\n"
                f"{self.pack.rubric}"
            )
        return super().get_judge_rubric()

    def run(self, fixture: EvalFixture, model_id: str) -> Tuple[Dict[str, Any], EvalUsage]:
        reject_customer_identifiers(fixture.to_dict())
        page = ""
        if fixture.website_url:
            page = fetch_page_text(fixture.website_url)
        if not page:
            page = fixture.input_text

        context: Dict[str, Any] = {
            "fixture": {
                "company": fixture.company_name,
                "company_name": fixture.company_name,
                "url": fixture.website_url,
                "website_url": fixture.website_url,
                "page": page,
                "input": fixture.input_text or page,
            },
            "steps": {},
            "research": {"snippets": ""},
        }
        usage = EvalUsage()
        step_outputs: Dict[str, str] = {}
        snippets = ""

        for step in self.pack.steps:
            if step.research:
                snippets = run_web_research(
                    step.research,
                    context,
                    extra_urls=fixture.extra_urls,
                )
                context["research"] = {"snippets": snippets}
            prompt = render_step_prompt(step, context)
            completion = complete_eval_model(model_id, prompt)
            text = (completion.text or "").strip()
            step_outputs[step.id] = text
            context["steps"][step.id] = flatten_step_output(text)
            _usage_from_completion(usage, completion)

        output = {
            "pack_id": self.pack.id,
            "steps": step_outputs,
            "sections": step_outputs,
            "research_used": bool(snippets),
            "sources": {
                "page": page,
                "snippets": snippets,
            },
        }
        return output, usage


@register_use_case
class VCFieldAssistantEvaluator(PackEvaluator):
    use_case_id = "vc-field-assistant"
    display_name = "VC Field Assistant living analysis"
    pack_id = PACK_ID


register_use_case_alias("vfa-living-analysis", VCFieldAssistantEvaluator)
