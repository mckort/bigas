"""Regression tests for GA4 page URL extraction used by underperforming analysis."""

from bigas.resources.marketing.storage_service import StorageService


def _storage_service() -> StorageService:
    """Build a StorageService without connecting to GCS."""
    return StorageService.__new__(StorageService)


def test_extracts_pages_with_zero_key_events():
    """Pages with 0 key events must still be extracted (they are the underperforming ones)."""
    raw_data = {
        "dimension_headers": ["pagePath", "hostName"],
        "metric_headers": ["sessions", "keyEvents"],
        "rows": [
            {
                "dimension_values": ["/store", "store.example.com"],
                "metric_values": ["20", "0"],
                "underperforming": True,
            },
            {
                "dimension_values": ["/", "www.example.com"],
                "metric_values": ["15", "0"],
                "underperforming": True,
            },
            {
                "dimension_values": ["/blog", "www.example.com"],
                "metric_values": ["5", "1"],
                "underperforming": False,
            },
        ],
    }

    pages = _storage_service()._extract_page_urls_from_raw_data(raw_data)

    assert len(pages) == 3
    underperforming = [p for p in pages if p["is_underperforming"]]
    assert len(underperforming) == 2
    assert underperforming[0]["page_url"] == "https://store.example.com/store"
    assert underperforming[0]["sessions"] == 20
    assert underperforming[0]["key_events"] == 0
    assert underperforming[0]["conversions"] == 0

    converting = pages[2]
    assert converting["page_url"] == "https://www.example.com/blog"
    assert converting["key_events"] == 1
    assert converting["conversions"] == 1  # mirrored from keyEvents


def test_extracts_pages_with_conversions_metric():
    raw_data = {
        "dimension_headers": ["pagePath", "hostName"],
        "metric_headers": ["sessions", "conversions"],
        "rows": [
            {
                "dimension_values": ["/landing", "www.example.com"],
                "metric_values": ["40", "0"],
                "underperforming": True,
            },
        ],
    }

    pages = _storage_service()._extract_page_urls_from_raw_data(raw_data)

    assert len(pages) == 1
    assert pages[0]["is_underperforming"] is True
    assert pages[0]["conversions"] == 0
    assert pages[0]["page_url"] == "https://www.example.com/landing"
