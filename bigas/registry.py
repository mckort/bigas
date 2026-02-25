"""
ProviderRegistry — discovers and holds all active provider instances.

Usage:
    from bigas.registry import registry
    finance = registry.get("finance")          # None if not configured
    ads_providers = registry.get_all("ads")    # list, may be empty
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

# Maps domain key -> (base class import path, providers sub-package)
DOMAIN_CONFIG = {
    "finance": ("bigas.providers.finance.base", "FinanceProvider", "bigas.providers.finance"),
    "ads": ("bigas.providers.ads.base", "AdsProvider", "bigas.providers.ads"),
    "analytics": ("bigas.providers.analytics.base", "AnalyticsProvider", "bigas.providers.analytics"),
    "notifications": ("bigas.providers.notifications.base", "NotificationChannel", "bigas.providers.notifications"),
}


class ProviderRegistry:
    def __init__(self) -> None:
        self._single: Dict[str, Any] = {}  # domain -> first configured provider
        self._multi: Dict[str, List[Any]] = {}  # domain -> all configured providers

    def discover(self) -> None:
        """
        Scan all provider sub-packages, instantiate every class that:
          1. Is a concrete subclass of the domain's base class
          2. Returns True from is_configured()
        Call once at app startup.
        """
        for domain, (base_module_path, base_class_name, pkg_path) in DOMAIN_CONFIG.items():
            self._multi[domain] = []
            try:
                base_module = importlib.import_module(base_module_path)
                base_cls = getattr(base_module, base_class_name)
                pkg = importlib.import_module(pkg_path)
            except Exception as exc:
                logger.warning("Failed to import base or package for domain %s: %s", domain, exc)
                continue

            for _finder, mod_name, _ispkg in pkgutil.iter_modules(pkg.__path__):
                if mod_name in ("base", "__init__"):
                    continue
                try:
                    mod = importlib.import_module(f"{pkg_path}.{mod_name}")
                except Exception as exc:
                    logger.warning("Could not import provider module %s.%s: %s", pkg_path, mod_name, exc)
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
                                logger.info("Registered provider: %s/%s", domain, getattr(attr, "name", attr.__name__))
                        except Exception as exc:
                            logger.warning("Provider %s failed is_configured() or init: %s", attr, exc)

    def get(self, domain: str) -> Optional[Any]:
        """Return the primary provider for a domain, or None."""
        return self._single.get(domain)

    def get_all(self, domain: str) -> List[Any]:
        """Return all active providers for a domain (useful for ads, notifications)."""
        return self._multi.get(domain, [])

    def status(self) -> dict:
        """Return a summary of all active providers, suitable for a health endpoint."""
        return {domain: [getattr(p, "name", p.__class__.__name__) for p in providers] for domain, providers in self._multi.items()}


# Module-level singleton — import this everywhere
registry = ProviderRegistry()

