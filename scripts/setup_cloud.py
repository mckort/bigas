#!/usr/bin/env python3
"""Create the Cloud Run plumbing, then optionally run ./deploy.sh.

    python scripts/setup_cloud.py --project acme-bigas            # plan
    python scripts/setup_cloud.py --project acme-bigas --apply    # APIs, registry, service account
    python scripts/setup_cloud.py --project acme-bigas --apply --deploy

Billing, gcloud auth login, and the Firebase web app stay manual. See
docs/agent-setup.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bigas.setup_cloud import setup_cloud, subprocess_runner  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="Google Cloud project id")
    parser.add_argument("--apply", action="store_true", help="Create the resources (default: print the plan)")
    parser.add_argument("--deploy", action="store_true", help="After --apply, run ./deploy.sh")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--firestore-location", default="eur3", help="Location used when creating Firestore (default eur3)")
    args = parser.parse_args(argv)
    if args.deploy and not args.apply:
        print("✗ --deploy requires --apply. Pass both, so the plan is not skipped by accident.")
        return 2
    return setup_cloud(
        project=args.project,
        apply=args.apply,
        deploy=args.deploy,
        env_file=Path(args.env_file),
        runner=subprocess_runner,
        firestore_location=args.firestore_location,
        repo_root=ROOT,
    )


if __name__ == "__main__":
    sys.exit(main())
