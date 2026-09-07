"""Load and render YAML eval packs (prompts stay in the product repo)."""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

import requests
import yaml

from bigas.eval.base import reject_customer_identifiers

logger = logging.getLogger(__name__)

# Fragment aliases → headings in vcfieldassistant/docs/analysis-prompts.md
# (stable #classify-style anchors are optional; Bigas matches these slugs.)
HEADING_ALIASES: Dict[str, Tuple[str, ...]] = {
    "classify": (
        "classify",
        "4.1",
        "klassificera bolaget",
        "klassificera",
    ),
    "primary": (
        "primary",
        "2.2",
        "primary pass",
        "1.1",
        "1.2",
        "1.3",
        "1.5",
        "2.1",
    ),
    "competitive-landscape": (
        "competitive-landscape",
        "competitive landscape",
        "1.4",
        "competitive analysis",
        "3.7",
        "skriv landscape",
    ),
    "guardrails-moats": (
        "guardrails-moats",
        "1.6",
        "guardrails",
        "competitive moats",
    ),
    "investment-thesis": (
        "investment-thesis",
        "1.8",
        "investment thesis",
    ),
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
_SOURCE_CACHE: Dict[str, str] = {}


@dataclass(frozen=True)
class PackResearch:
    provider: str
    queries: Tuple[str, ...] = ()
    fetch_urls_from: str = ""
    max_pages: int = 6
    max_results: int = 5


@dataclass
class PackStep:
    id: str
    prompt: str = ""
    prompt_from: str = ""
    input: str = ""
    research: Optional[PackResearch] = None
    resolved_prompt: str = ""


@dataclass
class EvalPack:
    id: str
    name: str
    source: str = ""
    fixture: Dict[str, Any] = field(default_factory=dict)
    steps: List[PackStep] = field(default_factory=list)
    rubric: str = ""
    path: Optional[Path] = None


def pack_search_dirs() -> List[Path]:
    dirs: List[Path] = []
    env = (os.environ.get("EVAL_PACK_DIR") or "").strip()
    if env:
        dirs.append(Path(env).expanduser())
    dirs.append(Path(__file__).resolve().parents[2] / "eval")
    return dirs


def find_pack_path(pack_id: str) -> Path:
    key = (pack_id or "").strip()
    if not key:
        raise ValueError("Pack id is required")
    if key.endswith(".yaml") or key.endswith(".yml") or "/" in key:
        path = Path(key).expanduser()
        if path.is_file():
            return path
        raise FileNotFoundError(f"Eval pack file not found: {key}")
    for directory in pack_search_dirs():
        for name in (f"{key}.pack.yaml", f"{key}.pack.yml", f"{key}.yaml"):
            candidate = directory / name
            if candidate.is_file():
                return candidate
    searched = ", ".join(str(d) for d in pack_search_dirs())
    raise FileNotFoundError(f"Eval pack {key!r} not found in {searched}")


def load_pack(pack_id: str) -> EvalPack:
    return load_pack_file(find_pack_path(pack_id))


def load_pack_file(path: Path) -> EvalPack:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"Eval pack {path} must be a YAML object")
    pack = pack_from_mapping(raw, path=path)
    resolve_pack_prompts(pack)
    return pack


def pack_from_mapping(raw: Mapping[str, Any], *, path: Optional[Path] = None) -> EvalPack:
    reject_customer_identifiers(raw)
    pack_id = str(raw.get("id") or "").strip()
    if not pack_id:
        raise ValueError("Eval pack must set id")
    fixture = raw.get("fixture") or {}
    if fixture and not isinstance(fixture, Mapping):
        raise ValueError("Eval pack fixture must be an object")
    if isinstance(fixture, Mapping):
        reject_customer_identifiers(fixture)
    steps_raw = raw.get("steps") or []
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError(f"Eval pack {pack_id!r} must declare at least one step")
    steps = [_parse_step(item, index) for index, item in enumerate(steps_raw)]
    rubric = raw.get("rubric") or ""
    if isinstance(rubric, list):
        rubric = "\n".join(str(line) for line in rubric)
    return EvalPack(
        id=pack_id,
        name=str(raw.get("name") or pack_id).strip(),
        source=str(raw.get("source") or "").strip(),
        fixture=dict(fixture) if isinstance(fixture, Mapping) else {},
        steps=steps,
        rubric=str(rubric).strip(),
        path=path,
    )


