"""Cursor Cloud Agents usage provider (List Agents + /usage)."""
from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from bigas.providers.usage.base import UsageEvent, UsageProvider
from bigas.resources.cto.autofix.cursor_client import (
    CursorCloudAgentClient,
    CursorCloudAgentError,
)
from bigas.resources.cto.usage.pricing import (
    CursorTokenUsage,
    default_autofix_model,
    estimate_cursor_cost_usd,
)

logger = logging.getLogger(__name__)

_BIGAS_AUTOFIX_NAME_RE = re.compile(r"^Bigas autofix\b", re.IGNORECASE)
_PR_IN_NAME_RE = re.compile(
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<pr>\d+)"
)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    raw = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _repo_from_pr_url(pr_url: str) -> str:
    try:
        path = urlparse(pr_url).path.strip("/")
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass
    return ""


def _feature_matches(feature: str, prefix: Optional[str]) -> bool:
    if not prefix:
        return True
    p = prefix.strip()
    if not p:
        return True
    return feature.startswith(p) or feature == p.rstrip("_")


class CursorCloudAgentUsageProvider(UsageProvider):
    name = "cursor"
    display_name = "Cursor Cloud Agents"

    @classmethod
    def is_configured(cls) -> bool:
        return bool((os.environ.get("CURSOR_API_KEY") or "").strip())

    def __init__(self, *, api_key: Optional[str] = None) -> None:
        key = (api_key or "").strip() or (os.environ.get("CURSOR_API_KEY") or "").strip()
        if not key:
            raise ValueError("CURSOR_API_KEY is required for CursorCloudAgentUsageProvider")
        self._client = CursorCloudAgentClient(api_key=key)
        self._model = default_autofix_model()

    def fetch_usage(
        self,
        *,
        start: datetime,
        end: datetime,
        feature_prefix: Optional[str] = None,
    ) -> List[UsageEvent]:
        if not _feature_matches("cto_autofix", feature_prefix):
            return []

        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)

        events: List[UsageEvent] = []
        agents_to_fetch: List[dict] = []
        cursor: Optional[str] = None
        pages = 0
        max_pages = 50

        while pages < max_pages:
            pages += 1
            try:
                payload = self._client.list_agents(limit=100, cursor=cursor)
            except CursorCloudAgentError as e:
                logger.warning("Cursor list_agents failed: %s", e)
                break

            items = payload.get("items") or []
            if not isinstance(items, list) or not items:
                break

            stop_paging = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                created = _parse_iso(item.get("createdAt") or item.get("created_at"))
                if created is not None and created < start:
                    stop_paging = True
                    break
                if created is not None and created > end:
                    continue

                name = (item.get("name") or "").strip()
                if not _BIGAS_AUTOFIX_NAME_RE.match(name):
                    continue

                agent_id = (item.get("id") or "").strip()
                if not agent_id:
                    continue

                # Collect agent metadata for concurrent usage fetching.
                agents_to_fetch.append({
                    "agent_id": agent_id,
                    "name": name,
                    "created_at": created.isoformat() if created else start.isoformat(),
                    "latest_run_id": (item.get("latestRunId") or item.get("latest_run_id") or ""),
                    "agent_url": (item.get("url") or ""),
                })

            if stop_paging:
                break
            next_cursor = payload.get("nextCursor") or payload.get("next_cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)

        # Fetch usage concurrently to avoid N+1 sequential API calls that can
        # exceed frontend/gateway timeouts when there are many agents.
        max_workers = min(10, len(agents_to_fetch)) if agents_to_fetch else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._event_for_agent,
                    agent_id=agent["agent_id"],
                    name=agent["name"],
                    created_at=agent["created_at"],
                    latest_run_id=agent["latest_run_id"],
                    agent_url=agent["agent_url"],
                ): agent["agent_id"]
                for agent in agents_to_fetch
            }
            for future in as_completed(futures):
                try:
                    event = future.result()
                    if event is not None:
                        events.append(event)
                except Exception as e:
                    agent_id = futures[future]
                    logger.warning("Concurrent get_usage failed for %s: %s", agent_id, e)

        return events

    def _event_for_agent(
        self,
        *,
        agent_id: str,
        name: str,
        created_at: str,
        latest_run_id: str,
        agent_url: str,
    ) -> Optional[UsageEvent]:
        try:
            usage_payload = self._client.get_usage(agent_id)
        except CursorCloudAgentError as e:
            logger.warning("Cursor get_usage failed for %s: %s", agent_id, e)
            return None

        total = usage_payload.get("totalUsage") or usage_payload.get("total_usage") or {}
        if not isinstance(total, dict):
            total = {}
        usage = CursorTokenUsage.from_mapping(total)
        if usage.total_tokens == 0:
            runs = usage_payload.get("runs") or []
            if isinstance(runs, list):
                inp = out = cw = cr = 0
                for run in runs:
                    if not isinstance(run, dict):
                        continue
                    u = CursorTokenUsage.from_mapping(run.get("usage") or {})
                    inp += u.input_tokens
                    out += u.output_tokens
                    cw += u.cache_write_tokens
                    cr += u.cache_read_tokens
                usage = CursorTokenUsage(
                    input_tokens=inp,
                    output_tokens=out,
                    cache_write_tokens=cw,
                    cache_read_tokens=cr,
                )

        model = self._model
        m = _PR_IN_NAME_RE.search(name or "")
        pr_url = (
            f"https://github.com/{m.group('repo')}/pull/{m.group('pr')}" if m else ""
        )
        repo = _repo_from_pr_url(pr_url) or (m.group("repo") if m else "")
        est = estimate_cursor_cost_usd(model, usage)
        return UsageEvent(
            provider=self.name,
            source_id=agent_id,
            started_at=created_at,
            feature="cto_autofix",
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            total_tokens=usage.total_tokens,
            est_cost_usd=est,
            cost_estimate=est is not None,
            meta={
                "agent_name": name,
                "agent_url": agent_url or f"https://cursor.com/agents/{agent_id}",
                "run_id": (latest_run_id or "").strip(),
                "pr_url": pr_url,
                "repo": repo,
            },
        )
