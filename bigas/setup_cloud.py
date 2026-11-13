"""Plan and apply the Cloud Run plumbing from the README deploy tutorial.

Billing, ``gcloud auth login``, and the Firebase web app stay with the human.
``./deploy.sh`` still needs ``GA4_PROPERTY_ID`` and one LLM key.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from bigas.setup_repo import read_env_file, update_env_text

REGION = "europe-north1"
ARTIFACT_REPO = "bigas-repo"
IMAGE_NAME = "bigas"
RUNNER_ACCOUNT = "bigas-runner"
APIS = (
    "run.googleapis.com",
    "analyticsdata.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "firestore.googleapis.com",
    "identitytoolkit.googleapis.com",
)
ROLES = (
    "roles/analyticsdata.reader",
    "roles/storage.objectAdmin",
    "roles/datastore.user",
    "roles/secretmanager.secretAccessor",
)

MANUAL_STEPS = """
Still done by you:
  1. Billing on the Google Cloud project. This script stops when it is not linked.
  2. gcloud auth login, once, in a browser.
  3. Firebase, so sign-in is real and chat survives a restart. This script enables
     the APIs and creates the Firestore database when it is missing. In the
     Firebase console: add this GCP project, add a Web app, enable Email/Password,
     turn off public sign-up, and set CHAT_AUTH_MODE=firebase,
     CHAT_STORAGE_MODE=firestore, FIREBASE_* and VITE_FIREBASE_*
     (README: Chat web interface → Production).
  4. GA4_PROPERTY_ID. deploy.sh still requires it.
