#!/usr/bin/env python3
"""CLI entry point for modular AI model evaluation."""
from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv(override=False)

    parser = argparse.ArgumentParser(description="Run Bigas modular model evaluation")
    parser.add_argument(
        "--use-case",
        required=True,
        help="Use case id (e.g. vc-field-assistant)",
    )
    parser.add_argument("--company", help="Fixture company name")
    parser.add_argument("--url", help="Fixture website URL")
    parser.add_argument(
        "--models",
        help="Comma-separated model ids (optional; default = champion + new pro models)",
    )
    parser.add_argument("--dry-run", action="store_true", help="List candidates without invoking adapters")
    parser.add_argument("--skip-judge", action="store_true", help="Skip LLM-as-a-judge scoring")
    parser.add_argument("--no-discord", action="store_true", help="Skip Discord posting")
    parser.add_argument("--no-chat", action="store_true", help="Skip PM chat posting")
    args = parser.parse_args()

    # Register adapters
    import bigas.eval.use_cases.vc_field_assistant  # noqa: F401
    from bigas.eval.base import EvalFixture
    from bigas.eval.reporter import summarize_for_response
    from bigas.eval.runner import EvalRunner

    fixture = None
    if args.company or args.url:
        fixture = EvalFixture.from_dict(
            {
                "company_name": args.company or "",
                "website_url": args.url or "",
            }
        )

    models = None
    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]

    runner = EvalRunner()
    result = runner.run(
        args.use_case,
        fixture=fixture,
        models=models,
        dry_run=args.dry_run,
        skip_judge=args.skip_judge,
        post_discord=not args.no_discord,
        post_chat=not args.no_chat,
    )
    print(json.dumps(summarize_for_response(result), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
