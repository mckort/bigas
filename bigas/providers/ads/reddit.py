import os
from datetime import date
from typing import List

from bigas.providers.ads.base import AdsProvider, CampaignMetrics
from bigas.resources.marketing.reddit_ads_service import RedditAdsService


class RedditAdsProvider(AdsProvider):
    name = "reddit"
    display_name = "Reddit Ads"

    @classmethod
    def is_configured(cls) -> bool:
        return all(
            [
                os.getenv("REDDIT_CLIENT_ID"),
                os.getenv("REDDIT_CLIENT_SECRET"),
                os.getenv("REDDIT_REFRESH_TOKEN"),
            ]
        )

    def __init__(self) -> None:
        self._service = RedditAdsService()

    def get_campaign_performance(self, start_date: str, end_date: str) -> List[CampaignMetrics]:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        # Use default account logic from the service if REDDIT_AD_ACCOUNT_ID is set
        account_id = os.environ.get("REDDIT_AD_ACCOUNT_ID") or ""
        if not account_id:
            # Try to derive at least one account from list_ad_accounts
            accounts = self._service.list_ad_accounts()
            data = accounts.get("data") or accounts.get("ad_accounts") or []
            first = next((a for a in data if isinstance(a, dict)), None)
            account_id = str(first.get("id")) if first and first.get("id") else ""
        if not account_id:
            return []

        report = self._service.get_performance_report(
            account_id=account_id,
            start_date=start,
            end_date=end,
        )
        rows = report.get("data") or []

        out: List[CampaignMetrics] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            campaign_id = str(row.get("campaign_id") or "")
            campaign_name = str(row.get("campaign_name") or "")
            impressions = int(row.get("impressions") or 0)
            clicks = int(row.get("clicks") or 0)
            spend = float(row.get("spend") or 0.0)
            currency = str(row.get("currency") or "USD")

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

