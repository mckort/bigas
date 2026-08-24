"""Tavily list-price from VCFA Firestore ``aiUsageDaily`` shards.

Not a GCP cost — VC Field Assistant records Tavily searches in Firestore.
Gemini in the same rollup is ignored (that spend is ``llm_logs``).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bigas.providers.usage.base import UsageEvent, UsageProvider, usage_provider_enabled

logger = logging.getLogger(__name__)


def _tavily_project() -> str:
    return (
        (os.environ.get("BIGAS_TAVILY_USAGE_PROJECT") or "").strip()
        or "vcfieldassistant"
    )


def _feature_matches(feature: str, prefix: Optional[str]) -> bool:
    if not prefix:
        return True
    p = prefix.strip()
    if not p:
        return True
    return feature.startswith(p) or feature == p.rstrip("_")


def _utc_day_ids(start: datetime, end: datetime) -> List[str]:
    first = start.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    days: List[str] = []
    cur = first
    while cur <= last:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _float_map(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for key, val in raw.items():
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if num:
            out[str(key)] = num
    return out


def merge_tavily_shards(
    shard_docs: Iterable[Dict[str, Any]],
) -> Tuple[Dict[str, float], float]:
    """Return (by_tavily_feature, leftover_tavily_usd)."""
    by_feature: Dict[str, float] = {}
    provider_total = 0.0
    for data in shard_docs:
        if not isinstance(data, dict):
            continue
        provider_total += float((_float_map(data.get("byProvider"))).get("tavily") or 0)
        for key, usd in _float_map(data.get("byFeature")).items():
            if key.startswith("tavily"):
                by_feature[key] = by_feature.get(key, 0.0) + usd
    featured = sum(by_feature.values())
    leftover = provider_total - featured
    if leftover < 1e-9:
        leftover = 0.0
    return by_feature, leftover


def events_for_day(
    day: str,
    shard_docs: Iterable[Dict[str, Any]],
    *,
    project: str,
) -> List[UsageEvent]:
    by_feature, leftover = merge_tavily_shards(shard_docs)
    started = f"{day}T00:00:00+00:00"
    events: List[UsageEvent] = []
    for feature, usd in sorted(by_feature.items()):
        events.append(
            UsageEvent(
                provider="tavily",
                source_id=f"tavily:{project}:{day}:{feature}",
                started_at=started,
                feature=feature,
                est_cost_usd=round(usd, 6),
                cost_estimate=True,
                meta={
                    "app": "vcfieldassistant",
                    "gcp_project": project,
                    "cost_kind": "list_price",
                },
            )
        )
    if leftover:
        events.append(
            UsageEvent(
                provider="tavily",
                source_id=f"tavily:{project}:{day}:tavily.search",
                started_at=started,
                feature="tavily.search",
                est_cost_usd=round(leftover, 6),
                cost_estimate=True,
                meta={
                    "app": "vcfieldassistant",
                    "gcp_project": project,
                    "cost_kind": "list_price",
                },
            )
        )
    return events


class TavilyUsageProvider(UsageProvider):
    name = "tavily"
    display_name = "Tavily search (VCFA)"

    @classmethod
    def is_configured(cls) -> bool:
        return usage_provider_enabled("tavily") and bool(_tavily_project())

    def __init__(self) -> None:
        self._project = _tavily_project()

    def fetch_usage(
        self,
        *,
        start: datetime,
        end: datetime,
        feature_prefix: Optional[str] = None,
    ) -> List[UsageEvent]:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        events: List[UsageEvent] = []
        for day in _utc_day_ids(start, end):
            shards = self._load_shards(day)
            events.extend(events_for_day(day, shards, project=self._project))
        return [e for e in events if _feature_matches(e.feature, feature_prefix)]

    def _load_shards(self, day: str) -> List[Dict[str, Any]]:
        try:
            from google.cloud import firestore
        except ImportError as e:
            raise RuntimeError("google-cloud-firestore is required for Tavily usage") from e
        try:
            db = firestore.Client(project=self._project)
            snaps = (
                db.collection("aiUsageDaily")
                .document(day)
                .collection("shards")
                .stream()
            )
            out: List[Dict[str, Any]] = []
            for snap in snaps:
                data = snap.to_dict() if hasattr(snap, "to_dict") else None
                if isinstance(data, dict):
                    out.append(data)
            return out
        except Exception as e:
            raise RuntimeError(
                f"Firestore aiUsageDaily/{day} failed for project {self._project}: {e}"
            ) from e
