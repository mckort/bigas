"""Portfolio of Jira projects, GitHub repos, sites, and GA4 properties."""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

DEFAULT_PROJECT_ALIASES: Dict[str, List[str]] = {
    "VFA": ["vcfieldassistant", "vc field assistant", "vcfield"],
    "WAYW": ["roadpal", "waywiser", "wayw"],
    "BIG": ["bigas"],
    "REM": ["remotebrief", "remote brief"],
    "GPWW": ["greenpromowear", "green promo wear", "green promo", "gpww"],
    "FYDA": ["fyda", "fulfillyourdreamadventure", "fulfill your dream adventure"],
    "MYL": ["mylifesdeed", "my lifes deed", "my life's deed"],
    "FRI": [
        "friman investments",
        "friman investment",
        "frimaninvestments",
        "friman-investments",
        "friman investments board",
    ],
}

DEFAULT_BRAND_NAMES: Dict[str, str] = {
    "VFA": "VC Field Assistant",
    "WAYW": "RoadPal",
    "BIG": "Bigas",
    "REM": "RemoteBrief",
    "GPWW": "Green Promo Wear",
    "FYDA": "Fulfill Your Dream Adventure",
    "MYL": "My Life's Deed",
    "FRI": "Friman investments",
}

# Retired internal board titles → migrate to ``board_name_for_project`` on /api/boards load.
LEGACY_BOARD_DISPLAY_NAMES: Dict[str, str] = {
    "FRI": "Friman investments",
}

# Jira project key → GitHub owner/repo (override via BIGAS_JIRA_PROJECT_REPO_MAP).
DEFAULT_PROJECT_REPOS: Dict[str, str] = {
    "VFA": "mckort/vcfieldassistant",
    "WAYW": "mckort/roadpal",
    "BIG": "mckort/bigas",
    "REM": "mckort/remotebrief",
    "GPWW": "Green-Promo-Wear-Global/greenpromowear-website",
    "FYDA": "mckort/fulfillyourdreamadventure",
    "MYL": "mckort/mylifesdeed",
    "FRI": "mckort/frimaninvestments",
}

# owner/repo → default branch for Cursor implement / deploy refs
DEFAULT_REPO_BASE_BRANCHES: Dict[str, str] = {
    "mckort/vcfieldassistant": "main",
    "mckort/roadpal": "main",
    "mckort/bigas": "main",
    "mckort/remotebrief": "main",
    "Green-Promo-Wear-Global/greenpromowear-website": "main",
    "mckort/fulfillyourdreamadventure": "master",
    "mckort/mylifesdeed": "main",
    "mckort/frimaninvestments": "main",
}

DEFAULT_SITE_TO_PROJECT: Dict[str, str] = {
    "greenpromowear.com": "GPWW",
    "www.greenpromowear.com": "GPWW",
    "vcfieldassistant.com": "VFA",
    "www.vcfieldassistant.com": "VFA",
    "fyda.today": "FYDA",
    "www.fyda.today": "FYDA",
    "remotebrief.com": "REM",
    "www.remotebrief.com": "REM",
    "mylifesdeed.com": "MYL",
    "www.mylifesdeed.com": "MYL",
    "bigas.me": "BIG",
    "www.bigas.me": "BIG",
    "frimaninvestments.com": "FRI",
    "www.frimaninvestments.com": "FRI",
}


