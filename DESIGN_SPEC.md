# bigas — Community-Extensible MCP Server: Design Spec

> **Purpose:** This file is an implementation spec for an AI coding tool.
> Work through the steps in order. Each step is independently deployable and must not break existing functionality.

---

## Context

bigas is a Flask-based MCP server (`app.py`) with three resource blueprints:
- `bigas/resources/marketing/` — GA4 analytics + ad platforms (LinkedIn, Reddit, Google Ads, Meta)
- `bigas/resources/product/` — Jira release notes + progress updates
- `bigas/resources/cto/` — GitHub PR review

**Goal:** Refactor so that external contributors can add new data providers (e.g. QuickBooks as a finance provider, TikTok as an ads provider) by dropping a single file into the right directory, without editing any existing files.

The existing `bigas/llm/factory.py` pattern is the model to follow — a base class, multiple implementations, config-driven selection.

---

## Step 1 — Create the Provider Base Classes

Create the following files. Do not modify any existing files.

### `bigas/providers/__init__.py`
Empty.

### `bigas/providers/finance/__init__.py`
Empty.

### `bigas/providers/finance/base.py`

```python
"""
FinanceProvider ABC — implement this to add a new finance data source.

Required env vars are declared in the concrete class's `is_configured()`.
All monetary values are returned as floats in the account's native currency.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Transaction:
    id: str
    date: str                   # ISO 8601: YYYY-MM-DD
    amount: float               # positive = income, negative = expense
    currency: str               # ISO 4217, e.g. "SEK", "USD"
    description: str
    category: Optional[str] = None
    counterpart: Optional[str] = None


@dataclass
class PeriodSummary:
    start_date: str
    end_date: str
    total: float
    currency: str
    breakdown: dict = field(default_factory=dict)   # category -> float


class FinanceProvider(ABC):

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'fortnox', 'quickbooks'. Used in logs and manifests."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'Fortnox', 'QuickBooks Online'."""

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Return True only when all required env vars are present and non-empty."""

    # ── Domain methods ────────────────────────────────────────────────────────

    @abstractmethod
    def get_revenue(self, start_date: str, end_date: str) -> PeriodSummary:
        """Return total revenue for the period."""

    @abstractmethod
    def get_expenses(self, start_date: str, end_date: str) -> PeriodSummary:
        """Return total expenses for the period."""

    @abstractmethod
    def get_transactions(
        self,
        start_date: str,
        end_date: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Transaction]:
        """Return a paginated list of transactions for the period."""

    # ── Optional — override for richer capability ─────────────────────────────

    def health_check(self) -> dict:
        """Return {'status': 'ok'} or {'status': 'error', 'message': '...'}."""
        return {"status": "ok", "provider": self.name}

    def get_mrr(self) -> Optional[float]:
        """Return current MRR if the provider supports it, else None."""
        return None
```

### `bigas/providers/ads/__init__.py`
Empty.

### `bigas/providers/ads/base.py`

```python
"""
AdsProvider ABC — implement this to add a new ad platform.

Multiple AdsProvider implementations can be active simultaneously.
Each instance represents one ad account on one platform.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CampaignMetrics:
    campaign_id: str
    campaign_name: str
    impressions: int
    clicks: int
    spend: float
    currency: str
    conversions: Optional[int] = None
    ctr: Optional[float] = None       # clicks / impressions
    cpc: Optional[float] = None       # spend / clicks
    cpm: Optional[float] = None       # spend / impressions * 1000
    extra: dict = field(default_factory=dict)   # platform-specific fields


class AdsProvider(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'linkedin', 'tiktok'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'LinkedIn Ads'."""

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Return True only when all required env vars are present."""

    @abstractmethod
    def get_campaign_performance(
        self,
        start_date: str,
        end_date: str,
    ) -> List[CampaignMetrics]:
        """Return per-campaign metrics for the period."""

    @abstractmethod
    def get_account_summary(self, start_date: str, end_date: str) -> dict:
        """Return account-level totals: total_spend, total_clicks, total_impressions."""

    def health_check(self) -> dict:
        return {"status": "ok", "provider": self.name}
```

### `bigas/providers/analytics/__init__.py`
Empty.

### `bigas/providers/analytics/base.py`

```python
"""
AnalyticsProvider ABC — implement this to add a new web analytics source.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PageMetrics:
    path: str
    sessions: int
    pageviews: int
    bounce_rate: Optional[float] = None
    avg_session_duration: Optional[float] = None


class AnalyticsProvider(ABC):

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool: ...

    @abstractmethod
    def get_overview(self, start_date: str, end_date: str) -> dict:
        """Return top-level metrics: sessions, users, pageviews, bounce_rate."""

    @abstractmethod
    def get_top_pages(self, start_date: str, end_date: str, limit: int = 10) -> List[PageMetrics]:
        """Return the top N pages by sessions."""

    def health_check(self) -> dict:
        return {"status": "ok", "provider": self.name}
```

