"""Turn eval run JSON into a human-readable markdown report."""
from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from bigas.eval.base import EvalModelResult, EvalRunResult


def _slugify(text: str) -> str:
    """Convert text to a URL-safe anchor slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", slug).strip("-")

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
    nested = output.get("fixtures")
    if isinstance(nested, list) and len(nested) > 1:
        parts: List[str] = []
        for item in nested:
            if not isinstance(item, Mapping):
                continue
            company = str(item.get("company") or "Fixture")
            parts.append(f"## Fixture: {company}")
            parts.append("")
            parts.append(humanize_model_output({k: v for k, v in item.items() if k != "fixtures"}))
            parts.append("")
        return "\n".join(parts).strip()
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


def format_duration_ms(ms: Optional[float]) -> str:
    """Human duration for reports — never dump raw millisecond walls."""
    if ms is None:
        return "—"
    try:
        value = float(ms)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(value) or value <= 0:
        return "—"
    if value < 1000:
        return f"{value:.0f} ms"
    seconds = value / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    total_seconds = int(round(seconds))
    hours, rem = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {secs:02d}s"


human_duration_ms = format_duration_ms


def _company_count(run: EvalRunResult, result: Optional[EvalModelResult] = None) -> int:
    if result and result.fixture_scores:
        return len(result.fixture_scores)
    fixtures = run.all_fixtures()
    return max(len(fixtures), 1)


def _pack_label(count: int) -> str:
    return "1 company" if count == 1 else f"{count} companies"


def fixture_count_for_result(
    result: EvalModelResult, run: Optional[EvalRunResult] = None
) -> int:
    if result.fixture_scores:
        return max(1, len(result.fixture_scores))
    if run is not None:
        fixtures = run.all_fixtures()
        return max(1, len(fixtures))
    return 1


def time_per_company_ms(
    result: EvalModelResult, run: Optional[EvalRunResult] = None
) -> Optional[float]:
    """User-facing wait per company. Generate only — judges excluded."""
    per_company = result.generate_ms_per_company()
    if per_company is not None and result.fixture_scores:
        return per_company
    usage = result.usage
    if usage and usage.generate_latency_ms:
        return float(usage.generate_latency_ms) / fixture_count_for_result(result, run)
    return per_company


def cost_per_company_usd(result: EvalModelResult, run: EvalRunResult) -> Optional[float]:
    per_company = result.cost_usd_per_company()
    if per_company is not None and result.fixture_scores:
        return per_company
    usage = result.usage
    if not usage or usage.cost_usd is None:
        return None
    return usage.cost_usd / fixture_count_for_result(result, run)


def _time_per_company(result: EvalModelResult, run: EvalRunResult) -> str:
    return format_duration_ms(time_per_company_ms(result, run))


def _cost_per_company(result: EvalModelResult, run: EvalRunResult) -> str:
    value = cost_per_company_usd(result, run)
    if value is None:
        return "n/a"
    return f"${value:.4f}"


def judge_column_keys(run: EvalRunResult) -> List[str]:
    keys: List[str] = []
    for item in run.judge_models:
        if item and item not in keys:
            keys.append(item)
    for result in run.results:
        for key in result.judge_scores:
            if key not in keys:
                keys.append(key)
    return keys


def _judge_label(key: str) -> str:
    model = (key or "").split(":", 1)[-1]
    return model or key or "judge"


def _score_cell(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}"


def _score_display(value: Optional[float]) -> str:
    """Score for prose (e.g. executive summary); avoids format errors on None."""
    if value is None:
        return "N/A"
    return f"{value:.1f}"


def format_motivation(result: EvalModelResult) -> str:
    """Human prose only — never dump raw judge JSON."""
    parts: List[str] = []
    if result.fixture_scores:
        for row in result.fixture_scores:
            company = str(row.get("company") or "Fixture")
            mean = row.get("score")
            heading = f"**{company}**"
            if mean is not None:
                heading += f" — mean {_score_cell(float(mean))}"
            dur = format_duration_ms(row.get("generate_ms"))
            if dur != "—":
                heading += f" · {dur}"
            per_judge = []
            for verdict in row.get("judges") or []:
                if not isinstance(verdict, Mapping):
                    continue
                label = str(verdict.get("model_id") or "judge")
                score = verdict.get("score")
                rationale = _prose_only(str(verdict.get("rationale") or ""))
                score_bit = f" ({float(score):.0f})" if score is not None else ""
                if rationale:
                    per_judge.append(f"{label}{score_bit}: {rationale}")
            mech = [str(item) for item in (row.get("mechanical_notes") or [])]
            block = [heading]
            block.extend(per_judge)
            if mech:
                block.append("Mechanical: " + "; ".join(mech))
            parts.append("\n".join(block))
    elif result.judges:
        for verdict in result.judges:
            if not isinstance(verdict, Mapping):
                continue
            label = str(verdict.get("model_id") or "judge")
            score = verdict.get("score")
            rationale = _prose_only(str(verdict.get("rationale") or ""))
            if not rationale:
                continue
            score_bit = f" ({float(score):.0f})" if score is not None else ""
            parts.append(f"**{label}{score_bit}:** {rationale}")
        if result.mechanical_notes:
            parts.append("**Mechanical:** " + "; ".join(result.mechanical_notes))
    else:
        prose = _prose_only(result.score_rationale)
        if prose:
            parts.append(prose)
        if result.mechanical_notes:
            parts.append("**Mechanical:** " + "; ".join(result.mechanical_notes))
    return "\n\n".join(parts) or "_No rationale provided._"


def _prose_only(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw.startswith("{") and '"rationale"' in raw:
        try:
            data = json.loads(raw)
            if isinstance(data, Mapping):
                return str(data.get("rationale") or "").strip()
        except json.JSONDecodeError:
            match = re.search(r'"rationale"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
            if match:
                val = match.group(1)
                try:
                    return val.encode("utf-8").decode("unicode_escape")
                except Exception:
                    return val
        return ""
    if raw.startswith("{") or raw.startswith("["):
        return ""
    return raw


def _scoring_header(run: EvalRunResult) -> List[str]:
    fixtures = run.all_fixtures()
    fixture_bits = [
        f"{item.company_name} — {item.website_url}" if item.website_url else item.company_name
        for item in fixtures
    ]
    judges = run.judge_models or judge_column_keys(run)
    lines = [
        f"**Use case:** {run.use_case}",
        f"**Run ID:** {run.run_id}",
        f"**Fixtures:** {'; '.join(fixture_bits) or (run.fixture.company_name + ' — ' + run.fixture.website_url)}",
    ]
    if judges:
        lines.append("**Judges:** " + ", ".join(f"`{item}`" for item in judges))
    lines.append(
        "**Scoring:** mean of judges (grounding 30%, structure 20%, landscape 25%, "
        "hallucination 25%), then mechanical penalty for missing sections, "
        "unsupported landscape names, and invented figures."
    )
    if run.rubric:
        lines.extend(["", "**Rubric:**", "", run.rubric.strip(), ""])
    else:
        lines.append("")
    return lines


def _build_executive_summary(run: EvalRunResult) -> List[str]:
    """Build a concise executive summary box."""
    ranked = run.ranked_results()
    if not ranked:
        return []

    champion = ranked[0]
    baseline = run.baseline_model or ""
    baseline_result = None
    for result in ranked:
        key = model_key(result)
        if key == baseline or result.model_id == baseline or result.model_id == baseline.split(":", 1)[-1]:
            baseline_result = result
            break

    lines = [
        "## Executive Summary",
        "",
        f"**Recommended model:** `{champion.model_id}` with score **{_score_display(champion.score)}/100**",
        "",
    ]

    if baseline_result and baseline_result is not champion:
        improvement = (champion.score or 0) - (baseline_result.score or 0)
        sign = "+" if improvement > 0 else ""
        lines.append(
            f"**vs. current production** (`{baseline_result.model_id}`): "
            f"{_score_display(baseline_result.score)}/100 → {_score_display(champion.score)}/100 "
            f"({sign}{improvement:.1f} points)"
        )
        lines.append("")

    n = _company_count(run, champion)
    pack_note = _pack_label(n)
    lines.append(f"**Pack size:** {pack_note}")
    cost_champion = cost_per_company_usd(champion, run)
    pack_cost = champion.usage.cost_usd if champion.usage else None
    if cost_champion is not None:
        if n > 1 and pack_cost is not None:
            lines.append(
                f"**Cost:** ${cost_champion:.4f} per company "
                f"(pack: {pack_note}, ${float(pack_cost):.4f} total)"
            )
        else:
            lines.append(f"**Cost:** ${cost_champion:.4f} per company")
    time_champion = time_per_company_ms(champion, run)
    if time_champion:
        lines.append(f"**Time:** {format_duration_ms(time_champion)} per company")
    if run.elapsed_ms:
        lines.append(
            f"**This eval:** {format_duration_ms(run.elapsed_ms)} wall clock "
            "(all models + judges)"
        )

    if baseline_result and baseline_result is not champion:
        cost_baseline = cost_per_company_usd(baseline_result, run)
        if cost_champion is not None and cost_baseline and cost_baseline > 0:
            lines.append(
                f"**Cost comparison:** {cost_champion / cost_baseline:.1f}× vs. production model"
            )
        time_baseline = time_per_company_ms(baseline_result, run)
        if time_champion and time_baseline and time_baseline > 0:
            lines.append(
                f"**Time comparison:** {time_champion / time_baseline:.1f}× vs. production model"
            )

    lines.append("")
    return lines


def _build_table_of_contents(run: EvalRunResult) -> List[str]:
    """Build a table of contents with anchor links."""
    ranked = run.ranked_results()
    errors = [item for item in run.results if item.error]

    lines = [
        "## Contents",
        "",
        "- [Executive Summary](#executive-summary)",
        "- [Ranking](#ranking)",
    ]

    if ranked:
        lines.append("- [Model Results](#model-results)")
        for idx, result in enumerate(ranked, start=1):
            role = result_roles(result, run)
            role_suffix = f" ({format_roles(role)})" if role else ""
            slug = _slugify(f"{idx}-{result.model_id}")
            lines.append(f"  - [{idx}. {result.model_id}{role_suffix}](#{slug})")

    if errors:
        lines.append("- [Failed Models](#failed-models)")

    lines.extend(["", "---", ""])
    return lines


def _parse_rationale_to_bullets(rationale: str) -> List[Tuple[str, str, str]]:
    """Parse rationale text into structured bullet points.
    
    Returns list of (status, category, text) tuples.
    Status is one of: 'good', 'warning', 'bad', 'neutral'
    """
    bullets: List[Tuple[str, str, str]] = []
    text = (rationale or "").strip()
    if not text:
        return bullets

    positive_patterns = [
        (r"well[- ]grounded", "Grounding"),
        (r"grounding[:\s]+(?:strong|solid|good|100)", "Grounding"),
        (r"all required sections", "Structure"),
        (r"structure[:\s]+(?:strong|solid|good|complete|100)", "Structure"),
        (r"no (?:fabricated|invented|hallucinated)", "Hallucination"),
        (r"hallucination[:\s]+(?:low|none|0|100)", "Hallucination"),
        (r"correctly (?:categorizes|separates|populates)", "Landscape"),
        (r"landscape[:\s]+(?:strong|solid|good|complete|100)", "Landscape"),
    ]

    warning_patterns = [
        (r"slightly weaken", "Minor issue"),
        (r"some (?:numeric|specifics)", "Minor issue"),
        (r"inferred|extrapolated", "Inference"),
        (r"truncated", "Structure"),
    ]

    negative_patterns = [
        (r"empty|missing", "Missing content"),
        (r"failed|failure", "Failure"),
        (r"landscape.*empty", "Landscape"),
        (r"violat", "Violation"),
    ]

    sentences = re.split(r"(?<=[.;])\s+", text)
    
    for sentence in sentences[:5]:
        sentence = sentence.strip()
        if not sentence or len(sentence) < 20:
            continue
            
        status = "neutral"
        category = "Note"
        
        sentence_lower = sentence.lower()
        for pattern, cat in positive_patterns:
            if re.search(pattern, sentence_lower):
                status = "good"
                category = cat
                break
        
        if status == "neutral":
            for pattern, cat in warning_patterns:
                if re.search(pattern, sentence_lower):
                    status = "warning"
                    category = cat
                    break
        
        if status == "neutral":
            for pattern, cat in negative_patterns:
                if re.search(pattern, sentence_lower):
                    status = "bad"
                    category = cat
                    break
        
        if len(sentence) > 150:
            sentence = sentence[:147] + "..."
        
        bullets.append((status, category, sentence))
    
    return bullets[:4]


def format_motivation_structured(result: EvalModelResult) -> str:
    """Format motivation with structured bullet points."""
    parts: List[str] = []
    
    if result.fixture_scores:
        for row in result.fixture_scores:
            company = str(row.get("company") or "Fixture")
            mean = row.get("score")
            heading = f"**{company}**"
            if mean is not None:
                heading += f" — mean {_score_cell(float(mean))}"
            dur = format_duration_ms(row.get("generate_ms"))
            if dur != "—":
                heading += f" · {dur}"
            parts.append(heading)
            parts.append("")
            
            for verdict in row.get("judges") or []:
                if not isinstance(verdict, Mapping):
                    continue
                label = str(verdict.get("model_id") or "judge")
                score = verdict.get("score")
                rationale = _prose_only(str(verdict.get("rationale") or ""))
                
                score_bit = f" ({float(score):.0f})" if score is not None else ""
                parts.append(f"**{label}{score_bit}:**")
                
                bullets = _parse_rationale_to_bullets(rationale)
                if bullets:
                    for status, category, text in bullets:
                        if status == "good":
                            icon = "✓"
                        elif status == "warning":
                            icon = "⚠"
                        elif status == "bad":
                            icon = "✗"
                        else:
                            icon = "•"
                        parts.append(f"- {icon} **{category}:** {text}")
                elif rationale:
                    parts.append(f"- {rationale[:200]}{'...' if len(rationale) > 200 else ''}")
                parts.append("")
            
            mech = [str(item) for item in (row.get("mechanical_notes") or [])]
            if mech:
                parts.append("**Mechanical penalties:**")
                for note in mech:
                    parts.append(f"- ✗ {note}")
                parts.append("")
    
    elif result.judges:
        for verdict in result.judges:
            if not isinstance(verdict, Mapping):
                continue
            label = str(verdict.get("model_id") or "judge")
            score = verdict.get("score")
            rationale = _prose_only(str(verdict.get("rationale") or ""))
            if not rationale:
                continue
            
            score_bit = f" ({float(score):.0f})" if score is not None else ""
            parts.append(f"**{label}{score_bit}:**")
            
            bullets = _parse_rationale_to_bullets(rationale)
            if bullets:
                for status, category, text in bullets:
                    if status == "good":
                        icon = "✓"
                    elif status == "warning":
                        icon = "⚠"
                    elif status == "bad":
                        icon = "✗"
                    else:
                        icon = "•"
                    parts.append(f"- {icon} **{category}:** {text}")
            elif rationale:
                parts.append(f"- {rationale[:200]}{'...' if len(rationale) > 200 else ''}")
            parts.append("")
        
        if result.mechanical_notes:
            parts.append("**Mechanical penalties:**")
            for note in result.mechanical_notes:
                parts.append(f"- ✗ {note}")
            parts.append("")
    
    else:
        prose = _prose_only(result.score_rationale)
        if prose:
            bullets = _parse_rationale_to_bullets(prose)
            if bullets:
                for status, category, text in bullets:
                    if status == "good":
                        icon = "✓"
                    elif status == "warning":
                        icon = "⚠"
                    elif status == "bad":
                        icon = "✗"
                    else:
                        icon = "•"
                    parts.append(f"- {icon} **{category}:** {text}")
            else:
                parts.append(prose)
        if result.mechanical_notes:
            parts.append("")
            parts.append("**Mechanical penalties:**")
            for note in result.mechanical_notes:
                parts.append(f"- ✗ {note}")
    
    return "\n".join(parts) or "_No rationale provided._"


def build_summary_markdown(run: EvalRunResult, *, include_navigation: bool = False) -> str:
    """Short ranking for Discord / PM chat.

    When ``include_navigation`` is True (full HTML/markdown reports), prepend executive
    summary and table of contents with anchor links.
    """
    ranked = run.ranked_results()
    lines = [
        "# AI Model Evaluation Report",
        "",
        *_scoring_header(run),
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

    if include_navigation:
        lines.extend(_build_executive_summary(run))
        lines.extend(_build_table_of_contents(run))

    champion = ranked[0]
    judge_keys = judge_column_keys(run)
    headers = [
        "Rank",
        "Model",
        "Role",
        "Mean",
        *[_judge_label(key) for key in judge_keys],
        "Mech",
        "Time / company",
        "Est. cost / company",
    ]
    lines.extend(
        [
            f"**Champion:** {champion.model_id} (mean {_score_display(champion.score)}/100)",
            "",
            "## Ranking",
            "",
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" if i < 3 else "---:" for i in range(len(headers))) + " |",
        ]
    )
    for idx, result in enumerate(ranked, start=1):
        role = format_roles(result_roles(result, run)) or "—"
        cells = [
            str(idx),
            result.model_id,
            role,
            _score_cell(result.score),
        ]
        for key in judge_keys:
            cells.append(_score_cell(result.judge_scores.get(key)))
        cells.extend(
            [
                f"−{result.mechanical_penalty:.0f}" if result.mechanical_penalty else "0",
                _time_per_company(result, run),
                _cost_per_company(result, run),
            ]
        )
        lines.append("| " + " | ".join(cells) + " |")

    pack_note = _pack_label(_company_count(run, champion))
    lines.extend(
        [
            "",
            f"_Time and cost are per company (this pack: {pack_note}). Judge time is excluded._",
        ]
    )
    if run.elapsed_ms:
        lines.append(
            f"_This eval took {format_duration_ms(run.elapsed_ms)} (all models + judges)._"
        )

    lines.extend(["", "## Model Results", ""])
    for idx, result in enumerate(ranked, start=1):
        role = format_roles(result_roles(result, run))
        slug = _slugify(f"{idx}-{result.model_id}")
        heading = f"### {idx}. {result.model_id} {{#{slug}}}"
        if role:
            heading = f"### {idx}. {result.model_id} ({role}) {{#{slug}}}"
        lines.append(heading)
        lines.append("")
        lines.append(format_motivation_structured(result))
        lines.append("")

    if errors and not include_navigation:
        for item in errors:
            lines.append(f"- **{item.model_id}:** {item.error}")
        lines.append("")

    if run.report_url:
        lines.append(f"Open the full readable report (outputs included): {run.report_url}")
    return "\n".join(lines).strip()


def build_full_markdown(run: EvalRunResult) -> str:
    """Full readable report: ranking plus each model's written output."""
    lines = [build_summary_markdown(run, include_navigation=True)]
    if not run.results:
        lines.extend(["", "---", "", "## Model Outputs", "", "_No model outputs._"])
        return "\n".join(lines).strip()

    lines.extend(["", "---", "", "## Full Model Outputs", ""])
    lines.append("_Detailed outputs from each model, including all generated sections._")
    lines.append("")

    ranked = run.ranked_results()
    errors = [item for item in run.results if item.error]
    
    for idx, result in enumerate(ranked, start=1):
        role = format_roles(result_roles(result, run))
        slug = _slugify(f"output-{result.model_id}")
        title = result.model_id
        if role:
            title = f"{title} ({role})"
        lines.append(f"# {title} {{#{slug}}}")
        lines.append("")
        
        if result.score is not None:
            judge_bits = " · ".join(
                f"{_judge_label(key)} {value:.1f}" for key, value in result.judge_scores.items()
            )
            extra = f" · {judge_bits}" if judge_bits else ""
            mech = f" · mech −{result.mechanical_penalty:.0f}" if result.mechanical_penalty else ""
            lines.append(
                f"**Mean:** {result.score:.1f}/100{extra}{mech} · "
                f"{_time_per_company(result, run)} / co. · {_cost_per_company(result, run)} / co."
            )
            lines.append("")
        
        motivation = format_motivation_structured(result)
        if motivation and motivation != "_No rationale provided._":
            lines.append("### Judge Assessment")
            lines.append("")
            lines.append(motivation)
            lines.append("")
        
        lines.append("### Generated Output")
        lines.append("")
        lines.append(humanize_model_output(result.output or {}))
        lines.append("")
        lines.append("---")
        lines.append("")
    
    if errors:
        lines.append("## Failed Models")
        lines.append("")
        for item in errors:
            lines.append(f"### {item.model_id}")
            lines.append("")
            lines.append(f"**Error:** {item.error}")
            lines.append("")

    return "\n".join(lines).strip()
