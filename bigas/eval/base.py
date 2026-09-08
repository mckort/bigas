"""Base types and registry for modular use-case evaluators."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Type

# Keys that must never appear in eval fixtures (customer workspace isolation).
FORBIDDEN_FIXTURE_KEYS = frozenset(
    {
        "workspaceId",
        "workspace_id",
        "companyId",
        "company_id",
        "workspace",
    }
)


@dataclass(frozen=True)
class EvalFixture:
    """Public company fixture for eval runs — never a live customer workspace."""

    company_name: str
    website_url: str
    extra_urls: tuple[str, ...] = ()
    input_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "company_name": self.company_name,
            "website_url": self.website_url,
            "extra_urls": list(self.extra_urls),
        }
        if self.input_text:
            data["input_text"] = self.input_text
        return data

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvalFixture":
        reject_customer_identifiers(raw)
        name = (raw.get("company_name") or raw.get("company") or "").strip()
        url = (raw.get("website_url") or raw.get("url") or "").strip()
        extra = raw.get("extra_urls") or []
        if isinstance(extra, str):
            extra = [u.strip() for u in extra.split(",") if u.strip()]
        input_text = str(raw.get("input_text") or raw.get("input") or "").strip()
        return cls(
            company_name=name,
            website_url=url,
            extra_urls=tuple(str(u).strip() for u in extra if str(u).strip()),
            input_text=input_text,
        )


def reject_customer_identifiers(raw: Mapping[str, Any]) -> None:
    """Raise if fixture or payload carries customer workspace/company identifiers."""
    for key in raw:
        if key in FORBIDDEN_FIXTURE_KEYS:
            raise ValueError(
                f"Eval fixtures must not include customer identifier {key!r}. "
                "Use public company_name + website_url only."
            )
    for nested_key in ("fixture", "payload", "body"):
        nested = raw.get(nested_key)
        if isinstance(nested, Mapping):
            reject_customer_identifiers(nested)


@dataclass
class EvalUsage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
        }


@dataclass
class EvalModelResult:
    model_id: str
    provider: str
    output: Dict[str, Any]
    usage: EvalUsage
    score: Optional[float] = None
    score_rationale: str = ""
    output_blob: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "output": self.output,
            "usage": self.usage.to_dict(),
            "score": self.score,
            "score_rationale": self.score_rationale,
            "output_blob": self.output_blob,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvalModelResult":
        usage_raw = raw.get("usage") or {}
        if not isinstance(usage_raw, Mapping):
            usage_raw = {}
        output = raw.get("output") or {}
        if not isinstance(output, Mapping):
            output = {"text": output}
        return cls(
            model_id=str(raw.get("model_id") or ""),
            provider=str(raw.get("provider") or ""),
            output=dict(output),
            usage=EvalUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                output_tokens=int(usage_raw.get("output_tokens") or 0),
                cached_tokens=int(usage_raw.get("cached_tokens") or 0),
                total_tokens=int(usage_raw.get("total_tokens") or 0),
                latency_ms=float(usage_raw.get("latency_ms") or 0),
                cost_usd=usage_raw.get("cost_usd"),
            ),
            score=raw.get("score"),
            score_rationale=str(raw.get("score_rationale") or ""),
            output_blob=str(raw.get("output_blob") or ""),
            error=raw.get("error"),
        )


@dataclass
class EvalRunResult:
    use_case: str
    run_id: str
    fixture: EvalFixture
    results: List[EvalModelResult] = field(default_factory=list)
    champion_model: str = ""
    baseline_model: str = ""
    report_blob: str = ""
    report_html_blob: str = ""
    report_markdown_blob: str = ""
    report_url: str = ""
    report_markdown: str = ""
    dry_run: bool = False

    def ranked_results(self) -> List[EvalModelResult]:
        valid = [r for r in self.results if r.error is None and r.score is not None]
        return sorted(valid, key=lambda r: (r.score or 0), reverse=True)

    def to_dict(self) -> Dict[str, Any]:
        ranked = self.ranked_results()
        return {
            "use_case": self.use_case,
            "run_id": self.run_id,
            "fixture": self.fixture.to_dict(),
            "champion_model": self.champion_model or (ranked[0].model_id if ranked else ""),
            "baseline_model": self.baseline_model,
            "report_blob": self.report_blob,
            "report_html_blob": self.report_html_blob,
            "report_markdown_blob": self.report_markdown_blob,
            "report_url": self.report_url,
            "dry_run": self.dry_run,
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvalRunResult":
        fixture_raw = raw.get("fixture") or {}
        if not isinstance(fixture_raw, Mapping):
            fixture_raw = {}
        results_raw = raw.get("results") or []
        results = [
            EvalModelResult.from_dict(item)
            for item in results_raw
            if isinstance(item, Mapping)
        ]
        return cls(
            use_case=str(raw.get("use_case") or ""),
            run_id=str(raw.get("run_id") or ""),
            fixture=EvalFixture.from_dict(fixture_raw),
            results=results,
            champion_model=str(raw.get("champion_model") or raw.get("champion") or ""),
            baseline_model=str(raw.get("baseline_model") or ""),
            report_blob=str(raw.get("report_blob") or ""),
            report_html_blob=str(raw.get("report_html_blob") or ""),
            report_markdown_blob=str(raw.get("report_markdown_blob") or ""),
            report_url=str(raw.get("report_url") or ""),
            dry_run=bool(raw.get("dry_run")),
        )


class BaseUseCaseEvaluator(ABC):
    """Run a use case (typically a YAML pack). Prompts stay in the product repo."""

    use_case_id: str = ""
    display_name: str = ""

    @abstractmethod
    def default_fixture(self) -> EvalFixture:
        raise NotImplementedError

    @abstractmethod
    def run(self, fixture: EvalFixture, model_id: str) -> tuple[Dict[str, Any], EvalUsage]:
        """Invoke the product eval pipeline. Return structured output + usage."""

    def get_judge_rubric(self) -> str:
        return (
            "Score the model output on a 0-100 scale using these criteria:\n"
            "1. Accuracy and factual grounding (no invented metrics or funding rounds)\n"
            "2. Source fidelity (claims trace to provided public sources)\n"
            "3. Structure and completeness (expected analysis sections present)\n"
            "4. Investment relevance (useful for a VC analyst)\n"
            "5. Rubric adherence (follows the expected living-analysis format)\n"
        )

    def artifact_prefix(self) -> str:
        return f"eval-runs/{self.use_case_id}"


_USE_CASE_REGISTRY: Dict[str, Type[BaseUseCaseEvaluator]] = {}


def register_use_case(cls: Type[BaseUseCaseEvaluator]) -> Type[BaseUseCaseEvaluator]:
    if not cls.use_case_id:
        raise ValueError(f"{cls.__name__} must set use_case_id")
    _USE_CASE_REGISTRY[cls.use_case_id] = cls
    return cls


def register_use_case_alias(alias: str, cls: Type[BaseUseCaseEvaluator]) -> None:
    key = (alias or "").strip().lower()
    if not key:
        raise ValueError("Use-case alias is required")
    _USE_CASE_REGISTRY[key] = cls


def get_use_case_evaluator(use_case_id: str) -> BaseUseCaseEvaluator:
    key = (use_case_id or "").strip().lower()
    if key not in _USE_CASE_REGISTRY:
        known = ", ".join(sorted(_USE_CASE_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown use case {use_case_id!r}. Known: {known}")
    return _USE_CASE_REGISTRY[key]()


def list_use_cases() -> List[str]:
    return sorted(_USE_CASE_REGISTRY)