### `bigas/providers/notifications/__init__.py`
Empty.

### `bigas/providers/notifications/base.py`

```python
"""
NotificationChannel ABC — implement this to add a new outbound notification channel.
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class NotificationChannel(ABC):

    @property
    @abstractmethod
    def name(self) -> str: ...

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool: ...

    @abstractmethod
    def send(self, message: str, channel_hint: Optional[str] = None) -> bool:
        """
        Send a message. channel_hint is a free-form routing hint
        (e.g. a Discord channel name, a Slack channel ID).
        Returns True on success.
        """
```

---

## Step 2 — Wrap Existing Services with Provider Classes

For each existing service, create a thin provider wrapper that delegates to the existing service. **Do not modify the existing service files.**

### `bigas/providers/ads/linkedin.py`

```python
import os
from typing import List
from bigas.providers.ads.base import AdsProvider, CampaignMetrics
# Import the existing service — do not rewrite it
from bigas.resources.marketing.linkedin_ads_service import (
    LinkedInConfig, get_ad_analytics,   # adjust to actual function names
)


class LinkedInAdsProvider(AdsProvider):
    name = "linkedin"
    display_name = "LinkedIn Ads"

    @classmethod
    def is_configured(cls) -> bool:
        return all([
            os.getenv("LINKEDIN_CLIENT_ID"),
            os.getenv("LINKEDIN_CLIENT_SECRET"),
            os.getenv("LINKEDIN_REFRESH_TOKEN"),
        ])

    def get_campaign_performance(self, start_date: str, end_date: str) -> List[CampaignMetrics]:
        # Delegate to existing service, then map to CampaignMetrics dataclass
        raw = get_ad_analytics(start_date=start_date, end_date=end_date)
        return [
            CampaignMetrics(
                campaign_id=row["id"],
                campaign_name=row["name"],
                impressions=row.get("impressions", 0),
                clicks=row.get("clicks", 0),
                spend=row.get("costInLocalCurrency", 0.0),
                currency=row.get("currency", "USD"),
            )
            for row in raw.get("campaigns", [])
        ]

    def get_account_summary(self, start_date: str, end_date: str) -> dict:
        rows = self.get_campaign_performance(start_date, end_date)
        return {
            "total_spend": sum(r.spend for r in rows),
            "total_clicks": sum(r.clicks for r in rows),
            "total_impressions": sum(r.impressions for r in rows),
        }
```

Repeat this pattern for:
- `bigas/providers/ads/reddit.py` → wraps `reddit_ads_service.py`
- `bigas/providers/ads/google_ads.py` → wraps `google_ads_service.py`
- `bigas/providers/ads/meta.py` → wraps `meta_ads_service.py`
- `bigas/providers/analytics/ga4.py` → wraps `ga4_service.py`
- `bigas/providers/notifications/discord.py` → wraps Discord webhook calls

---

## Step 3 — Create the Provider Registry

### `bigas/registry.py`

```python
"""
ProviderRegistry — discovers and holds all active provider instances.

Usage:
    from bigas.registry import registry
    finance = registry.get("finance")          # None if not configured
    ads_providers = registry.get_all("ads")    # list, may be empty
"""
from __future__ import annotations
import importlib
import pkgutil
import logging
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)

# Maps domain key -> (base class import path, providers sub-package)
DOMAIN_CONFIG = {
    "finance":       ("bigas.providers.finance.base",       "FinanceProvider",       "bigas.providers.finance"),
    "ads":           ("bigas.providers.ads.base",            "AdsProvider",           "bigas.providers.ads"),
    "analytics":     ("bigas.providers.analytics.base",      "AnalyticsProvider",     "bigas.providers.analytics"),
    "notifications": ("bigas.providers.notifications.base",  "NotificationChannel",   "bigas.providers.notifications"),
}


class ProviderRegistry:

    def __init__(self):
        self._single: Dict[str, Any] = {}    # domain -> first configured provider
        self._multi: Dict[str, List[Any]] = {}  # domain -> all configured providers

    def discover(self):
        """
        Scan all provider sub-packages, instantiate every class that:
          1. Is a concrete subclass of the domain's base class
          2. Returns True from is_configured()
        Call once at app startup.
        """
        for domain, (base_module_path, base_class_name, pkg_path) in DOMAIN_CONFIG.items():
            self._multi[domain] = []
            base_module = importlib.import_module(base_module_path)
            base_cls = getattr(base_module, base_class_name)
            pkg = importlib.import_module(pkg_path)

            for _, mod_name, _ in pkgutil.iter_modules(pkg.__path__):
                if mod_name in ("base", "__init__"):
                    continue
                try:
                    mod = importlib.import_module(f"{pkg_path}.{mod_name}")
                except Exception as e:
                    logger.warning("Could not import provider module %s.%s: %s", pkg_path, mod_name, e)
                    continue

                for attr_name in vars(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, base_cls)
                        and attr is not base_cls
                        and not getattr(attr, "__abstractmethods__", None)
                    ):
                        try:
                            if attr.is_configured():
                                instance = attr()
                                self._multi[domain].append(instance)
                                if domain not in self._single:
                                    self._single[domain] = instance
                                logger.info("Registered provider: %s/%s", domain, attr.name)
                        except Exception as e:
                            logger.warning("Provider %s failed is_configured(): %s", attr, e)

    def get(self, domain: str) -> Optional[Any]:
        """Return the primary provider for a domain, or None."""
        return self._single.get(domain)

    def get_all(self, domain: str) -> List[Any]:
        """Return all active providers for a domain (useful for ads, notifications)."""
        return self._multi.get(domain, [])

    def status(self) -> dict:
        """Return a summary of all active providers, suitable for a health endpoint."""
        return {
            domain: [p.name for p in providers]
            for domain, providers in self._multi.items()
        }


# Module-level singleton — import this everywhere
registry = ProviderRegistry()
```