def normalize_project_key(value: Optional[object]) -> str:
    """Coerce ``project_key`` / ``project_keys`` (string or list) to one uppercase Jira key."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip().upper()


def parse_csv_map(raw: str) -> Dict[str, str]:
    """Parse ``KEY:value,KEY2:value2`` into a dict (keys uppercased when they look like Jira keys)."""
    out: Dict[str, str] = {}
    for part in (raw or "").split(","):
        item = part.strip()
        if not item or ":" not in item:
            continue
        key, value = item.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        out[key] = value
    return out


def jira_project_keys() -> List[str]:
    raw = (
        os.environ.get("JIRA_PROJECT_KEYS")
        or os.environ.get("JIRA_PROJECT_KEY")
        or os.environ.get("BIGAS_JIRA_AUTOMATION_ALLOWED_PROJECTS")
        or ""
    )
    keys = []
    for part in raw.split(","):
        key = part.strip().upper()
        if key and key not in keys:
            keys.append(key)
    return keys


def repo_map() -> Dict[str, str]:
    """Jira key → owner/repo."""
    out = dict(DEFAULT_PROJECT_REPOS)
    parsed = parse_csv_map(os.environ.get("BIGAS_JIRA_PROJECT_REPO_MAP") or "")
    out.update({k.strip().upper(): v.strip() for k, v in parsed.items()})
    return out


def local_path_map() -> Dict[str, str]:
    """Optional Jira key → local checkout path (for operator docs / future local tooling)."""
    parsed = parse_csv_map(os.environ.get("BIGAS_PROJECT_LOCAL_PATH_MAP") or "")
    return {k.upper(): v for k, v in parsed.items()}


def local_path_for_project(project_key: Optional[str]) -> Optional[str]:
    key = (project_key or "").strip().upper()
    if not key:
        return None
    path = (local_path_map().get(key) or "").strip()
    return path or None


def board_name_for_project(project_key: str) -> str:
    key = (project_key or "").strip().upper()
    if not key:
        return ""
    return f"{key} Board"


def board_name_migration_target(
    project_key: str, current_name: Optional[str]
) -> Optional[str]:
    """Return canonical board name when ``current_name`` is a retired default."""
    key = (project_key or "").strip().upper()
    legacy = LEGACY_BOARD_DISPLAY_NAMES.get(key)
    if not legacy or (current_name or "").strip() != legacy:
        return None
    return board_name_for_project(key)


def ga4_property_map() -> Dict[str, str]:
    """Jira key or hostname → GA4 property ID."""
    parsed = parse_csv_map(os.environ.get("BIGAS_GA4_PROPERTY_MAP") or "")
    out: Dict[str, str] = {}
    for key, value in parsed.items():
        prop = value.replace("properties/", "").strip()
        if not prop:
            continue
        out[key.upper()] = prop
        out[key.lower()] = prop
    default = (os.environ.get("GA4_PROPERTY_ID") or "").replace("properties/", "").strip()
    if default and "GPWW" not in out:
        out["GPWW"] = default
    return out


def _site_to_project() -> Dict[str, str]:
    mapping = dict(DEFAULT_SITE_TO_PROJECT)
    for url in (os.environ.get("MONITOR_URLS") or "").split(","):
        host = url.strip().lower()
        host = re.sub(r"^https?://", "", host).split("/")[0]
        if not host:
            continue
        if host not in mapping:
            for project, aliases in DEFAULT_PROJECT_ALIASES.items():
                if any(alias.replace(" ", "") in host.replace(".", "") or alias in host for alias in aliases):
                    mapping[host] = project
                    break
    return mapping


def brand_name(project_key: Optional[str]) -> str:
    key = (project_key or "").strip().upper()
    if not key:
        return "Unknown brand"
    return DEFAULT_BRAND_NAMES.get(key) or key


def site_urls_for_project(project_key: Optional[str]) -> List[str]:
    key = (project_key or "").strip().upper()
    if not key:
        return []
    urls: List[str] = []
    for host, mapped in DEFAULT_SITE_TO_PROJECT.items():
        if mapped == key and not host.startswith("www."):
            urls.append(f"https://{host}")
    return urls


def project_aliases(project_key: str) -> List[str]:
    key = (project_key or "").strip().upper()
    aliases = [key.lower(), key]
    aliases.extend(DEFAULT_PROJECT_ALIASES.get(key, []))
    repo = repo_map().get(key) or ""
    if repo:
        aliases.append(repo.lower())
        aliases.append(repo.split("/")[-1].lower())
    for host, mapped in _site_to_project().items():
        if mapped == key:
            aliases.append(host)
            aliases.append(host.split(":")[0])
    # longest first so "green promo wear" matches before "green"
    return sorted({a.strip().lower() for a in aliases if a.strip()}, key=len, reverse=True)


def resolve_project(text: str) -> Optional[str]:
    """Return a Jira project key mentioned in free text, or None."""
    blob = (text or "").lower()
    if not blob:
        return None
    keys = jira_project_keys() or list(DEFAULT_PROJECT_ALIASES.keys())
    # Explicit Jira key as a token (VFA, GPWW-12, etc.)
    for key in keys:
        if re.search(rf"\b{re.escape(key.lower())}(?:-\d+)?\b", blob):
            return key
    alias_pairs: List[Tuple[str, str]] = []
    for key in keys:
        for alias in project_aliases(key):
            if len(alias) < 3:
                continue
            alias_pairs.append((alias, key))
    alias_pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    for alias, key in alias_pairs:
        if alias in blob:
            return key
    return None


def ga4_property_for_project(project_key: Optional[str]) -> Optional[str]:
    if not project_key:
        default = (os.environ.get("GA4_PROPERTY_ID") or "").replace("properties/", "").strip()
        return default or None
    mapping = ga4_property_map()
    key = project_key.strip().upper()
    return mapping.get(key) or mapping.get(project_key.strip().lower())


def resolve_ga4_property(
    question: str,
    *,
    project_key: Optional[object] = None,
    property_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (property_id, project_key, error_message)."""
    explicit = (property_id or "").replace("properties/", "").strip()
    if explicit:
        key = normalize_project_key(project_key) or resolve_project(question)
        return explicit, key or None, None

    key = normalize_project_key(project_key) or resolve_project(question)
    if key:
        prop = ga4_property_for_project(key)
        if not prop:
            configured = sorted({k for k in ga4_property_map() if k.isupper()})
            return (
                None,
                key,
                (
                    f"No GA4 property is configured for {key}. "
                    f"Add it to BIGAS_GA4_PROPERTY_MAP (currently: {', '.join(configured) or 'none'}). "
                    "Do not query another project's Analytics property."
                ),
            )
        return prop, key, None

    default = ga4_property_for_project(None)
    return default, None, None


