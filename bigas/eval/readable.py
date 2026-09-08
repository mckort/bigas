"""Turn eval run JSON into a human-readable markdown report."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from bigas.eval.base import EvalModelResult, EvalRunResult

_FENCE_RE = re.compile(r"^```(?:json|markdown|md)?\s*\n(.*)\n```\s*$", re.DOTALL | re.IGNORECASE)


def model_key(result: EvalModelResult) -> str:
    return f"{result.provider}:{result.model_id}" if result.provider else result.model_id


def result_roles(result: EvalModelResult, run: EvalRunResult) -> List[str]:
    roles: List[str] = []
    ranked = run.ranked_results()
    if ranked and result is ranked[0]:
        roles.append("Champion")
    baseline = (run.baseline_model or "").strip()
    if baseline and (
        model_key(result) == baseline
        or result.model_id == baseline
        or result.model_id == baseline.split(":", 1)[-1]
    ):
        roles.append("In production")
    return roles


def format_roles(roles: Sequence[str]) -> str:
    return " · ".join(roles)


def _strip_fence(text: str) -> str:
    raw = (text or "").strip()
    match = _FENCE_RE.match(raw)
    return match.group(1).strip() if match else raw


def _try_json(text: str) -> Any:
    raw = _strip_fence(text)
    if not raw or raw[0] not in "{[":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _landscape_rows(rows: Any) -> List[Mapping[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                cell.replace("|", "/").replace("\r", " ").replace("\n", " ") for cell in row
            )
            + " |"
        )
    return "\n".join(lines)


def _humanize_landscape(data: Mapping[str, Any]) -> str:
    parts: List[str] = []
    buckets = (
        ("overlapping", "Overlapping products"),
        ("adHoc", "Ad-hoc substitutes"),
        ("complementary", "Complementary systems"),
    )
    for key, title in buckets:
        rows = _landscape_rows(data.get(key))
        parts.append(f"### {title}")
        parts.append("")
        if not rows:
            parts.append("_None._")
        else:
            parts.append(
                _md_table(
                    ("Name", "Product", "Strength", "Relevance"),
                    (
                        (
                            str(row.get("name") or ""),
                            str(row.get("product") or ""),
                            str(row.get("strength") or ""),
                            str(row.get("relevance") or ""),
                        )
                        for row in rows
                    ),
                )
            )
        parts.append("")
    note = str(data.get("websiteClaimNote") or "").strip()
    if note:
        parts.extend(["### Website claim note", "", note, ""])
    return "\n".join(parts).strip()


def _humanize_sections(sections: Sequence[Any]) -> str:
    parts: List[str] = []
    for item in sections:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or item.get("key") or "Section").strip()
        body = str(item.get("body") or "").strip()
        parts.append(f"### {title}")
        parts.append("")
        parts.append(body or "_Empty._")
        parts.append("")
    return "\n".join(parts).strip()


def _humanize_mapping(data: Mapping[str, Any], *, skip: Optional[set] = None) -> str:
    ignore = skip or set()
    lines: List[str] = []
    for key, value in data.items():
        if key in ignore or key == "citations":
            continue
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value if str(item).strip())
            lines.append(f"- **{key}:** {rendered or '—'}")
        elif isinstance(value, Mapping):
            lines.append(f"- **{key}:**")
            for nested_key, nested_value in value.items():
                lines.append(f"  - {nested_key}: {nested_value}")
        else:
            text = str(value).strip()
            if text:
                lines.append(f"- **{key}:** {text}")
    return "\n".join(lines).strip()


def humanize_step_output(step_id: str, text: str) -> str:
    parsed = _try_json(text)
    if isinstance(parsed, Mapping):
        sections = parsed.get("sections")
        if isinstance(sections, list):
            return _humanize_sections(sections)
        if any(key in parsed for key in ("overlapping", "adHoc", "complementary")):
            return _humanize_landscape(parsed)
        mapping = _humanize_mapping(parsed)
        if mapping:
            return mapping
    return _strip_fence(text) or "_Empty._"


def iter_step_outputs(output: Mapping[str, Any]) -> List[Tuple[str, str]]:
    steps = output.get("steps") or output.get("sections")
    if isinstance(steps, Mapping):
        return [(str(key), "" if value is None else str(value)) for key, value in steps.items()]
    if isinstance(output.get("output"), Mapping):
        return iter_step_outputs(output["output"])  # type: ignore[arg-type]
    return []


def humanize_model_output(output: Mapping[str, Any]) -> str:
    steps = iter_step_outputs(output)
    if not steps:
        dumped = json.dumps(dict(output), indent=2, ensure_ascii=False)
        return f"```\n{dumped}\n```" if dumped and dumped != "{}" else "_No model output._"
    parts: List[str] = []
    for step_id, text in steps:
        parts.append(f"## {step_id}")
        parts.append("")
        parts.append(humanize_step_output(step_id, text))
        parts.append("")
    return "\n".join(parts).strip()


def _latency(result: EvalModelResult) -> str:
    return f"{result.usage.latency_ms:.0f} ms"


def _cost(result: EvalModelResult) -> str:
    if result.usage.cost_usd is None:
        return "n/a"
    return f"${result.usage.cost_usd:.4f}"


def build_summary_markdown(run: EvalRunResult) -> str:
    """Short ranking for Discord / PM chat."""
    ranked = run.ranked_results()
    lines = [
        "# AI Model Evaluation Report",
        "",
        f"**Use case:** {run.use_case}",
        f"**Run ID:** {run.run_id}",
        f"**Fixture:** {run.fixture.company_name} — {run.fixture.website_url}",
        "",
    ]
    if run.baseline_model:
        lines.append(f"**In production:** `{run.baseline_model}`")
        lines.append("")
    if run.report_url:
        lines.append(f"**Readable report:** {run.report_url}")
        lines.append("")

    errors = [item for item in run.results if item.error]
    if not ranked:
        lines.append("_No successful model results to rank._")
        for item in errors:
            lines.append(f"- {item.model_id}: {item.error}")
        return "\n".join(lines).strip()

    champion = ranked[0]
    lines.extend(
        [
            f"**Champion:** {champion.model_id} (score {champion.score:.1f}/100)",
            "",
            "## Ranking",
            "",
            "| Rank | Model | Role | Score | Latency | Est. cost |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for idx, result in enumerate(ranked, start=1):
        role = format_roles(result_roles(result, run)) or "—"
        lines.append(
            f"| {idx} | {result.model_id} | {role} | {result.score:.1f} | {_latency(result)} | {_cost(result)} |"
        )

    lines.extend(["", "## Motivation", ""])
    for idx, result in enumerate(ranked, start=1):
        role = format_roles(result_roles(result, run))
        heading = f"### {idx}. {result.model_id}"
        if role:
            heading = f"{heading} ({role})"
        lines.append(heading)
        lines.append(result.score_rationale or "_No rationale provided._")
        lines.append("")

    if errors:
        lines.extend(["## Failed models", ""])
        for item in errors:
            lines.append(f"- **{item.model_id}:** {item.error}")
        lines.append("")

    if run.report_url:
        lines.append(f"Open the full readable report (outputs included): {run.report_url}")
    return "\n".join(lines).strip()


def build_full_markdown(run: EvalRunResult) -> str:
    """Full readable report: ranking plus each model's written output."""
    lines = [build_summary_markdown(run), "", "## Model outputs", ""]
    if not run.results:
        lines.append("_No model outputs._")
        return "\n".join(lines).strip()

    for result in run.results:
        role = format_roles(result_roles(result, run))
        title = result.model_id
        if role:
            title = f"{title} ({role})"
        lines.append(f"# {title}")
        lines.append("")
        if result.error:
            lines.append(f"**Error:** {result.error}")
            lines.append("")
            continue
        if result.score is not None:
            lines.append(f"**Score:** {result.score:.1f}/100 · {_latency(result)} · {_cost(result)}")
        if result.score_rationale:
            lines.append("")
            lines.append(result.score_rationale)
        lines.append("")
        lines.append(humanize_model_output(result.output or {}))
        lines.append("")
    return "\n".join(lines).strip()