---

## Step 4 — Initialise Registry at App Startup

Edit `app.py`. Find the line where the Flask app is created and add registry discovery **after** the app is created and **before** the first request is served.

```python
# In app.py — add these imports near the top
from bigas.registry import registry

# Add this call after create_app() or after blueprint registration:
registry.discover()

# Optional: add a /mcp/providers health endpoint
@app.route("/mcp/providers", methods=["GET"])
def providers_status():
    return jsonify(registry.status())
```

---

## Step 5 — Add the Tool Registration Decorator

### `bigas/tools.py`

```python
"""
@register_tool decorator — annotates a handler function with its MCP tool metadata.

The manifest is built from _TOOL_REGISTRY at startup; it never drifts from the
actual handlers.

Usage:
    from bigas.tools import register_tool

    @register_tool(
        name="get_revenue",
        description="Return total revenue for a date range.",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        }
    )
    def get_revenue_handler(start_date: str, end_date: str):
        ...
"""
from __future__ import annotations
from typing import Callable, List

_TOOL_REGISTRY: List[dict] = []


def register_tool(name: str, description: str, parameters: dict):
    """Decorator that registers a handler as an MCP tool."""
    def decorator(fn: Callable) -> Callable:
        entry = {
            "name": name,
            "description": description,
            "path": f"/mcp/tools/{name}",
            "method": "POST",
            "parameters": parameters,
            "_handler": fn,
        }
        fn._mcp_tool = entry
        _TOOL_REGISTRY.append(entry)
        return fn
    return decorator


def get_registered_tools() -> List[dict]:
    """Return all tool manifest entries (without the _handler key)."""
    return [
        {k: v for k, v in tool.items() if k != "_handler"}
        for tool in _TOOL_REGISTRY
    ]
```

---

## Step 6 — How to Add a New Provider (Community Template)

This is the complete workflow a contributor follows. Document this in `CONTRIBUTING.md`.

### Example: Adding QuickBooks as a Finance Provider

1. **Create `bigas/providers/finance/quickbooks.py`**

