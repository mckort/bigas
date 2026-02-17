# Reddit audience vs UI discrepancy – analysis and proposals

## Comparison reference (time window)

- **Last 7 days (from report date):** In the Reddit Ads Manager UI, **News & Education** shows **19 clicks**. When comparing API results to the UI, use this as the reference for the interest "News & Education" (same date range and campaign/account scope).

## Test setup (2026-02-09 to 2026-02-15)

- **Endpoint:** `POST /mcp/tools/run_reddit_portfolio_report` with `{"debug_audience": true}`.
- **Result:** `audience_scope: account`, `top_campaign_id: None`.

### Debug run (with new diagnostics)

- **performance_elements_count:** 1 → performance report has only one row (likely aggregated).
- **first_element_keys:** `['segments', 'campaign_id', 'campaign_name', 'metrics', 'derived']` → element has `campaign_id` / `campaign_name` keys.
- **first_element_has_campaign_id:** False → values are `None` or empty (Reddit API is not populating campaign in this response, or returns a single aggregate row without campaign dimension).
- **Conclusion:** Campaign scoping cannot run because the performance data we load has no usable campaign id (either one aggregated row with null campaign, or API response shape). Next: inspect `first_element_sample.campaign_id` / `segments_preview` in the API response; if segments contain `campaign_id=xxx`, our fallback should pick it up—if not, the Reddit performance report request or response format needs to be fixed.

### First element sample (confirmed)

- **campaign_id:** null  
- **campaign_name:** null  
- **segments_preview:** `["{'clicks': 196, 'impressions': 45866, 'spend': 166362839}"]`  

So the performance report is returning **one row** that contains only **metrics** (clicks, impressions, spend). There are **no dimension fields** (campaign_id, ad_id) in that row—so `segments` was set to `[str(row)]`, i.e. the string representation of the row. We do request `fields = [CAMPAIGN_ID, AD_ID, IMPRESSIONS, CLICKS, SPEND]`, but the Reddit API response for this report either (1) returns a single aggregate with only metrics, or (2) returns dimensions in a different structure (e.g. separate object or different key names). **Next step:** Inspect the raw performance report API response (e.g. store or log it when `debug_audience` is true) to see the exact response shape and whether per-campaign rows are available.

### Raw performance response (confirmed)

When `debug_audience: true`, the response includes `troubleshooting.raw_performance_response` (loaded from the raw blob). Observed shape:

- **Top-level:** `data`, `pagination`
- **data:** `metrics`, `metrics_updated_at`
- **data.metrics:** list of **1 row**
- **Row keys:** only `clicks`, `impressions`, `spend` — **no** `campaign_id`, `ad_id`, or any dimension

So the Reddit API is returning a **single aggregate row** with only metrics, even though we send `fields: [CAMPAIGN_ID, AD_ID, IMPRESSIONS, CLICKS, SPEND]`. Either (1) the API ignores dimension fields for this report type and always aggregates, or (2) the request must use a different structure (e.g. separate `dimensions` / `breakdowns` in the body instead of putting dimensions in `fields`). **Update:** The performance report request was updated to send `breakdowns: dimensions` (same style as the audience report). **Result:** The API now returns rows that include `campaign_id` and `ad_id`. After deploy, a debug run showed `audience_scope: campaign`, `top_campaign_id: 2428566902956608787`, `first_element_has_campaign_id: True`, and `raw_performance_response.data.metrics[0]` with keys `['ad_id', 'campaign_id', 'clicks', 'impressions', 'spend']`. So campaign-scoped audience is working; interests/communities in the report are now for the top campaign by spend. (If "News & Education" still doesn’t appear, the API may use a different interest taxonomy; communities may still show 0 clicks depending on how the API attributes them per campaign.)

---

## Findings

### 1. Scope: account vs campaign

