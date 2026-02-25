import os
from datetime import date
from typing import List

from bigas.providers.ads.base import AdsProvider, CampaignMetrics
from bigas.resources.marketing.meta_ads_service import MetaAdsService


class MetaAdsProvider(AdsProvider):
    name = "meta"
    display_name = "Meta Ads"

    @classmethod
    def is_configured(cls) -> bool:
        return bool(os.getenv("META_ACCESS_TOKEN"))

    def __init__(self) -> None:
        self._service = MetaAdsService()

    def get_campaign_performance(self, start_date: str, end_date: str) -> List[CampaignMetrics]:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        account_id = os.environ.get("META_AD_ACCOUNT_ID") or ""
        if not account_id:
            # Without a default account we cannot query; return empty list.
            return []

        raw_rows = self._service.get_campaign_insights(
            account_id=account_id,
            start_date=start,
            end_date=end,
            level="campaign",
        )
        normalized = MetaAdsService.normalize_campaign_daily_rows(raw_rows, level="campaign")
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
            currency = (metrics.get("currency") or normalized.get("summary", {}).get("currency") or "USD")

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