""".strip()


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str]], CommandResult]


def runner_email(project: str) -> str:
    return f"{RUNNER_ACCOUNT}@{project}.iam.gserviceaccount.com"


def subprocess_runner(args: Sequence[str]) -> CommandResult:
    try:
        proc = subprocess.run(list(args), capture_output=True, text=True, check=False)
    except OSError as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def _ok(result: CommandResult) -> bool:
    return result.returncode == 0


def planned_commands(project: str, *, firestore_location: str = "eur3") -> List[str]:
    """Human-readable command list. Apply skips create steps that already exist."""
    email = runner_email(project)
    apis = " ".join(APIS)
    lines = [
        f"gcloud config set project {project}",
        f"gcloud services enable {apis} --project {project}",
        f"gcloud artifacts repositories describe {ARTIFACT_REPO} --location {REGION} --project {project}",
        f"  # when missing: gcloud artifacts repositories create {ARTIFACT_REPO} "
        f"--repository-format docker --location {REGION} --project {project}",
        f"gcloud auth configure-docker {REGION}-docker.pkg.dev --quiet",
        f"gcloud iam service-accounts describe {email} --project {project}",
        f"  # when missing: gcloud iam service-accounts create {RUNNER_ACCOUNT} "
        f"--display-name \"Bigas Cloud Run runtime\" --project {project}",
    ]
    for role in ROLES:
        lines.append(
            "gcloud projects add-iam-policy-binding "
            f"{project} --member serviceAccount:{email} --role {role}"
        )
    lines.append(f"gcloud firestore databases list --project {project}")
    lines.append(
        f"  # when missing: gcloud firestore databases create --location {firestore_location} "
        f"--project {project}"
    )
    lines.append(
        f"# .env: GOOGLE_PROJECT_ID={project}, GOOGLE_SERVICE_ACCOUNT_EMAIL={email}, "
        f"DOCKER_REPO={ARTIFACT_REPO}, IMAGE_NAME={IMAGE_NAME}, IMAGE_TAG=latest"
    )
    return lines


def _env_fill(project: str) -> dict:
    return {
        "GOOGLE_PROJECT_ID": project,
        "GOOGLE_SERVICE_ACCOUNT_EMAIL": runner_email(project),
        "DOCKER_REPO": ARTIFACT_REPO,
        "IMAGE_NAME": IMAGE_NAME,
        "IMAGE_TAG": "latest",
    }


def missing_deploy_requirements(env: dict) -> List[str]:
    """What ``deploy.sh`` will reject. LLM keys: any one of the three is enough."""
    missing = []
    if not any((env.get(name) or "").strip() for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")):
        missing.append("one of GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY")
    for name in ("GA4_PROPERTY_ID", "GOOGLE_PROJECT_ID", "GOOGLE_SERVICE_ACCOUNT_EMAIL", "DOCKER_REPO", "IMAGE_NAME"):
        if not (env.get(name) or "").strip():
            missing.append(name)
    return missing


def setup_cloud(
    *,
    project: str,
    apply: bool,
    deploy: bool,
    env_file: Path,
    runner: Runner,
    firestore_location: str = "eur3",
    repo_root: Optional[Path] = None,
) -> int:
    """Print the plan. With ``apply``, create the plumbing. ``deploy`` runs ``./deploy.sh``."""
    project = project.strip()
    if not project or "/" in project or " " in project:
        print("✗ Pass --project with the Google Cloud project id (e.g. acme-bigas).")
        return 2

    print(f"Cloud Run setup for {project} ({REGION})\n")
    for line in planned_commands(project, firestore_location=firestore_location):
        print(line)
    print()
    print(MANUAL_STEPS)

    if not apply:
        print("\nDry run. Add --apply to create these resources. Add --deploy to run ./deploy.sh after that.")
        return 0

    auth = runner(["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"])
    account = (auth.stdout or "").strip().splitlines()
    if not _ok(auth) or not account or not account[0].strip():
        print("\n✗ No active gcloud account. Run: gcloud auth login")
        return 2
    print(f"\n✓ gcloud account {account[0].strip()}")

    billing = runner(["gcloud", "billing", "projects", "describe", project, "--format=value(billingEnabled)"])
    enabled = (billing.stdout or "").strip().lower()
    if not _ok(billing) or enabled not in ("true", "yes"):
        print(f"✗ Billing is not enabled on {project}. Link a billing account, then rerun.")
        return 2
    print("✓ Billing is enabled")

    steps = [
        ["gcloud", "config", "set", "project", project],
        ["gcloud", "services", "enable", *APIS, "--project", project],
    ]
    for args in steps:
        result = runner(args)
        if not _ok(result):
            print(f"✗ {' '.join(args)}\n{(result.stderr or result.stdout).strip()}")
            return 1

    email = runner_email(project)
    described = runner(
        ["gcloud", "artifacts", "repositories", "describe", ARTIFACT_REPO, "--location", REGION, "--project", project]
    )
    if not _ok(described):
        created = runner(
            [
                "gcloud", "artifacts", "repositories", "create", ARTIFACT_REPO,
                "--repository-format", "docker", "--location", REGION, "--project", project,
            ]
        )
        if not _ok(created):
            print(f"✗ Could not create Artifact Registry repo {ARTIFACT_REPO}.\n{(created.stderr or '').strip()}")
            return 1
        print(f"✓ Created Artifact Registry repo {ARTIFACT_REPO}")
    else:
        print(f"✓ Artifact Registry repo {ARTIFACT_REPO} already exists")

    docker_cfg = runner(["gcloud", "auth", "configure-docker", f"{REGION}-docker.pkg.dev", "--quiet"])
    if not _ok(docker_cfg):
        print(f"✗ gcloud auth configure-docker failed.\n{(docker_cfg.stderr or '').strip()}")
        return 1

    sa = runner(["gcloud", "iam", "service-accounts", "describe", email, "--project", project])
    if not _ok(sa):
        made = runner(
            [
                "gcloud", "iam", "service-accounts", "create", RUNNER_ACCOUNT,
                "--display-name", "Bigas Cloud Run runtime", "--project", project,
            ]
        )
        if not _ok(made):
            print(f"✗ Could not create service account {email}.\n{(made.stderr or '').strip()}")
            return 1
        print(f"✓ Created service account {email}")
    else:
        print(f"✓ Service account {email} already exists")

    for role in ROLES:
        bound = runner(
            [
                "gcloud", "projects", "add-iam-policy-binding", project,
                "--member", f"serviceAccount:{email}", "--role", role,
            ]
        )
        if not _ok(bound):
            print(f"✗ Could not grant {role}.\n{(bound.stderr or '').strip()}")
            return 1
    print("✓ Runtime roles granted")

    databases = runner(["gcloud", "firestore", "databases", "list", "--project", project, "--format=value(name)"])
    if _ok(databases) and (databases.stdout or "").strip():
        print("✓ Firestore database already exists")
    else:
        created_db = runner(
            ["gcloud", "firestore", "databases", "create", "--location", firestore_location, "--project", project]
        )
        if _ok(created_db):
            print(f"✓ Created Firestore database in {firestore_location}")
        else:
            print(
                "✗ Firestore database was not created. Create it in the console "
                f"(Native mode, location {firestore_location}). Cloud setup continues.\n"
                + (created_db.stderr or "").strip()
            )

    env = read_env_file(env_file)
    fill = {key: value for key, value in _env_fill(project).items() if not (env.get(key) or "").strip()}
    if fill:
        text = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
        env_file.write_text(update_env_text(text, fill), encoding="utf-8")
        print(f"✓ Wrote {', '.join(fill)} to {env_file}")
    else:
        print(f"✓ {env_file} already has the Cloud Run settings")

    if not deploy:
        print("\nNext: set GA4_PROPERTY_ID if it is empty, then")
        print(f"  python scripts/setup_cloud.py --project {project} --apply --deploy")
        print("Copy the service URL into BIGAS_PUBLIC_URL, then python scripts/apply_portfolio.py --apply")
        return 0

    env = {**os.environ, **read_env_file(env_file)}
    missing = missing_deploy_requirements(env)
    if missing:
        print("✗ ./deploy.sh needs: " + ", ".join(missing))
        return 2
    root = repo_root or Path(__file__).resolve().parents[1]
    if (env.get("SECRET_MANAGER") or "").strip().lower() == "true":
        sync = runner([sys.executable, str(root / "scripts" / "sync_env_to_secret_manager.py")])
        if not _ok(sync):
            print(f"✗ Secret Manager sync failed.\n{(sync.stderr or sync.stdout).strip()}")
            return 1
        print("✓ Synced secrets")
    previous = os.environ.get("ENV_FILE")
    os.environ["ENV_FILE"] = str(env_file)
    try:
        deployed = runner([str(root / "deploy.sh")])
    finally:
        if previous is None:
            os.environ.pop("ENV_FILE", None)
        else:
            os.environ["ENV_FILE"] = previous
    if not _ok(deployed):
        print("✗ ./deploy.sh failed.")
        tail = (deployed.stderr or deployed.stdout).strip().splitlines()
        if tail:
            print("\n".join(tail[-10:]))
        return 1
    print("✓ Deployed. Copy the service URL into BIGAS_PUBLIC_URL, then:")
    print("  python scripts/apply_portfolio.py --webhook --apply")
    print("  python scripts/bigas_doctor.py")
    return 0
