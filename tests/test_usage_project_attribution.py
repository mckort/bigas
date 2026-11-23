"""Unit tests for board-project cost attribution."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from bigas.providers.usage.base import UsageEvent
from bigas.resources.cto.usage.project_attribution import (
    board_project_for_event,
    gcp_project_to_board_map,
)


def _ev(**kwargs) -> UsageEvent:
    meta = kwargs.pop("meta", {})
    return UsageEvent(
        provider=kwargs.pop("provider", "llm_logs"),
        source_id=kwargs.pop("source_id", "1"),
        started_at=kwargs.pop("started_at", "2026-08-01T00:00:00Z"),
        feature=kwargs.pop("feature", "chat"),
        est_cost_usd=kwargs.pop("est_cost_usd", 1.0),
        meta=meta,
        **kwargs,
    )


class ProjectAttributionTests(unittest.TestCase):
    def test_repo_maps_to_board(self):
        self.assertEqual(
            board_project_for_event(
                _ev(meta={"repo": "mckort/vcfieldassistant"})
            ),
            "VFA",
        )
        self.assertEqual(
            board_project_for_event(_ev(meta={"repo": "mckort/auralearn"})),
            "AURA",
        )

    def test_infra_repo_is_shared_vm(self):
        self.assertEqual(
            board_project_for_event(
                _ev(meta={"repo": "mckort/gcp-single-vm-webstack"})
            ),
            "SHARED_VM",
        )

    def test_pr_url_and_agent_name(self):
        self.assertEqual(
            board_project_for_event(
                _ev(
                    meta={
                        "pr_url": "https://github.com/mckort/pass-or-fund/pull/3"
                    }
                )
            ),
            "POF",
        )
        self.assertEqual(
            board_project_for_event(
                _ev(
                    meta={
                        "agent_name": "Bigas autofix mckort/mylifesdeed#7 (1/5)"
                    }
                )
            ),
            "MYL",
        )

    def test_gcp_project_map(self):
        self.assertEqual(
            board_project_for_event(
                _ev(meta={"gcp_project": "auralearn-491309"})
            ),
            "AURA",
        )
        self.assertEqual(
            board_project_for_event(
                _ev(meta={"gcp_project": "multiple-websites-491407"})
            ),
            "SHARED_VM",
        )

    def test_vfa_features_and_shared_default(self):
        self.assertEqual(
            board_project_for_event(_ev(feature="tavily_search", meta={})),
            "VFA",
        )
        self.assertEqual(
            board_project_for_event(_ev(feature="llm.living_analysis", meta={})),
            "VFA",
        )
        self.assertEqual(
            board_project_for_event(_ev(feature="cto_pr_review", meta={})),
            "BIG",
        )

    @patch.dict(
        "os.environ",
        {"BIGAS_USAGE_GCP_PROJECT_MAP": "custom-proj:FRI"},
        clear=False,
    )
    def test_env_overlay(self):
        self.assertEqual(gcp_project_to_board_map().get("custom-proj"), "FRI")
        self.assertEqual(
            board_project_for_event(_ev(meta={"gcp_project": "custom-proj"})),
            "FRI",
        )


if __name__ == "__main__":
    unittest.main()
