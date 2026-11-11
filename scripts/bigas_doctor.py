#!/usr/bin/env python3
"""Check a Bigas setup: the instance config and every connected GitHub repo.

Usage (from the repo root, after `pip install -r requirements.txt`):

    python scripts/bigas_doctor.py              # human-readable report
    python scripts/bigas_doctor.py --json       # machine-readable, for AI agents
    python scripts/bigas_doctor.py --offline    # config only, no GitHub calls
    python scripts/bigas_doctor.py --env-file deploy.env

Exit code 0 when nothing failed, 1 when at least one check failed.
Every failing or warning check carries a `fix`: a command or setting.
The procedure these checks verify is docs/agent-setup.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")  # library deprecation noise would hide the report

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ICONS = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=str(ROOT / ".env"), help="env file to load (default: .env)")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--offline", action="store_true", help="skip GitHub API checks")
    args = parser.parse_args()

    env_file = Path(args.env_file)
    if env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=True)
    elif not args.json:
        print(f"! {env_file} not found; using the current environment only.\n")

    # Importing bigas must not need cloud credentials.
    os.environ.setdefault("CHAT_STORAGE_MODE", os.environ.get("CHAT_STORAGE_MODE") or "memory")

    from bigas.setup_check import run_checks

    report = run_checks(offline=args.offline)
    report["env_file"] = str(env_file) if env_file.is_file() else ""

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    print("Bigas doctor")
    current = None
    for check in report["checks"]:
        section = check["project"] or "instance"
        if section != current:
            current = section
            print(f"\n[{section}]")
        print(f"  {ICONS.get(check['status'], '?')} {check['message']}")
        if check["fix"] and check["status"] in ("fail", "warn", "info"):
            print(f"      fix: {check['fix']}")
    c = report["counts"]
    print(f"\n{c['ok']} ok · {c['warn']} warnings · {c['fail']} failed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
