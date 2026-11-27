"""Biweekly Pass or Fund → VC Field Assistant blog/memo PR job.

Cloud Scheduler hits ``publish_passorfund_case_blog`` every Friday. In-app
``every_n_weeks`` (epoch 2026-12-04) keeps the real cadence every other Friday
before the Monday newsletter. The job:

1. Finds the latest published case on passorfund.com
2. Loads the guest deal memo via authenticated VFA MCP (Pass or Fund workspace)
3. Opens a VFA content PR that upserts ``passOrFundCases.ts`` (full memo)
4. Opens a companion PoF allowlist PR so the CTA does not 404
5. Posts Discord so Marcus can review / merge / deploy VFA
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests

from bigas.discord_webhook import post_to_discord
from bigas.resources.devops.github_actions import GitHubActionsClient, GitHubActionsError
from bigas.resources.devops.service import DevOpsError, _github_client
from bigas.tickets.releases import lookup_board_release_defaults

logger = logging.getLogger(__name__)

STOCKHOLM = ZoneInfo("Europe/Stockholm")
# First biweekly Friday before the 2026-12-07 newsletter; then every other Friday.
POF_BLOG_CADENCE_EPOCH = date(2026, 12, 4)

POF_SITE = (os.environ.get("PASSORFUND_SITE_URL") or "https://passorfund.com").rstrip("/")
VFA_REPO = (os.environ.get("VFA_GITHUB_REPO") or "mckort/vcfieldassistant").strip()
POF_REPO = (os.environ.get("POF_GITHUB_REPO") or "mckort/pass-or-fund").strip()
VFA_CASES_PATH = "web/src/lib/passOrFundCases.ts"
VFA_CASE_PAGE_PATH = "web/src/pages/PassOrFundCasePage.tsx"
POF_CONFIG_PATH = "src/config/__init__.py"
BRANCH_PREFIX = "bigas-pof-blog/"
VFA_TICKET_KEY = (os.environ.get("POF_BLOG_VFA_TICKET") or "VFA-359").strip()
POF_TICKET_KEY = (os.environ.get("POF_BLOG_POF_TICKET") or "POF-52").strip()

_HTTP_TIMEOUT = 45
_USER_AGENT = "BigasPassOrFundBlogJob/1.0"


class PassOrFundBlogError(RuntimeError):
    """Safe-to-surface failure for the scheduler / MCP response."""


@dataclass(frozen=True)
class PofCaseMeta:
    slug: str
    company: str
    title: str
    summary: str
    published_on: str
    source_url: str
    utm_campaign: str


@dataclass(frozen=True)
class MemoSection:
    heading: str
    body: str


def _http_get(url: str) -> str:
    resp = requests.get(
        url,
        timeout=_HTTP_TIMEOUT,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
    )
    if resp.status_code >= 400:
        raise PassOrFundBlogError(f"HTTP {resp.status_code} fetching {url}")
    return resp.text or ""


def _slug_from_article_href(href: str) -> Optional[str]:
    m = re.search(r"/article/([A-Za-z0-9][A-Za-z0-9_-]*)", href or "")
    return m.group(1) if m else None


def list_published_case_slugs(html: Optional[str] = None) -> List[str]:
    """Ordered unique article slugs from the homepage (newest first)."""
    page = html if html is not None else _http_get(f"{POF_SITE}/")
    seen: List[str] = []
    for href in re.findall(r'href="(/article/[^"#?]+)"', page):
        slug = _slug_from_article_href(href)
        if slug and slug not in seen:
            seen.append(slug)
    if not seen:
        raise PassOrFundBlogError("No published Pass or Fund articles found on the homepage")
    return seen


def _strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _company_from_html(html: str, title: str) -> str:
    pill = re.search(
        r'tag-pill[^>]*>\s*([^<]+?)\s*[—–-]',
        html,
        flags=re.I,
    )
    if pill:
        name = _strip_tags(pill.group(1))
        if name:
            return name
    # "Treat raises …" → Treat
    words = (title or "").strip().split()
    if words:
        return words[0].rstrip(".,:;!")
    raise PassOrFundBlogError("Could not parse company name from Pass or Fund article")


def _published_on(slug: str, html: str) -> str:
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", slug or "")
    if m:
        return m.group(1)
    meta = re.search(
        r'property="article:published_time"\s+content="([^"]+)"',
        html,
        flags=re.I,
    )
    if meta:
        raw = meta.group(1).strip()
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
                return raw[:10]
    return date.today().isoformat()


def _utm_campaign(company: str, published_on: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", (company or "case").lower()).strip("-") or "case"
    return f"pof-{token[:40]}-{published_on}"


def fetch_case_meta(slug: str, *, html: Optional[str] = None) -> PofCaseMeta:
    source_url = f"{POF_SITE}/article/{slug}"
    page = html if html is not None else _http_get(source_url)
    title_m = re.search(r'property="og:title"\s+content="([^"]+)"', page, flags=re.I)
    desc_m = re.search(r'name="description"\s+content="([^"]+)"', page, flags=re.I)
    h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", page, flags=re.I | re.S)
    title = _strip_tags((title_m.group(1) if title_m else "") or (h1_m.group(1) if h1_m else "") or slug)
    if " — Pass or Fund" in title:
        title = title.split(" — Pass or Fund", 1)[0].strip()
    summary = _strip_tags(desc_m.group(1) if desc_m else "") or title
    company = _company_from_html(page, title)
    published_on = _published_on(slug, page)
    return PofCaseMeta(
        slug=slug,
        company=company,
        title=title,
        summary=summary,
        published_on=published_on,
        source_url=source_url,
        utm_campaign=_utm_campaign(company, published_on),
    )


def _vfa_mcp_credentials() -> Tuple[str, str]:
    url = (os.environ.get("VFA_PASSORFUND_MCP_URL") or os.environ.get("KOF_VFA_MCP_URL") or "").strip()
    token = (os.environ.get("VFA_PASSORFUND_MCP_TOKEN") or os.environ.get("KOF_VFA_MCP_TOKEN") or "").strip()
    if not url or not token:
        raise PassOrFundBlogError(
            "Set VFA_PASSORFUND_MCP_URL and VFA_PASSORFUND_MCP_TOKEN "
            "(Pass or Fund workspace MCP credentials)"
        )
    return url.rstrip("/"), token


def call_vfa_mcp_json(tool: str, arguments: Dict[str, Any], *, timeout: int = 90) -> Dict[str, Any]:
    """JSON-RPC tools/call against the hosted VFA MCP endpoint (URL already ends in /mcp)."""
    url, token = _vfa_mcp_credentials()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise PassOrFundBlogError(f"{tool}: could not reach VC Field Assistant ({exc.__class__.__name__})") from exc
    if resp.status_code >= 400:
        raise PassOrFundBlogError(f"{tool}: HTTP {resp.status_code} from VC Field Assistant")
    try:
        body = resp.json()
    except ValueError as exc:
        raise PassOrFundBlogError(f"{tool}: unexpected non-JSON response") from exc
    if body.get("error"):
        err = body["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise PassOrFundBlogError(f"{tool}: {msg or 'error'}")
    result = body.get("result") or {}
    if result.get("isError"):
        text = "".join(
            c.get("text", "") for c in (result.get("content") or []) if isinstance(c, dict)
        )
        raise PassOrFundBlogError(f"{tool}: {text[:300] or 'error'}")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    text = "".join(
        c.get("text", "") for c in (result.get("content") or []) if isinstance(c, dict)
    ).strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PassOrFundBlogError(f"{tool}: expected JSON object in tool result") from exc
    if not isinstance(data, dict):
        raise PassOrFundBlogError(f"{tool}: expected a JSON object")
    return data


def _company_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("companies", "items", "results"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    if isinstance(payload.get("company"), dict):
        return [payload["company"]]
    return []


def resolve_vfa_company(company_name: str) -> Tuple[str, str]:
    """Return (companyId, companyName) for the Pass or Fund workspace lead."""
    wanted = (company_name or "").strip().lower()
    if not wanted:
        raise PassOrFundBlogError("company name is empty")
    payload = call_vfa_mcp_json("vcfieldassistant_list_companies", {})
    rows = _company_rows(payload)
    exact = [
        r
        for r in rows
        if str(r.get("name") or r.get("companyName") or "").strip().lower() == wanted
    ]
    prefix = [
        r
        for r in rows
        if str(r.get("name") or r.get("companyName") or "").strip().lower().startswith(wanted)
    ]
    pick = (exact or prefix or [None])[0]
    if not pick:
        raise PassOrFundBlogError(
            f"No VC Field Assistant company named {company_name!r} in the Pass or Fund workspace"
        )
    company_id = str(pick.get("id") or pick.get("companyId") or "").strip()
    name = str(pick.get("name") or pick.get("companyName") or company_name).strip()
    if not company_id:
        raise PassOrFundBlogError(f"Company {company_name!r} has no id in list_companies")
    return company_id, name


def memo_sections_from_payload(payload: Dict[str, Any]) -> List[MemoSection]:
    memo = payload.get("memo") if isinstance(payload.get("memo"), dict) else payload
    memo = memo if isinstance(memo, dict) else {}
    sections = [s for s in (memo.get("sections") or []) if isinstance(s, dict)]
    out: List[MemoSection] = []
    for section in sections:
        status = section.get("status")
        if status not in (None, "filled"):
            continue
        body = str(section.get("body") or "").strip()
        if not body:
            continue
        heading = str(section.get("title") or section.get("key") or "Section").strip()
        out.append(MemoSection(heading=heading, body=body))
    if not out:
        raise PassOrFundBlogError("Deal memo has no filled sections yet")
    return out


def fetch_full_deal_memo(company_id: str, company_name: str) -> List[MemoSection]:
    payload = call_vfa_mcp_json(
        "vcfieldassistant_get_company_deal_memo",
        {
            "companyId": company_id,
            "companyName": company_name,
            "format": "full",
        },
    )
    if not payload.get("memo") and not payload.get("sections"):
        raise PassOrFundBlogError(
            f"No deal memo yet for {company_name!r} — guest must generate it in VC Field Assistant"
        )
    return memo_sections_from_payload(payload)


def _ts_string(value: str) -> str:
    """Emit a TypeScript single-quoted string literal."""
    escaped = (
        (value or "")
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )
    return f"'{escaped}'"


def render_case_entry(meta: PofCaseMeta, sections: Sequence[MemoSection], *, indent: str = "  ") -> str:
    lines = [
        f"{indent}{{",
        f"{indent}  slug: {_ts_string(meta.slug)},",
        f"{indent}  company: {_ts_string(meta.company)},",
        f"{indent}  title:",
        f"{indent}    {_ts_string(meta.title)},",
        f"{indent}  publishedOn: {_ts_string(meta.published_on)},",
        f"{indent}  summary: {_ts_string(meta.summary)},",
        f"{indent}  sourceUrl:",
        f"{indent}    {_ts_string(meta.source_url)},",
        f"{indent}  utmCampaign: {_ts_string(meta.utm_campaign)},",
        f"{indent}  memoSections: [",
    ]
    for section in sections:
        lines.append(f"{indent}    {{")
        lines.append(f"{indent}      heading: {_ts_string(section.heading)},")
        lines.append(f"{indent}      body:")
        lines.append(f"{indent}        {_ts_string(section.body)},")
        lines.append(f"{indent}    }},")
    lines.append(f"{indent}  ],")
    lines.append(f"{indent}}},")
    return "\n".join(lines)


_CASES_ARRAY_RE = re.compile(
    r"(export const PASS_OR_FUND_CASES: readonly PassOrFundCase\[\] = \[)(.*?)(\] as const;)",
    re.S,
)


def _iter_top_level_objects(array_body: str) -> List[Tuple[str, int, int]]:
    """Return (slug, start, end) for each top-level `{...},` object in the array body."""
    text = array_body or ""
    out: List[Tuple[str, int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        if text[i] != "{":
            i += 1
            continue
        start = i
        depth = 0
        in_str = False
        quote = ""
        escape = False
        while i < n:
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    in_str = False
                i += 1
                continue
            if ch in ("'", '"', "`"):
                in_str = True
                quote = ch
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    end = i
                    while end < n and text[end] in " \t\r\n":
                        end += 1
                    if end < n and text[end] == ",":
                        end += 1
                    block = text[start:end]
                    slug_m = re.search(r"slug:\s*'([^']+)'", block)
                    slug = slug_m.group(1) if slug_m else ""
                    out.append((slug, start, end))
                    i = end
                    break
            i += 1
        else:
            break
    return out


def upsert_pass_or_fund_cases_ts(existing: str, meta: PofCaseMeta, sections: Sequence[MemoSection]) -> str:
    """Replace or prepend the case object inside PASS_OR_FUND_CASES."""
    match = _CASES_ARRAY_RE.search(existing or "")
    if not match:
        raise PassOrFundBlogError(f"Could not find PASS_OR_FUND_CASES array in {VFA_CASES_PATH}")
    body = match.group(2)
    entry = render_case_entry(meta, sections, indent="  ")
    objects = _iter_top_level_objects(body)
    kept: List[str] = []
    replaced = False
    for slug, start, end in objects:
        if slug == meta.slug:
            kept.append(entry)
            replaced = True
        else:
            kept.append(body[start:end].rstrip())
    if not replaced:
        kept.insert(0, entry)
    new_body = "\n" + "\n".join(kept) + "\n"
    return existing[: match.start(2)] + new_body + existing[match.end(2) :]


def case_already_published(existing: str, slug: str) -> bool:
    return f"slug: '{slug}'" in (existing or "")


def update_case_page_copy(existing: str) -> str:
    """Prefer full-memo wording once the job ships full sections."""
    text = existing or ""
    replacements = (
        ("{caseItem.company}: deal memo excerpt", "{caseItem.company}: deal memo"),
        (
            "Below is the\n            public Fund / Pass framing from{' '}",
            "Below is the guest&apos;s full deal memo from{' '}",
        ),
        (
            "Below is the public Fund / Pass framing from{' '}",
            "Below is the guest&apos;s full deal memo from{' '}",
        ),
        ("<h2>Public excerpt</h2>", "<h2>Deal memo</h2>"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


_ALLOWLIST_RE = re.compile(
    r"(_VFA_MEMO_UTM_BY_SLUG: dict\[str, str\] = \{)(.*?)(\n\})",
    re.S,
)


def upsert_pof_allowlist(existing: str, slug: str, utm_campaign: str) -> str:
    match = _ALLOWLIST_RE.search(existing or "")
    if not match:
        raise PassOrFundBlogError(f"Could not find _VFA_MEMO_UTM_BY_SLUG in {POF_CONFIG_PATH}")
    body = match.group(2)
    line_re = re.compile(rf'^\s*"{re.escape(slug)}"\s*:\s*"[^"]*",?\s*$', re.M)
    new_line = f'    "{slug}": "{utm_campaign}",'
    if line_re.search(body):
        new_body = line_re.sub(new_line, body, count=1)
    else:
        trimmed = body.rstrip()
        if trimmed and not trimmed.endswith(","):
            # last entry may already have a trailing comma; keep body intact
            pass
        new_body = body.rstrip() + "\n" + new_line + "\n"
    return existing[: match.start(2)] + new_body + existing[match.end(2) :]


def resolve_vfa_pr_base() -> str:
    lookup = lookup_board_release_defaults("VFA")
    pr_base = str(lookup.get("pr_base") or "").strip()
    forbidden = {str(x).strip() for x in (lookup.get("forbidden_pr_bases") or []) if str(x).strip()}
    if not pr_base:
        raise PassOrFundBlogError("lookup_board_releases returned no pr_base for VFA")
    if pr_base in forbidden:
        raise PassOrFundBlogError(f"VFA pr_base {pr_base!r} is forbidden (released or PR-locked)")
    if pr_base in ("main", "master"):
        raise PassOrFundBlogError(
            f"VFA pr_base unexpectedly {pr_base!r}; refuse opening a feature content PR onto production"
        )
    return pr_base


def resolve_pof_pr_base(client: GitHubActionsClient) -> str:
    lookup = lookup_board_release_defaults("POF")
    pr_base = str(lookup.get("pr_base") or "").strip()
    forbidden = {str(x).strip() for x in (lookup.get("forbidden_pr_bases") or []) if str(x).strip()}
    if pr_base and pr_base not in forbidden:
        return pr_base
    owner, name = POF_REPO.split("/", 1)
    return client.get_default_branch(owner, name)


def _branch_name(slug: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug).strip("-")[:80] or "case"
    return f"{BRANCH_PREFIX}{safe}"


def open_files_pr(
    *,
    repo: str,
    base_branch: str,
    new_branch_name: str,
    title: str,
    body: str,
    files_to_change: Dict[str, str],
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a branch + PR for content files (not limited to bigas-hotfix/)."""
    if not files_to_change:
        raise PassOrFundBlogError("files_to_change must include at least one file")
    branch = (new_branch_name or "").strip()
    if not branch.startswith(BRANCH_PREFIX):
        raise PassOrFundBlogError(f"new_branch_name must start with {BRANCH_PREFIX}")
    owner, name = repo.split("/", 1)
    client = _github_client(github_token)
    base = (base_branch or "").strip()
    if not base:
        raise PassOrFundBlogError("base_branch is required")

    existing = client.find_open_pull_request(owner, name, head=branch, base=base)
    if existing:
        return {
            "status": "exists",
            "summary": f"Open PR #{existing.get('number')} already covers `{branch}` → `{base}`.",
            "repo": repo,
            "base_branch": base,
            "head_branch": branch,
            "pr_number": existing.get("number"),
            "html_url": (existing.get("html_url") or "").strip(),
            "commit_sha": None,
        }

    try:
        parent_sha = client.get_ref_sha(owner, name, base)
        base_commit = client.get_commit(owner, name, parent_sha)
    except GitHubActionsError as exc:
        raise PassOrFundBlogError(str(exc)) from exc

    base_tree = ((base_commit.get("commit") or {}).get("tree") or {}).get("sha") or ""
    if not base_tree:
        raise PassOrFundBlogError(f"Could not resolve tree for base branch {base}")

    entries: List[Dict[str, str]] = []
    for path, content in files_to_change.items():
        filename = (path or "").strip().lstrip("/")
        if not filename:
            continue
        blob_sha = client.create_blob(owner, name, content)
        entries.append({"path": filename, "mode": "100644", "type": "blob", "sha": blob_sha})
    if not entries:
        raise PassOrFundBlogError("No valid file paths in files_to_change")

    try:
        if client.branch_exists(owner, name, branch):
            # Unique branch if a previous closed PR left the ref behind.
            branch = f"{branch}-{datetime.now(STOCKHOLM).strftime('%Y%m%d%H%M')}"
        tree_sha = client.create_tree(owner, name, base_tree, entries)
        commit_sha = client.create_git_commit(
            owner,
            name,
            message=title.strip() or f"chore: {branch}",
            tree_sha=tree_sha,
            parent_sha=parent_sha,
        )
        client.create_branch_ref(owner, name, branch, commit_sha)
        pr = client.create_pull_request(
            owner,
            name,
            title=title.strip(),
            body=(body or "").strip(),
            head=branch,
            base=base,
        )
    except (GitHubActionsError, DevOpsError) as exc:
        raise PassOrFundBlogError(str(exc)) from exc

    return {
        "status": "ok",
        "summary": f"Opened pull request #{pr.get('number')} from `{branch}` → `{base}`.",
        "repo": repo,
        "base_branch": base,
        "head_branch": branch,
        "pr_number": pr.get("number"),
        "html_url": (pr.get("html_url") or "").strip(),
        "commit_sha": commit_sha,
    }


