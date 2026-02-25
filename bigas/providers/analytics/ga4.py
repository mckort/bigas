from datetime import date
from typing import List

from bigas.providers.analytics.base import AnalyticsProvider, PageMetrics
from bigas.resources.marketing.ga4_service import GA4Service


class GA4AnalyticsProvider(AnalyticsProvider):
    name = "ga4"
    display_name = "Google Analytics 4"

    @classmethod
    def is_configured(cls) -> bool:
        # GA4 requires a property ID; in standalone mode GA4_PROPERTY_ID is mandatory,
        # while in SaaS mode it is provided per-request. For discovery we only check
        # the global env; SaaS flows can construct providers differently later.
        from os import getenv

        return bool(getenv("GA4_PROPERTY_ID"))

    def __init__(self) -> None:
        self._service = GA4Service()

    def _ensure_property_id(self) -> str:
        from os import getenv

        prop = getenv("GA4_PROPERTY_ID")
        if not prop:
            raise ValueError("GA4_PROPERTY_ID must be set to use GA4AnalyticsProvider.")
        return prop

    def get_overview(self, start_date: str, end_date: str) -> dict:
        prop = self._ensure_property_id()
        ga4 = self._service

        template = {
            "metrics": ["sessions", "totalUsers", "screenPageViews"],
            "dimensions": ["date"],
        }
        date_range = {"start_date": start_date, "end_date": end_date}
        result = ga4.run_template_query(property_id=prop, template=template, date_range=date_range)

        rows = result.get("rows", [])
        total_sessions = sum(int(r.get("sessions", 0)) for r in rows if isinstance(r, dict))
        total_users = sum(int(r.get("totalUsers", 0)) for r in rows if isinstance(r, dict))
        total_pageviews = sum(int(r.get("screenPageViews", 0)) for r in rows if isinstance(r, dict))

        return {
            "property_id": prop,
            "start_date": start_date,
            "end_date": end_date,
            "sessions": total_sessions,
            "users": total_users,
            "pageviews": total_pageviews,
        }

    def get_top_pages(self, start_date: str, end_date: str, limit: int = 10) -> List[PageMetrics]:
        prop = self._ensure_property_id()
        ga4 = self._service

        template = {
            "metrics": ["screenPageViews"],
            "dimensions": ["pagePath"],
            "order_by": [{"field": "screenPageViews", "direction": "DESCENDING"}],
            "limit": limit,
        }
        date_range = {"start_date": start_date, "end_date": end_date}
        result = ga4.run_template_query(property_id=prop, template=template, date_range=date_range)

        pages: List[PageMetrics] = []
        for row in result.get("rows", []):
            if not isinstance(row, dict):
                continue
            path = str(row.get("pagePath") or row.get("path") or "/")
            pageviews = int(row.get("screenPageViews") or row.get("pageviews") or 0)
            sessions = int(row.get("sessions") or 0)
            pages.append(
                PageMetrics(
                    path=path,
                    sessions=sessions,
                    pageviews=pageviews,
                )
            )
        return pages

