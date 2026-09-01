"""Unit tests for GA4 page URL extraction used by underperforming-page analysis."""

from __future__ import annotations

from bigas.resources.marketing.storage_service import StorageService


def _extract(raw_data):
    return StorageService._extract_page_urls_from_raw_data(raw_data)


def test_extracts_zero_key_event_pages():
    pages = _extract(
        {
            "dimension_headers": ["pagePath", "hostName"],
            "metric_headers": ["sessions", "keyEvents"],
            "rows": [
                {
                    "dimension_values": ["/", "greenpromowear.com"],
                    "metric_values": ["39", "0"],
                    "underperforming": True,
                },
                {
                    "dimension_values": ["/about", "greenpromowear.com"],
                    "metric_values": ["10", "1"],
                    "underperforming": False,
                },
            ],
        }
    )

    assert len(pages) == 2
    home = pages[0]
    assert home["page_url"] == "https://greenpromowear.com/"
    assert home["sessions"] == 39
    assert home["key_events"] == 0
    assert home["conversions"] == 0
    assert home["is_underperforming"] is True
    assert pages[1]["conversions"] == 1
    assert pages[1]["is_underperforming"] is False
