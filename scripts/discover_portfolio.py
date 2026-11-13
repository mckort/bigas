#!/usr/bin/env python3
"""Draft bigas.portfolio.yaml from a folder of cloned GitHub repos.

Each subfolder that is a git repo with a GitHub origin becomes one project.
Folders that are not repos are listed and skipped. The default folder is the
parent of this Bigas clone, which is the right place when every product repo
is a sibling.

    python scripts/discover_portfolio.py
    python scripts/discover_portfolio.py --root ~/Code
    python scripts/discover_portfolio.py --stdout

Show the file to the human, edit keys, delete rows, then:

    python scripts/apply_portfolio.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bigas.setup_portfolio import discover, render  # noqa: E402

DEFAULT_OUT = ROOT / "bigas.portfolio.yaml"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--root",
        default="",
        help="Parent folder whose subfolders are clones (default: the parent of this repo)",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Where to write the draft")
    parser.add_argument("--stdout", action="store_true", help="Print the draft instead of writing it")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing portfolio file")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser() if args.root else ROOT.parent
    if not root.is_dir():
        print(f"✗ {root} is not a directory.")
        return 2
    result = discover(root)
    text = render(result)
    if args.stdout:
        print(text, end="")
        return 0
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"✗ {out} already exists. Show it to the human, or pass --force to rewrite it.")
        print(f"Scanned {root}: {len(result.projects)} GitHub repos.")
        return 2
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out} ({len(result.projects)} projects from {root}).")
    print("Show this file to the human. Delete rows and edit keys, then:")
    print("  python scripts/apply_portfolio.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
