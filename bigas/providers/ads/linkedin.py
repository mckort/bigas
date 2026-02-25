import os
from datetime import date
from typing import List

from bigas.providers.ads.base import AdsProvider, CampaignMetrics
from bigas.resources.marketing.linkedin_ads_service import LinkedInAdsService


class LinkedInAdsProvider(AdsProvider):
    name = "linkedin"
    display_name = "LinkedIn Ads"

    @classmethod
    def is_configured(cls) -> bool:
        return all(
            [
                os.getenv("LINKEDIN_CLIENT_ID"),
                os.getenv("LINKEDIN_CLIENT_SECRET"),
                os.getenv("LINKEDIN_REFRESH_TOKEN"),
            ]
        )

    def __init__(self) -> None:
        # Delegate auth/env handling to existing service
        self._service = LinkedInAdsService()

    def get_campaign_performance(self, start_date: str, end_date: str) -> List[CampaignMetrics]:
        # Use statistics finder with campaign pivot to get per-campaign rows
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        # For now, assume accounts are discovered externally; a real implementation
        # could accept account URNs via config or env.
        # Here we fallback to listing accounts and using all of them.
        accounts_resp = self._service.list_ad_accounts()
        elements = accounts_resp.get("elements") or accounts_resp.get("data") or []
        account_urns = [e.get("id") or e.get("account") or e.get("resource") for e in elements if isinstance(e, dict)]
        account_urns = [u for u in account_urns if u]
        if not account_urns:
            return []

        raw = self._service.ad_analytics_statistics(
            start_date=start,
            end_date=end,
            time_granularity="DAILY",
            pivots=["CAMPAIGN"],
            account_urns=account_urns,
            fields=["IMPRESSIONS", "CLICKS", "SPEND", "CAMPAIGN_NAME"],
        )
        rows = raw.get("elements") or raw.get("results") or raw.get("rows") or []

        out: List[CampaignMetrics] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            metrics = row.get("metrics") or row
            campaign = row.get("campaign") or {}
            impressions = int(metrics.get("impressions", 0) or 0)
            clicks = int(metrics.get("clicks", 0) or 0)
            spend = float(metrics.get("costInLocalCurrency", 0.0) or metrics.get("spend", 0.0) or 0.0)
            currency = metrics.get("currencyCode") or metrics.get("currency") or ""

            out.append(
                CampaignMetrics(
                    campaign_id=str(campaign.get("id") or row.get("campaignId") or ""),
                    campaign_name=str(campaign.get("name") or row.get("campaignName") or ""),
                    impressions=impressions,
                    clicks=clicks,
                    spend=spend,
                    currency=currency or "USD",
                )
            )

        return out

    def get_account_summary(self, start_date: str, end_date: str) -> dict:
        campaigns = self.get_campaign_performance(start_date, end_date)
        return {
            "total_spend": sum(c.spend for c in campaigns),
            "total_clicks": sum(c.clicks for c in campaigns),
            "total_impressions": sum(c.impressions for c in campaigns),
            "currency": campaigns[0].currency if campaigns else None,
        }