- **API (current):** Audience reports are **account-level** (all campaigns combined).
- **UI:** You are looking at the **Audience** tab for a **single campaign** (e.g. “Traffic Campaign 2026-02-02”).
- **Effect:** Numbers cannot match. The UI shows “News & Education” 71 clicks for that one campaign; the API returns interests aggregated across all campaigns (e.g. General Entertainment 18, Career Planning 16).

### 2. “News & Education” missing from API response

- In the **raw** Reddit interests response (50 rows), the only interest containing “news” or “education” is **“Gaming News & Discussion”** with 0 clicks.
- **“News & Education”** does **not** appear in the account-level interest list at all (so it is not a naming or sorting bug on our side).
- Conclusion: “News & Education” with 71 clicks is a **campaign-level** breakdown in the UI. The account-level report either does not expose that segment or names/aggregates it differently.

### 3. Communities: all zeros at account level

- Raw Reddit **communities** response: 50 rows, **all with 0 clicks** (e.g. agile, ai_agents, 30plusskincare).
- UI shows r/remotework 66 clicks, r/remoteworks 49, etc. for **one campaign**.
- Conclusion: At **account level** the API returns communities with no clicks (or a different attribution). The high-click communities you see in the UI are again **campaign-level**.

### 4. Why campaign scoping did not run

- `top_campaign_id` and `top_campaign_name` were **None**, so we never requested campaign-scoped audience.
- Top campaign is derived from the **enriched performance blob** (elements with `campaign_id` and spend). If the blob was built **before** we added `campaign_id` to elements, or if it was served from **cache** without `force_refresh`, elements may lack `campaign_id` (or only have it inside `segments`). Parsing from `segments` is implemented, but if the cached blob has no campaign info at all, we stay account-level.

---

## Proposals

### P1. Force refresh when debugging (implemented)

- When you call the portfolio report with **`debug_audience: true`**, the code now **automatically sets `force_refresh: true`** for the performance fetch so the enriched blob is rebuilt with `campaign_id` on each element. You do not need to pass `force_refresh` yourself.
- After deploy, run once with `debug_audience: true` and check:
  - `troubleshooting.top_campaign_id` and `top_campaign_name` are set.
  - `audience_scope` is `"campaign"`.
  - Whether interests/communities now align with the UI for that campaign.

**Example:**

```json
{"debug_audience": true, "force_refresh": true}
```

### P2. Optional: default to campaign scope when possible

- If, after P1, we consistently get a non-null `top_campaign_id` and the Reddit API accepts campaign-scoped audience requests (e.g. `breakdowns: ["CAMPAIGN_ID", "INTEREST"]`), the current behaviour is already correct: we request campaign-scoped audience and filter to the top campaign.
- If the API **rejects** campaign-scoped audience (e.g. 400 or empty data), we already fall back to account-level and log it. No change needed except to document that the UI will only match when Reddit supports campaign-level audience in the Reports API.

### P3. Document scope in the Discord message

- In the Discord report footer, always state scope explicitly, e.g.:
  - “Audience: **account-level** (all campaigns).”
  - “Audience: **campaign-level** (top by spend: &lt;campaign_name&gt;).”
- This avoids confusion when comparing with the UI (single campaign).

### P4. Keep troubleshooting payload in production

- Leave the `debug_audience` flag and `troubleshooting` object in the API response (only when `debug_audience: true`). No extra cost when the flag is false, and it makes it easy to re-check raw API data and payloads without new code.

---

## Next steps

1. Run the portfolio report with **`debug_audience: true` and `force_refresh: true`**.
2. Inspect `troubleshooting.audience_scope`, `top_campaign_id`, `top_campaign_name`, and the interests/communities payloads.
3. If scope becomes `"campaign"` and the Reddit API returns non-empty campaign-scoped audience data, compare again to the UI (same campaign and date range); they should be much closer.
4. If the API still returns account-level only (e.g. campaign filter not supported or returns empty), then the discrepancy is a **Reddit API limitation** and the only fix is to compare UI and report at the same scope (e.g. “All campaigns” in the UI) or to ask Reddit for campaign-level audience in the Reports API.
