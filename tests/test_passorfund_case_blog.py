"""Unit tests for the biweekly Pass or Fund → VFA blog PR job."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bigas.resources.marketing.passorfund_case_blog import (
    POF_BLOG_CADENCE_EPOCH,
    MemoSection,
    PofCaseMeta,
    fetch_case_meta,
    list_published_case_slugs,
    memo_sections_from_payload,
    render_case_entry,
    should_run_pof_blog_cadence,
    update_case_page_copy,
    upsert_pass_or_fund_cases_ts,
    upsert_pof_allowlist,
)

STOCKHOLM = ZoneInfo("Europe/Stockholm")

SAMPLE_HOME = """
<html><body>
<a href="/article/2026-11-18-treat-raised-3m-to-replace-the-gp-appointment-in-a-country-t">Treat</a>
<a href="/article/2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho">Magic</a>
</body></html>
"""

SAMPLE_ARTICLE = """
<html><head>
<meta property="og:title" content="Treat raises €3 million pre-seed to test whether a resolution-based primary care model can win Nordic employer budgets">
<meta name="description" content="Swappie founders bet €3M on AI primary care winning Nordic employer contracts.">
<meta property="article:published_time" content="2026-11-22T14:47:59.155093+00:00">
</head><body>
<span class="tag-pill bg-gray-100"> Treat — €3M Pre-seed </span>
<h1>Treat raises €3 million</h1>
</body></html>
"""

SAMPLE_CASES_TS = """/**
 * Republished Pass or Fund cases
 */
export type PassOrFundMemoSection = {
  heading: string;
  body: string;
};

export type PassOrFundCase = {
  slug: string;
  company: string;
  title: string;
  publishedOn: string;
  summary: string;
  sourceUrl: string;
  utmCampaign: string;
  memoSections: readonly PassOrFundMemoSection[];
};

export const PASS_OR_FUND_CASES: readonly PassOrFundCase[] = [
  {
    slug: '2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho',
    company: 'Magic Vision',
    title: 'Magic Vision raised €600K',
    publishedOn: '2026-11-03',
    summary: 'Italian startup monitors care home residents.',
    sourceUrl:
      'https://passorfund.com/article/2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho',
    utmCampaign: 'pof-magic-vision-2026-11-03',
    memoSections: [
      {
        heading: 'The case for Fund',
        body: 'Early progress.',
      },
    ],
  },
] as const;

