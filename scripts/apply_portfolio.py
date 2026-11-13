#!/usr/bin/env python3
"""Plan or apply every project in bigas.portfolio.yaml.

Dry run (default) prints the same plan as connect_repo.py, once per project.
``--apply`` makes the changes. ``--skip-github`` writes only the Bigas env
maps, which is the right call before Bigas has a public URL.

    python scripts/apply_portfolio.py
    python scripts/apply_portfolio.py --apply --skip-github
    python scripts/apply_portfolio.py --webhook --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import connect_repo  # noqa: E402
from bigas.setup_portfolio import apply_projects, load_portfolio  # noqa: E402

DEFAULT_FILE = ROOT / "bigas.portfolio.yaml"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="Portfolio YAML (default: bigas.portfolio.yaml)")
    parser.add_argument("--apply", action="store_true", help="Make the changes (default: print the plan)")
    parser.add_argument("--webhook", action="store_true", help="Add the workflow_run webhook on each repo")
    parser.add_argument("--skip-github", action="store_true", help="Update .env only. Use this before a public URL exists.")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--bigas-url", default="", help="Override BIGAS_PUBLIC_URL for this run")
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"✗ {path} not found. Draft one with: python scripts/discover_portfolio.py")
        return 2
    try:
        projects = load_portfolio(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"✗ {path}: {exc}")
        return 2
    print(f"Portfolio {path}: {len(projects)} projects\n")
    return apply_projects(
        projects,
        apply=args.apply,
        webhook=args.webhook,
        skip_github=args.skip_github,
        env_file=args.env_file,
        bigas_url=args.bigas_url,
        connect=connect_repo.main,
    )


if __name__ == "__main__":
    sys.exit(main())
