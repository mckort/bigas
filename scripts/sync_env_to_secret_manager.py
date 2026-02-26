#!/usr/bin/env python3
"""
One-time script: read .env from the repo root, create (if missing) each secret in
Google Secret Manager and add a version with the value. Only syncs keys that are in
SECRET_NAMES and have a non-empty value in .env. Run from repo root.
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")

# Same list as env.example SECRET_MANAGER_SECRET_NAMES (excl. GOOGLE_PROJECT_ID – must stay in .env)
SECRET_NAMES = [
    "GA4_PROPERTY_ID",
    "OPENAI_API_KEY",
    "BIGAS_PROGRESS_UPDATES_MODEL",
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_PROJECT_KEY",
    "DISCORD_WEBHOOK_URL_MARKETING",
    "DISCORD_WEBHOOK_URL_PRODUCT",
    "DISCORD_WEBHOOK_URL_CTO",
    "STORAGE_BUCKET_NAME",
    "LINKEDIN_CLIENT_ID",
    "LINKEDIN_CLIENT_SECRET",
    "LINKEDIN_REFRESH_TOKEN",
    "LINKEDIN_AD_ACCOUNT_URN",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_REFRESH_TOKEN",
    "REDDIT_AD_ACCOUNT_ID",
    "META_ACCESS_TOKEN",
    "META_AD_ACCOUNT_ID",
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CUSTOMER_ID",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
    "TARGET_KEYWORDS",
    "GITHUB_TOKEN",
]


def parse_env(path: str) -> dict[str, str]:
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            idx = line.index("=")
            key = line[:idx].strip()
            value = line[idx + 1 :].strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1].replace('\\"', '"')
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1].replace("\\'", "'")
            out[key] = value
    return out


def main() -> None:
    if not os.path.isfile(ENV_PATH):
        print(f".env not found at {ENV_PATH}. Run from repo root.", file=sys.stderr)
        sys.exit(1)

    env = parse_env(ENV_PATH)
    project = env.get("GOOGLE_PROJECT_ID", "").strip()
    if not project:
        print("GOOGLE_PROJECT_ID not set in .env.", file=sys.stderr)
        sys.exit(1)

    to_sync = [(name, env[name]) for name in SECRET_NAMES if env.get(name, "").strip()]
    if not to_sync:
        print("No matching keys with values found in .env.")
        return

    print(f"Project: {project}. Will create/update {len(to_sync)} secrets.")
    for name, value in to_sync:
        # Create secret if it doesn't exist (ignore error if exists)
        subprocess.run(
            ["gcloud", "secrets", "create", name, f"--project={project}"],
            capture_output=True,
        )
        # Add version with value via stdin (avoids exposing value in argv)
        r = subprocess.run(
            ["gcloud", "secrets", "versions", "add", name, f"--project={project}", "--data-file=-"],
            input=value.encode("utf-8"),
            capture_output=True,
            text=False,
        )
        if r.returncode == 0:
            print(f"  OK {name}")
        else:
            print(f"  FAIL {name}: {r.stderr.decode('utf-8', errors='replace').strip()}", file=sys.stderr)
    print("Done.")


if __name__ == "__main__":
    main()