export function passOrFundCaseBySlug(slug: string): PassOrFundCase | undefined {
  return PASS_OR_FUND_CASES.find((c) => c.slug === slug);
}
"""

SAMPLE_ALLOWLIST = '''
_VFA_MEMO_UTM_BY_SLUG: dict[str, str] = {
    "2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho": "pof-magic-vision-2026-11-03",
}
'''


def test_list_published_case_slugs_orders_newest_first():
    slugs = list_published_case_slugs(SAMPLE_HOME)
    assert slugs[0].startswith("2026-11-18-treat")
    assert "2026-11-03-magic" in slugs[1]


def test_list_published_case_slugs_accepts_quotes_and_absolute_urls():
    html = """
    <a href='/article/2026-11-18-treat-raised-3m'>Treat</a>
    <a href="https://passorfund.com/article/2026-11-03-magic-vision">Magic</a>
    <a href='https://www.passorfund.com/article/2026-10-01-other-case'>Other</a>
    """
    slugs = list_published_case_slugs(html)
    assert slugs == [
        "2026-11-18-treat-raised-3m",
        "2026-11-03-magic-vision",
        "2026-10-01-other-case",
    ]


def test_fetch_case_meta_parses_company_and_utm():
    meta = fetch_case_meta(
        "2026-11-18-treat-raised-3m-to-replace-the-gp-appointment-in-a-country-t",
        html=SAMPLE_ARTICLE,
    )
    assert meta.company == "Treat"
    assert meta.published_on == "2026-11-18"
    assert meta.utm_campaign == "pof-treat-2026-11-18"
    assert "Swappie" in meta.summary


def test_fetch_case_meta_keeps_multi_word_company_without_tag_pill():
    html = """
    <html><head>
    <meta property="og:title" content="Magic Vision raised €600K to put computer vision into care homes">
    <meta name="description" content="Italian startup monitors care home residents.">
    </head><body><h1>Magic Vision raised €600K</h1></body></html>
    """
    meta = fetch_case_meta("2026-11-03-magic-vision", html=html)
    assert meta.company == "Magic Vision"
    bare = """
    <html><head>
    <meta property="og:title" content="Magic Vision">
    <meta name="description" content="Italian startup monitors care home residents.">
    </head><body><h1>Magic Vision</h1></body></html>
    """
    assert fetch_case_meta("2026-11-03-magic-vision", html=bare).company == "Magic Vision"


def test_memo_sections_from_payload_keeps_filled_only():
    sections = memo_sections_from_payload(
        {
            "memo": {
                "sections": [
                    {"key": "team", "title": "Team", "status": "filled", "body": "Strong."},
                    {"key": "empty", "title": "Empty", "status": "filled", "body": "  "},
                    {"key": "skip", "title": "Skip", "status": "todo", "body": "Nope."},
                ]
            }
        }
    )
    assert sections == [MemoSection(heading="Team", body="Strong.")]


def test_upsert_pass_or_fund_cases_ts_prepends_new_case():
    meta = PofCaseMeta(
        slug="2026-11-18-treat-raised-3m-to-replace-the-gp-appointment-in-a-country-t",
        company="Treat",
        title="Treat raises €3 million",
        summary="Summary",
        published_on="2026-11-18",
        source_url="https://passorfund.com/article/2026-11-18-treat-raised-3m-to-replace-the-gp-appointment-in-a-country-t",
        utm_campaign="pof-treat-2026-11-18",
    )
    updated = upsert_pass_or_fund_cases_ts(
        SAMPLE_CASES_TS,
        meta,
        [MemoSection(heading="Thesis", body="Full memo body with 'quotes'.")],
    )
    assert "slug: '2026-11-18-treat" in updated
    assert updated.index("2026-11-18-treat") < updated.index("2026-11-03-magic")
    assert "Full memo body with \\'quotes\\'." in updated
    # Existing Magic Vision case preserved, including its opening indent
    assert "Magic Vision" in updated
    assert "\n  {\n    slug: '2026-11-03-magic" in updated
    assert "\n{\n    slug: '2026-11-03-magic" not in updated


def test_upsert_pass_or_fund_cases_ts_replaces_existing_slug():
    meta = PofCaseMeta(
        slug="2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho",
        company="Magic Vision",
        title="Magic Vision updated title",
        summary="New summary",
        published_on="2026-11-03",
        source_url="https://passorfund.com/article/2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho",
        utm_campaign="pof-magic-vision-2026-11-03",
    )
    updated = upsert_pass_or_fund_cases_ts(
        SAMPLE_CASES_TS,
        meta,
        [MemoSection(heading="Full", body="Entire deal memo.")],
    )
    assert updated.count("slug: '2026-11-03-magic") == 1
    assert "Entire deal memo." in updated
    assert "Early progress." not in updated


def test_upsert_pass_or_fund_cases_ts_replaces_double_quoted_slug():
    existing = SAMPLE_CASES_TS.replace(
        "slug: '2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho',",
        'slug: "2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho",',
    )
    meta = PofCaseMeta(
        slug="2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho",
        company="Magic Vision",
        title="Magic Vision updated title",
        summary="New summary",
        published_on="2026-11-03",
        source_url="https://passorfund.com/article/2026-11-03-magic-vision-raised-600k-to-put-computer-vision-into-care-ho",
        utm_campaign="pof-magic-vision-2026-11-03",
    )
    updated = upsert_pass_or_fund_cases_ts(
        existing,
        meta,
        [MemoSection(heading="Full", body="Entire deal memo.")],
    )
    assert 'slug: "2026-11-03-magic' not in updated
    assert updated.count("slug: '2026-11-03-magic") == 1
    assert "Entire deal memo." in updated
    assert "Early progress." not in updated


def test_upsert_pof_allowlist_adds_and_updates():
    slug = "2026-11-18-treat-raised-3m-to-replace-the-gp-appointment-in-a-country-t"
    once = upsert_pof_allowlist(SAMPLE_ALLOWLIST, slug, "pof-treat-2026-11-18")
    assert f'"{slug}": "pof-treat-2026-11-18"' in once
    assert "\n\n}" not in once
    twice = upsert_pof_allowlist(once, slug, "pof-treat-2026-11-18-b")
    assert twice.count(slug) == 1
    assert "pof-treat-2026-11-18-b" in twice


def test_update_case_page_copy_full_memo_wording():
    src = (
        "<h1>{caseItem.company}: deal memo excerpt</h1>\n"
        "Below is the\n            public Fund / Pass framing from{' '}\n"
        "<h2>Public excerpt</h2>"
    )
    out = update_case_page_copy(src)
    assert "deal memo excerpt" not in out
    assert "full deal memo" in out
    assert "Public excerpt" not in out


def test_should_run_pof_blog_cadence_biweekly_from_dec_4():
    assert POF_BLOG_CADENCE_EPOCH.isoformat() == "2026-12-04"
    assert should_run_pof_blog_cadence(
        2, now=datetime(2026, 12, 4, 9, 0, tzinfo=STOCKHOLM)
    )
    assert not should_run_pof_blog_cadence(
        2, now=datetime(2026, 12, 11, 9, 0, tzinfo=STOCKHOLM)
    )
    assert should_run_pof_blog_cadence(
        2, now=datetime(2026, 12, 18, 9, 0, tzinfo=STOCKHOLM)
    )


def test_render_case_entry_escapes_newlines():
    meta = PofCaseMeta(
        slug="s",
        company="C",
        title="T",
        summary="S",
        published_on="2026-12-04",
        source_url="https://example.com",
        utm_campaign="pof-c-2026-12-04",
    )
    rendered = render_case_entry(meta, [MemoSection(heading="H", body="line1\nline2")])
    assert "line1\\nline2" in rendered


def test_publish_skips_off_cadence(monkeypatch):
    from bigas.resources.marketing import passorfund_case_blog as mod

    result = mod.publish_passorfund_case_blog(
        every_n_weeks=2,
        force=False,
        now=datetime(2026, 12, 11, 9, 0, tzinfo=STOCKHOLM),
    )
    assert result["status"] == "skipped_cadence"
    assert result["ok"] is True


def test_publish_endpoint_rejects_non_numeric_every_n_weeks():
    from flask import Flask

    from bigas.resources.marketing.endpoints import marketing_bp

    app = Flask(__name__)
    app.register_blueprint(marketing_bp)
    resp = app.test_client().post(
        "/mcp/tools/publish_passorfund_case_blog",
        json={"every_n_weeks": "two"},
    )
    assert resp.status_code == 400
    body = resp.get_json() or {}
    assert body.get("ok") is False
    assert "every_n_weeks" in (body.get("error") or "")
