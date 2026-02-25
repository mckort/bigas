import os
from datetime import date
from typing import List

from bigas.providers.ads.base import AdsProvider, CampaignMetrics
from bigas.resources.marketing.google_ads_service import GoogleAdsService


class GoogleAdsProvider(AdsProvider):
    name = "google_ads"
    display_name = "Google Ads"

    @classmethod
    def is_configured(cls) -> bool:
        # Google Ads uses ADC plus a developer token; login_customer_id is optional.
        return bool(os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN"))

    def __init__(self) -> None:
        self._service = GoogleAdsService()

    def get_campaign_performance(self, start_date: str, end_date: str) -> List[CampaignMetrics]:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        customer_id = os.environ.get("GOOGLE_ADS_CUSTOMER_ID") or ""
        if not customer_id:
            # As a fallback, try list_accessible_customers and pick the first.
            customers = self._service.list_accessible_customers()
            customer_id = customers[0].split("/")[-1] if customers else ""
        if not customer_id:
            return []

        query = GoogleAdsService.build_campaign_daily_performance_query(start=start, end=end)
        raw_rows, _chunks = self._service.search_stream(customer_id=customer_id, query=query)
        normalized = GoogleAdsService.normalize_campaign_daily_rows(raw_rows, report_level="campaign")
        rows = normalized.get("rows") or []

        out: List[CampaignMetrics] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            metrics = row.get("metrics") or {}
            campaign_id = str(row.get("campaign_id") or "")
            campaign_name = str(row.get("campaign_name") or "")
            impressions = int(metrics.get("impressions") or 0)
            clicks = int(metrics.get("clicks") or 0)
            spend = float(metrics.get("cost") or 0.0)
            currency = (row.get("currency_code") or normalized.get("summary", {}).get("currency") or "USD")

            out.append(
                CampaignMetrics(
                    campaign_id=campaign_id,
                    campaign_name=campaign_name,
                    impressions=impressions,
                    clicks=clicks,
                    spend=spend,
                    currency=currency,
                )
            )

        return out

    def get_account_summary(self, start_date: str, end_date: str) -> dict:
        campaigns = self.get_campaign_performance(start_date, end_date)
        currency = campaigns[0].currency if campaigns else None
        return {
            "total_spend": sum(c.spend for c in campaigns),
            "total_clicks": sum(c.clicks for c in campaigns),
            "total_impressions": sum(c.impressions for c in campaigns),
            "currency": currency,
        }