```python
"""
QuickBooks Online finance provider.

Required env vars:
    QUICKBOOKS_CLIENT_ID
    QUICKBOOKS_CLIENT_SECRET
    QUICKBOOKS_REFRESH_TOKEN
    QUICKBOOKS_REALM_ID
"""
import os
import requests
from typing import List
from bigas.providers.finance.base import FinanceProvider, PeriodSummary, Transaction

QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_API_BASE  = "https://quickbooks.api.intuit.com/v3/company"


class QuickBooksProvider(FinanceProvider):
    name         = "quickbooks"
    display_name = "QuickBooks Online"

    @classmethod
    def is_configured(cls) -> bool:
        return all([
            os.getenv("QUICKBOOKS_CLIENT_ID"),
            os.getenv("QUICKBOOKS_CLIENT_SECRET"),
            os.getenv("QUICKBOOKS_REFRESH_TOKEN"),
            os.getenv("QUICKBOOKS_REALM_ID"),
        ])

    def _access_token(self) -> str:
        resp = requests.post(QB_TOKEN_URL, data={
            "grant_type":    "refresh_token",
            "refresh_token": os.environ["QUICKBOOKS_REFRESH_TOKEN"],
        }, auth=(os.environ["QUICKBOOKS_CLIENT_ID"], os.environ["QUICKBOOKS_CLIENT_SECRET"]))
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _report(self, report_name: str, params: dict) -> dict:
        realm   = os.environ["QUICKBOOKS_REALM_ID"]
        token   = self._access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url     = f"{QB_API_BASE}/{realm}/reports/{report_name}"
        resp    = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_revenue(self, start_date: str, end_date: str) -> PeriodSummary:
        data  = self._report("ProfitAndLoss", {"start_date": start_date, "end_date": end_date})
        total = float(data.get("Rows", {}).get("TotalIncome", 0))
        return PeriodSummary(start_date=start_date, end_date=end_date, total=total, currency="USD")

    def get_expenses(self, start_date: str, end_date: str) -> PeriodSummary:
        data  = self._report("ProfitAndLoss", {"start_date": start_date, "end_date": end_date})
        total = float(data.get("Rows", {}).get("TotalExpenses", 0))
        return PeriodSummary(start_date=start_date, end_date=end_date, total=total, currency="USD")

    def get_transactions(self, start_date: str, end_date: str, limit: int = 100, offset: int = 0) -> List[Transaction]:
        # QuickBooks TransactionList report
        data = self._report("TransactionList", {
            "start_date": start_date, "end_date": end_date,
            "maxresults": limit, "startposition": offset + 1,
        })
        rows = data.get("Rows", {}).get("Row", [])
        return [
            Transaction(
                id=r.get("ColData", [{}])[0].get("value", ""),
                date=r.get("ColData", [{}])[1].get("value", ""),
                amount=float(r.get("ColData", [{}])[4].get("value", 0)),
                currency="USD",
                description=r.get("ColData", [{}])[2].get("value", ""),
            )
            for r in rows
        ]
```

2. **Add env vars to `.env`**

```
QUICKBOOKS_CLIENT_ID=your_client_id
QUICKBOOKS_CLIENT_SECRET=your_client_secret
QUICKBOOKS_REFRESH_TOKEN=your_refresh_token
QUICKBOOKS_REALM_ID=your_realm_id
```

3. **Restart the server** — `registry.discover()` finds `QuickBooksProvider`, calls `is_configured()` → `True`, and registers it. No other files change.

4. **Verify** — `GET /mcp/providers` should include `"finance": ["quickbooks"]`.

---

## Step 7 — Decompose `marketing/endpoints.py` (deferred, low risk)

> Do this step last, after Steps 1–6 are live and stable.

Split `bigas/resources/marketing/endpoints.py` into per-platform files. The goal is one file per platform that can be reviewed, tested, and replaced independently.

**Target structure:**
```
bigas/providers/ads/
  linkedin/
    __init__.py
    provider.py       # LinkedInAdsProvider (from Step 2)
    service.py        # move linkedin_ads_service.py here
    endpoints.py      # @register_tool handlers for LinkedIn tools only
    tests/
      test_linkedin.py
  reddit/
    ...same...
  google_ads/
    ...same...
  meta/
    ...same...
```

**Migration rule:** Move the handler functions one platform at a time. After each move, run the existing test suite to confirm nothing regressed. The blueprint and manifest aggregation in `bigas/resources/marketing/` stays unchanged; it just stops importing from the monolithic `endpoints.py` and starts importing from each sub-package.

---

## Acceptance Criteria

After completing Steps 1–6, the following must be true:

- [ ] All existing MCP tools still appear in `GET /mcp/manifest` and respond correctly.
- [ ] `GET /mcp/providers` returns a JSON object mapping each domain to its active providers.
- [ ] Dropping `bigas/providers/finance/quickbooks.py` and setting the four QB env vars causes `"finance": ["quickbooks"]` to appear in `/mcp/providers` after restart — with **zero other file changes**.
- [ ] Removing all QB env vars causes the QuickBooks provider to disappear silently — no crash, no error log, no change to other tools.
- [ ] The test suite (`pytest`) passes without modification.

---

## Key Files Reference

| File | Role | Modify? |
|------|------|---------|
| `app.py` | Flask app, blueprint registration | Yes — add `registry.discover()` |
| `bigas/registry.py` | Provider discovery & singleton | Create new |
| `bigas/tools.py` | `@register_tool` decorator | Create new |
| `bigas/providers/*/base.py` | ABCs for each domain | Create new |
| `bigas/providers/ads/linkedin.py` | Wrapper over existing service | Create new |
| `bigas/resources/marketing/endpoints.py` | Existing monolith | Do not touch (Step 7 only) |
| `bigas/resources/marketing/linkedin_ads_service.py` | Existing service | Do not touch |
| `bigas/llm/factory.py` | Existing LLM abstraction | Do not touch — this is the pattern to follow |