def _parse_step(raw: Any, index: int) -> PackStep:
    if not isinstance(raw, Mapping):
        raise ValueError(f"Pack step {index} must be an object")
    reject_customer_identifiers(raw)
    step_id = str(raw.get("id") or "").strip()
    if not step_id:
        raise ValueError(f"Pack step {index} must set id")
    prompt = str(raw.get("prompt") or "")
    prompt_from = str(raw.get("prompt_from") or "").strip()
    if not prompt.strip() and not prompt_from:
        raise ValueError(f"Pack step {step_id!r} must set prompt or prompt_from")
    research = None
    research_raw = raw.get("research")
    if research_raw:
        if not isinstance(research_raw, Mapping):
            raise ValueError(f"Pack step {step_id!r} research must be an object")
        queries = research_raw.get("queries") or []
        if isinstance(queries, str):
            queries = [queries]
        research = PackResearch(
            provider=str(research_raw.get("provider") or "web").strip() or "web",
            queries=tuple(str(q) for q in queries if str(q).strip()),
            fetch_urls_from=str(research_raw.get("fetch_urls_from") or "").strip(),
            max_pages=int(research_raw.get("max_pages") or 6),
            max_results=int(research_raw.get("max_results") or 5),
        )
    return PackStep(
        id=step_id,
        prompt=prompt,
        prompt_from=prompt_from,
        input=str(raw.get("input") or ""),
        research=research,
        resolved_prompt=prompt.strip(),
    )


def resolve_pack_prompts(pack: EvalPack) -> None:
    for step in pack.steps:
        if step.prompt.strip():
            step.resolved_prompt = step.prompt.strip()
            continue
        step.resolved_prompt = resolve_prompt_from(pack, step.prompt_from)


def resolve_prompt_from(pack: EvalPack, prompt_from: str) -> str:
    spec = (prompt_from or "").strip()
    if not spec:
        raise ValueError("prompt_from is empty")
    path, fragment = _split_prompt_from(spec)
    text = load_prompt_source(pack, path)
    if not fragment:
        return _prefer_fenced_blocks(text).strip()
    extracted = extract_heading_prompts(text, fragment)
    if not extracted.strip():
        raise ValueError(
            f"Could not resolve heading {fragment!r} in {path} "
            f"(pack {pack.id}). Add a matching heading or a stable #anchor."
        )
    return extracted.strip()


def _split_prompt_from(spec: str) -> Tuple[str, str]:
    if "#" in spec:
        path, fragment = spec.split("#", 1)
        return path.strip(), fragment.strip()
    return spec, ""


def load_prompt_source(pack: EvalPack, rel_path: str) -> str:
    rel = (rel_path or "").strip().lstrip("/")
    cache_key = f"{pack.source}|{rel}|{pack.path}"
    if cache_key in _SOURCE_CACHE:
        return _SOURCE_CACHE[cache_key]
    local = _find_local_source(pack, rel)
    if local is not None:
        text = local.read_text(encoding="utf-8")
        _SOURCE_CACHE[cache_key] = text
        return text
    url = github_raw_url(pack.source, rel)
    if not url:
        raise FileNotFoundError(
            f"Prompt source {rel} not found locally and pack {pack.id} has no GitHub source"
        )
    logger.info("Fetching eval prompt source %s", url)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    text = resp.text
    _SOURCE_CACHE[cache_key] = text
    return text