def scrub_analytics_question(question: str, project_key: Optional[str] = None) -> str:
    """Remove brand/site names so GA4 parse_query does not treat them as hostname filters."""
    text = question or ""
    key = (project_key or resolve_project(text) or "").upper()
    if not key:
        return text.strip()
    scrubbed = text
    for alias in project_aliases(key):
        if len(alias) < 3:
            continue
        scrubbed = re.sub(rf"\b{re.escape(alias)}\b", " ", scrubbed, flags=re.IGNORECASE)
    scrubbed = re.sub(r"\s+", " ", scrubbed).strip(" ,.-")
    return scrubbed or "How is traffic going recently? Include suggestions."


def prompt_block() -> str:
    """Human-readable catalog injected into chat agent system prompts."""
    keys = jira_project_keys() or list(repo_map().keys()) or list(DEFAULT_PROJECT_ALIASES.keys())
    repos = repo_map()
    ga4 = ga4_property_map()
    sites = _site_to_project()
    lines = [
        "You work across ALL of these projects — not a single company.",
        "",
        "| Jira | GitHub repo | Site | GA4 |",
        "|---|---|---|---|",
    ]
    for key in keys:
        repo = repos.get(key, "—")
        site = next((host for host, mapped in sites.items() if mapped == key and not host.startswith("www.")), "—")
        prop = ga4.get(key) or "not configured"
        lines.append(f"| {key} | {repo} | {site} | {prop} |")
    lines.extend(
        [
            "",
            "When the user names a product, site, repo, or Jira key, use that project.",
            "For analytics, pass project_key to ask_analytics_question. Never treat the brand name as a GA4 hostname filter.",
            "If GA4 is not configured for that project, say so — do not query another property.",
            "For new development work, ask the user to create/drag a Jira issue in that project "
            "(Research → Design → In Progress AI) so Bigas can open a PR on the mapped repo.",
            "For an existing pull request, use review_and_comment_pr with pr_url or repo + pr_number "
            "(a GitHub PR URL is enough).",
        ]
    )
    return "\n".join(lines)
