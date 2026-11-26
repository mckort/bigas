"""Google Ads portfolio report: GAQL build and REST row normalization (camelCase)."""
from __future__ import annotations

from datetime import date

import pytest


def test_build_performance_query_includes_ad_and_currency_fields():
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    start = date(2026, 10, 23)
    end = date(2026, 11, 21)
    q_campaign = GoogleAdsService.build_performance_query(start=start, end=end, report_level="campaign")
    assert "customer.currency_code" in q_campaign
    assert "metrics.cost_micros" in q_campaign
    assert "FROM campaign" in q_campaign

    q_ad = GoogleAdsService.build_performance_query(
        start=start, end=end, report_level="ad", include_optional_reach_metrics=False
    )
    assert "FROM ad_group_ad" in q_ad
    assert "ad_group.id" in q_ad
    assert "ad_group_ad.ad.id" in q_ad
    assert "ad_group_ad.ad.responsive_search_ad.headlines" in q_ad


def test_flatten_campaign_row_camel_case_cost_and_currency():
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    row = {
        "segments": {"date": "2026-11-01"},
        "campaign": {"id": "111", "name": "Julklapp anställda – Sök SE 2026"},
        "customer": {"currencyCode": "SEK"},
        "metrics": {
            "impressions": "260",
            "clicks": "12",
            "costMicros": "45230000",
            "conversions": "0",
            "conversionsValue": "0",
        },
    }
    flat = GoogleAdsService._flatten_campaign_daily_row(row, report_level="campaign")
    assert flat["currency_code"] == "SEK"
    assert flat["metrics"]["cost"] == 45.23
    assert flat["derived"]["cpc"] == pytest.approx(45.23 / 12, rel=1e-3)
    assert flat["metrics"]["clicks"] == 12
    assert flat["metrics"]["impressions"] == 260


def test_flatten_ad_level_row_camel_case_hierarchy_and_rsa_name():
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    row = {
        "segments": {"date": "2026-11-01"},
        "campaign": {"id": "111", "name": "Search SE"},
        "customer": {"currencyCode": "SEK"},
        "adGroup": {"id": "222", "name": "Ad group SE"},
        "adGroupAd": {
            "ad": {
                "id": "333",
                "responsiveSearchAd": {
                    "headlines": [{"text": "Employee gift ideas"}, {"text": "Second headline"}],
                },
            }
        },
        "metrics": {
            "impressions": "100",
            "clicks": "5",
            "costMicros": "2500000",
        },
    }
    flat = GoogleAdsService._flatten_campaign_daily_row(row, report_level="ad")
    assert flat["ad_group_id"] == "222"
    assert flat["ad_group_name"] == "Ad group SE"
    assert flat["ad_id"] == "333"
    assert flat["ad_name"] == "Employee gift ideas"
    assert flat["metrics"]["cost"] == 2.5


def test_flatten_ad_name_fallback_to_ad_id():
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    row = {
        "segments": {"date": "2026-11-01"},
        "campaign": {"id": "1", "name": "C"},
        "customer": {"currencyCode": "USD"},
        "adGroup": {"id": "2", "name": "G"},
        "adGroupAd": {"ad": {"id": "999"}},
        "metrics": {"impressions": "1", "clicks": "0", "costMicros": "0"},
    }
    flat = GoogleAdsService._flatten_campaign_daily_row(row, report_level="ad")
    assert flat["ad_name"] == "Ad 999"
    assert flat["derived"]["cpc"] == 0.0


def test_normalize_campaign_daily_rows_aggregates_currency_from_rows():
    from bigas.resources.marketing.google_ads_service import GoogleAdsService

    rows = [
        {
            "segments": {"date": "2026-11-01"},
            "campaign": {"id": "1", "name": "C"},
            "customer": {"currencyCode": "EUR"},
            "metrics": {"impressions": "10", "clicks": "2", "costMicros": "1000000"},
        },
        {
            "segments": {"date": "2026-11-02"},
            "campaign": {"id": "1", "name": "C"},
            "customer": {"currencyCode": "EUR"},
            "metrics": {"impressions": "20", "clicks": "4", "costMicros": "2000000"},
        },
    ]
    out = GoogleAdsService.normalize_campaign_daily_rows(rows, report_level="campaign")
    assert out["summary"]["currency"] == "EUR"
    assert out["summary"]["total_cost"] == 3.0
    assert out["summary"]["total_clicks"] == 6
    assert out["summary"]["cpc"] == pytest.approx(0.5)