def _discord_notify(message: str, *, post: bool) -> bool:
    if not post:
        return False
    webhook = (
        (os.environ.get("DISCORD_WEBHOOK_URL_MARKETING") or "").strip()
        or (os.environ.get("DISCORD_WEBHOOK_URL_PRODUCT") or "").strip()
    )
    return post_to_discord(
        webhook,
        message,
        chat_agent_id="marketing",
        channel_hint="marketing",
    )


def should_run_pof_blog_cadence(every_n_weeks: int, *, now: Optional[datetime] = None) -> bool:
    """True on the Stockholm weeks aligned to ``POF_BLOG_CADENCE_EPOCH``."""
    interval = max(1, int(every_n_weeks or 1))
    if interval <= 1:
        return True
    stamp = now or datetime.now(STOCKHOLM)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=STOCKHOLM)
    else:
        stamp = stamp.astimezone(STOCKHOLM)
    weeks_elapsed = (stamp.date() - POF_BLOG_CADENCE_EPOCH).days // 7
    return weeks_elapsed % interval == 0


def publish_passorfund_case_blog(
    *,
    dry_run: bool = False,
    force: bool = False,
    every_n_weeks: int = 2,
    slug: Optional[str] = None,
    post_to_discord_flag: bool = True,
    update_pof_allowlist: bool = True,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Main entry: cadence gate → fetch → memo → PRs → Discord."""
    stamp = now or datetime.now(STOCKHOLM)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=STOCKHOLM)
    else:
        stamp = stamp.astimezone(STOCKHOLM)

    if not force and not should_run_pof_blog_cadence(every_n_weeks, now=stamp):
        weeks_elapsed = (stamp.date() - POF_BLOG_CADENCE_EPOCH).days // 7
        interval = max(1, int(every_n_weeks or 1))
        return {
            "ok": True,
            "status": "skipped_cadence",
            "summary": (
                f"Skipped: cadence every {interval} weeks from {POF_BLOG_CADENCE_EPOCH.isoformat()} "
                f"(weeks_elapsed={weeks_elapsed}). Pass force=true to run anyway."
            ),
            "now": stamp.isoformat(),
        }

    chosen_slug = (slug or "").strip() or list_published_case_slugs()[0]
    meta = fetch_case_meta(chosen_slug)
    company_id, company_name = resolve_vfa_company(meta.company)
    sections = fetch_full_deal_memo(company_id, company_name)

    client = _github_client()
    vfa_owner, vfa_name = VFA_REPO.split("/", 1)
    vfa_base = resolve_vfa_pr_base()
    existing_cases = client.get_file_text(vfa_owner, vfa_name, VFA_CASES_PATH, vfa_base) or ""
    if not existing_cases:
        raise PassOrFundBlogError(f"{VFA_CASES_PATH} missing on {VFA_REPO}@{vfa_base}")

    updated_cases = upsert_pass_or_fund_cases_ts(existing_cases, meta, sections)
    case_page = client.get_file_text(vfa_owner, vfa_name, VFA_CASE_PAGE_PATH, vfa_base) or ""
    updated_page = update_case_page_copy(case_page) if case_page else case_page

    vfa_files: Dict[str, str] = {VFA_CASES_PATH: updated_cases}
    if case_page and updated_page != case_page:
        vfa_files[VFA_CASE_PAGE_PATH] = updated_page

    branch = _branch_name(meta.slug)
    vfa_title = f"{VFA_TICKET_KEY}: Publish Pass or Fund case {meta.company} ({meta.published_on})"
    vfa_body = "\n".join(
        [
            VFA_TICKET_KEY,
            "",
            "## Summary",
            f"- Bigas biweekly job (BIG-220): add/update `/passorfund/{meta.slug}` + `/blog/{meta.slug}`",
            f"- Company: **{meta.company}** (`{company_id}`) — full deal memo ({len(sections)} sections)",
            f"- Source: {meta.source_url}",
            "",
            "## Deploy",
            "1. Review + merge into staging",
            "2. Prepare-deploy / merge to main when ready",
            "3. Deploy VC Field Assistant (Marcus)",
            "4. Merge the companion Pass or Fund allowlist PR before the newsletter CTA goes out",
        ]
    )

    result: Dict[str, Any] = {
        "ok": True,
        "status": "dry_run" if dry_run else "ready",
        "case": {
            "slug": meta.slug,
            "company": meta.company,
            "companyId": company_id,
            "publishedOn": meta.published_on,
            "utmCampaign": meta.utm_campaign,
            "sourceUrl": meta.source_url,
            "sectionCount": len(sections),
            "sectionHeadings": [s.heading for s in sections],
        },
        "vfa_base": vfa_base,
        "dry_run": bool(dry_run),
    }

    if dry_run:
        result["summary"] = (
            f"Dry run: would open VFA PR for {meta.company} ({meta.slug}) "
            f"with {len(sections)} memo sections onto `{vfa_base}`."
        )
        result["preview"] = {
            "vfa_files": list(vfa_files.keys()),
            "branch": branch,
            "cases_changed": updated_cases != existing_cases,
        }
        return result

    if updated_cases == existing_cases and VFA_CASE_PAGE_PATH not in vfa_files:
        result["status"] = "unchanged"
        result["summary"] = (
            f"{meta.company} ({meta.slug}) already has the same content on `{vfa_base}`; no VFA PR opened."
        )
        _discord_notify(
            f"Pass or Fund blog job: `{meta.slug}` already up to date on `{vfa_base}` — nothing to deploy.",
            post=post_to_discord_flag,
        )
        return result

    vfa_pr = open_files_pr(
        repo=VFA_REPO,
        base_branch=vfa_base,
        new_branch_name=branch,
        title=vfa_title,
        body=vfa_body,
        files_to_change=vfa_files,
    )
    result["vfa_pr"] = vfa_pr

    pof_pr = None
    if update_pof_allowlist:
        pof_owner, pof_name = POF_REPO.split("/", 1)
        pof_base = resolve_pof_pr_base(client)
        config_text = client.get_file_text(pof_owner, pof_name, POF_CONFIG_PATH, pof_base) or ""
        if not config_text:
            raise PassOrFundBlogError(f"{POF_CONFIG_PATH} missing on {POF_REPO}@{pof_base}")
        updated_config = upsert_pof_allowlist(config_text, meta.slug, meta.utm_campaign)
        if updated_config != config_text:
            pof_pr = open_files_pr(
                repo=POF_REPO,
                base_branch=pof_base,
                new_branch_name=branch,
                title=f"{POF_TICKET_KEY}: Allow VFA memo CTA for {meta.company}",
                body="\n".join(
                    [
                        POF_TICKET_KEY,
                        "",
                        "## Summary",
                        f"- Add `{meta.slug}` → `{meta.utm_campaign}` to `_VFA_MEMO_UTM_BY_SLUG`",
                        f"- Pair with VFA PR: {vfa_pr.get('html_url') or '(pending)'}",
                        f"- Parent automation: BIG-220 / {VFA_TICKET_KEY}",
                        "",
                        "Merge **after** VC Field Assistant has deployed the case page, "
                        "so the newsletter / site CTA does not 404.",
                    ]
                ),
                files_to_change={POF_CONFIG_PATH: updated_config},
            )
            result["pof_pr"] = pof_pr
            result["pof_base"] = pof_base
        else:
            result["pof_pr"] = {"status": "unchanged", "summary": "Allowlist already contains this slug."}

    vfa_url = (vfa_pr or {}).get("html_url") or ""
    pof_url = (pof_pr or {}).get("html_url") if isinstance(pof_pr, dict) else ""
    lines = [
        f"**Pass or Fund → VFA blog job** ({meta.published_on})",
        f"Case: **{meta.company}** — `{meta.slug}` ({len(sections)} memo sections)",
        f"VFA PR (`{vfa_base}`): {vfa_url or '(none)'}",
    ]
    if pof_url:
        lines.append(f"PoF allowlist PR: {pof_url}")
    lines.append("Review → merge staging → deploy VFA, then merge PoF allowlist before the newsletter.")
    notified = _discord_notify("\n".join(lines), post=post_to_discord_flag)
    result["discord_notified"] = notified
    result["status"] = "ok"
    result["summary"] = (
        f"Opened VFA PR for {meta.company} ({meta.slug}) onto `{vfa_base}`"
        + (f"; PoF allowlist PR ready" if pof_url else "")
        + "."
    )
    return result