def _find_local_source(pack: EvalPack, rel_path: str) -> Optional[Path]:
    candidates: List[Path] = []
    root = (os.environ.get("EVAL_PACK_SOURCE_ROOT") or "").strip()
    if root:
        candidates.append(Path(root).expanduser() / rel_path)
    if pack.path:
        candidates.append(pack.path.parent / rel_path)
        candidates.append(pack.path.parent.parent / rel_path)
    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root / rel_path)
    candidates.append(repo_root.parent / "vcfieldassistant" / rel_path)
    parsed = urlparse(pack.source or "")
    if parsed.netloc == "github.com":
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            candidates.append(repo_root.parent / parts[1] / rel_path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def github_raw_url(source: str, rel_path: str) -> str:
    source = (source or "").strip()
    if not source:
        return ""
    parsed = urlparse(source)
    if "raw.githubusercontent.com" in (parsed.netloc or ""):
        base = source.rstrip("/")
        return f"{base}/{rel_path}" if not base.endswith(rel_path) else base
    if parsed.netloc != "github.com":
        return ""
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return ""
    owner, repo = parts[0], parts[1]
    ref = (os.environ.get("EVAL_PACK_SOURCE_REF") or "main").strip() or "main"
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{rel_path}"


def slugify_heading(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = re.sub(r"^#+\s*", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


def heading_matches(title: str, fragment: str) -> bool:
    target = (fragment or "").strip().lstrip("#")
    if not target:
        return False
    title_norm = (title or "").strip()
    title_slug = slugify_heading(title_norm)
    aliases = HEADING_ALIASES.get(target.lower(), ())
    candidates = (target, *aliases)
    for alias in candidates:
        alias = (alias or "").strip()
        if not alias:
            continue
        if _is_numbered_heading(alias):
            if re.match(rf"^{re.escape(alias)}(?:\s|$)", title_norm, flags=re.IGNORECASE):
                return True
            continue
        alias_slug = slugify_heading(alias)
        if not alias_slug:
            continue
        if title_slug == alias_slug or title_slug.startswith(alias_slug + "-"):
            return True
        if f"-{alias_slug}-" in f"-{title_slug}-":
            return True
    return False


def _is_numbered_heading(value: str) -> bool:
    return bool(re.match(r"^\d+(?:\.\d+)+$", (value or "").strip()))


def extract_heading_prompts(markdown: str, fragment: str) -> str:
    """Return fenced prompt blocks (or section body) for a heading slug/alias."""
    blocks: List[str] = []
    seen: set[str] = set()
    for _start, level, title, body in _iter_sections(markdown):
        if not heading_matches(title, fragment):
            continue
        extracted = _prefer_fenced_blocks(body)
        key = extracted.strip()
        if key and key not in seen:
            seen.add(key)
            blocks.append(extracted.strip())
        # Numbered fragments like "4.1" should take the first match only.
        if re.match(r"^\d+(\.\d+)*$", fragment.strip()):
            break
    return "\n\n".join(blocks)


def _iter_sections(markdown: str) -> Iterable[Tuple[int, int, str, str]]:
    matches = list(_HEADING_RE.finditer(markdown or ""))
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        content_start = match.end()
        content_end = len(markdown)
        for later in matches[index + 1 :]:
            if len(later.group(1)) <= level:
                content_end = later.start()
                break
        yield match.start(), level, title, markdown[content_start:content_end]


def _prefer_fenced_blocks(text: str) -> str:
    fences = re.findall(r"```(?:[a-zA-Z0-9_-]+)?\n(.*?)```", text or "", flags=re.DOTALL)
    if fences:
        return "\n\n".join(block.strip() for block in fences if block.strip())
    return (text or "").strip()


def interpolate(template: str, context: Mapping[str, Any]) -> str:
    def _replace(match: re.Match[str]) -> str:
        path = match.group(1)
        value = lookup_path(context, path)
        if value is None:
            logger.warning("Eval pack template missing %s", path)
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return _TEMPLATE_RE.sub(_replace, template or "")


def lookup_path(context: Mapping[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        if isinstance(current, Mapping):
            snake = _to_snake(part)
            camel = _to_camel(part)
            if snake in current:
                current = current[snake]
                continue
            if camel in current:
                current = current[camel]
                continue
        return None
    return current


def flatten_step_output(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {"output": text}
    parsed = _try_json(text)
    if isinstance(parsed, Mapping):
        for key, value in parsed.items():
            data[str(key)] = value
            data[_to_snake(str(key))] = value
            data[_to_camel(str(key))] = value
    return data


def _try_json(text: str) -> Any:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _to_snake(name: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).replace("-", "_").lower()


def _to_camel(name: str) -> str:
    parts = re.split(r"[-_]", name)
    if not parts:
        return name
    return parts[0].lower() + "".join(part[:1].upper() + part[1:] for part in parts[1:] if part)


def render_step_prompt(step: PackStep, context: Mapping[str, Any]) -> str:
    parts = [interpolate(step.resolved_prompt or step.prompt, context)]
    if step.input.strip():
        parts.append(interpolate(step.input, context))
    return "\n\n".join(part.strip() for part in parts if part.strip())
