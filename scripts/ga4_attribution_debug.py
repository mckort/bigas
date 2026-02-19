#!/usr/bin/env python3
"""
GA4 Attribution debug script: run report requests directly and print responses/errors.
Use this to see how the GA4 API responds and why attribution might fail.

Run from repo root (so .env is found):
  python scripts/ga4_attribution_debug.py

Requires:
  - GA4_PROPERTY_ID in .env (e.g. 473559548 or properties/473559548).
  - Google credentials with Analytics Data API access:
      - Local: Use a service account JSON key added to your GA4 property, then
        export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
      - Or: gcloud auth application-default login (user ADC may lack analytics scope).
  - The service account or user must have "Viewer" (or higher) on the GA4 property.

What the script does:
  1. Minimal report (totalUsers) – checks property access.
  2. Channel groups without filter – lists firstUserDefaultChannelGroup + firstUserSource.
  3. Paid Social only (filter) – same as cross-platform attribution, without conversions.
  4. Paid Social + conversions – full attribution request.
  5. Session scope (sessionDefaultChannelGroup + sessionSource) – alternative if firstUser* fails.

If you see 403 ACCESS_TOKEN_SCOPE_INSUFFICIENT: use a service account key with access to the
GA4 property. If you see invalid metric/dimension errors: the script output shows which
request failed so you can adjust the API query in the app.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

# Load .env from repo root (parent of scripts/)
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.isfile(os.path.join(_repo_root, ".env")):
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_repo_root, ".env"))

def main() -> None:
    property_id = (os.environ.get("GA4_PROPERTY_ID") or "").strip()
    if not property_id:
        print("ERROR: GA4_PROPERTY_ID not set in environment. Add it to .env or export it.")
        sys.exit(1)
    if not property_id.startswith("properties/"):
        property_id = f"properties/{property_id}"

    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            RunReportRequest,
            Filter,
            FilterExpression,
        )
    except ImportError as e:
        print(f"ERROR: Install GA4 deps: pip install google-analytics-data. {e}")
        sys.exit(1)

    # Initialize client (ADC or GOOGLE_APPLICATION_CREDENTIALS)
    try:
        client = BetaAnalyticsDataClient()
    except Exception as e:
        print(f"ERROR: Failed to create GA4 client: {e}")
        print("  Ensure: gcloud auth application-default login (or set GOOGLE_APPLICATION_CREDENTIALS)")
        sys.exit(1)

    end_date = date.today()
    start_date = end_date - timedelta(days=30)
    start_s = start_date.isoformat()
    end_s = end_date.isoformat()
    print(f"Date range: {start_s} to {end_s}")
    print(f"Property:  {property_id}")
    print()

    def run(name: str, metrics: list[str], dimensions: list[str], dimension_filter: FilterExpression | None = None, limit: int | None = 50) -> None:
        print(f"--- Request: {name} ---")
        req = RunReportRequest(
            property=property_id,
            date_ranges=[DateRange(start_date=start_s, end_date=end_s)],
            metrics=[Metric(name=m) for m in metrics],
            dimensions=[Dimension(name=d) for d in dimensions],
            dimension_filter=dimension_filter,
            limit=limit,
        )
        try:
            resp = client.run_report(req, timeout=30)
            rows = getattr(resp, "rows", []) or []
            print(f"  Rows returned: {len(rows)}")
            if resp.dimension_headers:
                dims = [h.name for h in resp.dimension_headers]
                print(f"  Dimensions: {dims}")
            if resp.metric_headers:
                mets = [h.name for h in resp.metric_headers]
                print(f"  Metrics:    {mets}")
            for i, row in enumerate(rows[:10]):
                dim_vals = [dv.value for dv in row.dimension_values]
                met_vals = [mv.value for mv in row.metric_values]
                print(f"  Row {i+1}: dims={dim_vals} metrics={met_vals}")
            if len(rows) > 10:
                print(f"  ... and {len(rows) - 10} more rows")
        except Exception as e:
            print(f"  EXCEPTION: {type(e).__name__}: {e}")
        print()

    # 1) Minimal: can we reach the property at all?
    run("1. Minimal (totalUsers, no dimensions)", metrics=["totalUsers"], dimensions=[])

    # 2) Which channel groups do we have? (no filter)
    run(
        "2. Channel groups (firstUserDefaultChannelGroup + firstUserSource, no filter)",
        metrics=["eventCount", "activeUsers"],
        dimensions=["firstUserDefaultChannelGroup", "firstUserSource"],
    )

    # 3) Paid Social only (filter) – same as production attribution
    paid_social_filter = FilterExpression(
        filter=Filter(
            field_name="firstUserDefaultChannelGroup",
            string_filter=Filter.StringFilter(
                value="Paid Social",
                match_type=Filter.StringFilter.MatchType.EXACT,
            ),
        ),
    )
    run(
        "3. Paid Social only (filter firstUserDefaultChannelGroup = Paid Social)",
        metrics=["eventCount", "activeUsers"],
        dimensions=["firstUserDefaultChannelGroup", "firstUserSource"],
        dimension_filter=paid_social_filter,
    )

    # 4) Add conversions metric (Key Events)
    run(
        "4. Paid Social + conversions (Key Events)",
        metrics=["eventCount", "activeUsers", "conversions"],
        dimensions=["firstUserDefaultChannelGroup", "firstUserSource"],
        dimension_filter=paid_social_filter,
    )

    # 5) Try with sessionSource instead of firstUserSource (in case firstUser* not available)
    run(
        "5. Session scope (sessionDefaultChannelGroup + sessionSource, filter Paid Social)",
        metrics=["eventCount", "activeUsers"],
        dimensions=["sessionDefaultChannelGroup", "sessionSource"],
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="sessionDefaultChannelGroup",
                string_filter=Filter.StringFilter(
                    value="Paid Social",
                    match_type=Filter.StringFilter.MatchType.EXACT,
                ),
            ),
        ),
    )

    print("Done. Use the output above to see which request fails and what the API returns.")

if __name__ == "__main__":
    main()
