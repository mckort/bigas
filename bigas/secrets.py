"""
Load environment variables from Google Cloud Secret Manager when SECRET_MANAGER=true.

Only runs when DEPLOYMENT_MODE=standalone. Set SECRET_MANAGER_SECRET_NAMES to a comma-separated
list of secret names. Each name is also the env var name; the secret's payload (plain text)
is set as os.environ[name]. E.g. secret "DISCORD_WEBHOOK_URL_MARKETING" -> env DISCORD_WEBHOOK_URL_MARKETING.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _is_secret_manager_enabled() -> bool:
    raw = (os.environ.get("SECRET_MANAGER") or "").strip().lower()
    return raw in ("true", "1", "yes")


def _is_standalone() -> bool:
    return (os.environ.get("DEPLOYMENT_MODE") or "standalone").strip().lower() == "standalone"


def _get_project_id() -> str | None:
    project_id = (
        (os.environ.get("GOOGLE_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    )
    if project_id:
        return project_id
    try:
        import google.auth
        _, project_id = google.auth.default()
        return project_id or None
    except Exception:
        return None


def _fetch_secret(client, project_id: str, secret_name: str) -> str | None:
    """Fetch latest version of a secret; return payload as string or None on failure."""
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8")
    except Exception as e:
        logger.warning("Failed to access secret '%s': %s", secret_name, e)
        return None


def _load_one_secret_per_setting(
    client, project_id: str, secret_names: list[str]
) -> int:
    """Fetch each secret by name and set os.environ[name] = payload. Return count set."""
    count = 0
    for name in secret_names:
        name = name.strip()
        if not name:
            continue
        value = _fetch_secret(client, project_id, name)
        if value is not None:
            os.environ[name] = value
            count += 1
    return count


def load_secrets_from_secret_manager() -> bool:
    """
    If SECRET_MANAGER=true and DEPLOYMENT_MODE=standalone, fetch secrets from Google
    Secret Manager and set them in os.environ. Returns True if any secrets were loaded.
    """
    if not _is_secret_manager_enabled():
        return False
    if not _is_standalone():
        logger.info("SECRET_MANAGER=true but DEPLOYMENT_MODE is not 'standalone'; skipping Secret Manager load.")
        return False

    try:
        from google.cloud import secretmanager
    except ImportError:
        logger.warning(
            "SECRET_MANAGER=true but google-cloud-secret-manager is not installed. "
            "Install it or set SECRET_MANAGER=false."
        )
        return False

    project_id = _get_project_id()
    if not project_id:
        logger.warning(
            "No GOOGLE_PROJECT_ID or GOOGLE_CLOUD_PROJECT set and default credentials have no project."
        )
        return False

    try:
        client = secretmanager.SecretManagerServiceClient()
    except Exception as e:
        logger.error("Failed to create Secret Manager client: %s", e, exc_info=True)
        return False

    raw_names = (os.environ.get("SECRET_MANAGER_SECRET_NAMES") or "").strip()
    if not raw_names:
        logger.warning(
            "SECRET_MANAGER=true but SECRET_MANAGER_SECRET_NAMES is empty. "
            "Set it to a comma-separated list of secret names (each name = env var name)."
        )
        return False

    secret_names = [n.strip() for n in raw_names.split(",") if n.strip()]
    count = _load_one_secret_per_setting(client, project_id, secret_names)
    if count > 0:
        logger.info(
            "Loaded %d env var(s) from Secret Manager: %s.",
            count,
            ", ".join(secret_names),
        )
    return count > 0
