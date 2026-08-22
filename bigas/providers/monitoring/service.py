"""
Website monitoring service for checking HTTP availability and SSL certificate health.

Uses native Python libraries (requests, ssl, socket) for checks rather than shelling
out to curl, providing cleaner error handling and certificate parsing.
"""
from __future__ import annotations

import logging
import os
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 10
SSL_TIMEOUT_SECONDS = 10
SSL_EXPIRY_WARNING_DAYS = 14
USER_AGENT = "Mozilla/5.0 (compatible; BigasMonitor/1.0)"
MAX_CONCURRENT_CHECKS = 10


@dataclass
class UrlCheckResult:
    """Result of checking a single URL."""

    url: str
    is_healthy: bool
    errors: list[str] = field(default_factory=list)
    http_status: Optional[int] = None
    ssl_days_until_expiry: Optional[int] = None


@dataclass
class MonitoringResult:
    """Result of running all monitoring checks."""

    total_urls: int
    healthy_count: int
    unhealthy_count: int
    results: list[UrlCheckResult]
    alert_message: Optional[str] = None
    alerts_sent: bool = False


def _check_http_status(url: str) -> tuple[Optional[int], Optional[str], bool]:
    """
    Perform an HTTP GET request to the URL.

    Returns:
        (status_code, error_message, is_connection_failure) - error_message is None if successful.
        is_connection_failure is True if the error was a connection/timeout issue.
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(
            url, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=True, headers=headers
        )
        if response.status_code >= 400:
            return response.status_code, f"HTTP {response.status_code}", False
        return response.status_code, None, False
    except requests.exceptions.Timeout:
        return None, "Connection timed out", True
    except requests.exceptions.SSLError as e:
        return None, f"SSL error: {str(e)[:100]}", False
    except requests.exceptions.ConnectionError as e:
        return None, f"Connection error: {str(e)[:100]}", True
    except requests.exceptions.RequestException as e:
        return None, f"Request failed: {str(e)[:100]}", False


def _check_ssl_certificate(hostname: str, port: int = 443) -> tuple[Optional[int], Optional[str]]:
    """
    Check the SSL certificate expiration date for a host.

    Returns:
        (days_until_expiry, error_message) - error_message is None if cert is valid
        and not expiring soon.
    """
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, port), timeout=SSL_TIMEOUT_SECONDS) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                if not cert:
                    return None, "No certificate returned"

                not_after_str = cert.get("notAfter")
                if not not_after_str:
                    return None, "Certificate missing notAfter field"

                timestamp = ssl.cert_time_to_seconds(not_after_str)
                not_after = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                now = datetime.now(timezone.utc)
                days_until_expiry = (not_after - now).days

                if days_until_expiry < SSL_EXPIRY_WARNING_DAYS:
                    return days_until_expiry, f"SSL certificate expires in {days_until_expiry} days"

                return days_until_expiry, None

    except ssl.SSLError as e:
        return None, f"SSL error: {str(e)[:100]}"
    except socket.timeout:
        return None, "SSL connection timed out"
    except socket.error as e:
        return None, f"Socket error: {str(e)[:100]}"
    except Exception as e:
        return None, f"SSL check failed: {str(e)[:100]}"


def check_url(url: str) -> UrlCheckResult:
    """
    Check a URL for HTTP availability and SSL certificate health.

    Args:
        url: The URL to check (e.g., https://example.com)

    Returns:
        UrlCheckResult with errors if any issues were detected.
    """
    errors: list[str] = []
    http_status: Optional[int] = None
    ssl_days: Optional[int] = None

    status, http_error, is_connection_failure = _check_http_status(url)
    http_status = status
    if http_error:
        errors.append(http_error)

    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname and not is_connection_failure:
        port = parsed.port or 443
        ssl_days, ssl_error = _check_ssl_certificate(parsed.hostname, port)
        if ssl_error:
            errors.append(ssl_error)

    return UrlCheckResult(
        url=url,
        is_healthy=len(errors) == 0,
        errors=errors,
        http_status=http_status,
        ssl_days_until_expiry=ssl_days,
    )


def _get_configured_urls() -> list[str]:
    """
    Parse MONITOR_URLS environment variable into a list of URLs.

    Format: comma-separated list of URLs.
    Example: https://site1.com,https://site2.com
    """
    raw = os.environ.get("MONITOR_URLS", "").strip()
    if not raw:
        return []
    return [url.strip() for url in raw.split(",") if url.strip()]


def _format_alert_message(unhealthy_results: list[UrlCheckResult]) -> str:
    """Format an alert message for unhealthy URLs."""
    lines = ["🚨 **Website Monitoring Alerts** 🚨", ""]
    for result in unhealthy_results:
        error_str = "; ".join(result.errors)
        lines.append(f"• **{result.url}**: {error_str}")
    return "\n".join(lines)


def _send_alert(message: str) -> bool:
    """
    Send an alert message to the configured notification channel.

    Uses DISCORD_WEBHOOK_URL_CTO for ops/engineering alerts.
    Falls back to DISCORD_WEBHOOK_URL_PRODUCT if CTO channel not set.
    """
    from bigas.discord_webhook import post_long_to_discord

    webhook_url = (
        os.environ.get("DISCORD_WEBHOOK_URL_CTO")
        or os.environ.get("DISCORD_WEBHOOK_URL_PRODUCT")
        or os.environ.get("DISCORD_WEBHOOK_URL_MARKETING")
    )

    posted = bool(webhook_url) and not webhook_url.strip().lower().startswith("placeholder")
    if not posted:
        logger.warning("No Discord webhook configured for monitoring alerts")
    post_long_to_discord(webhook_url, message, chat_agent_id="cto")
    return posted


def run_monitoring_checks() -> MonitoringResult:
    """
    Run monitoring checks on all configured URLs.

    Reads URLs from MONITOR_URLS environment variable, checks each for HTTP
    availability and SSL certificate health concurrently, and sends alerts
    for any failures.

    Returns:
        MonitoringResult with details about all checks.
    """
    urls = _get_configured_urls()
    if not urls:
        logger.info("No URLs configured in MONITOR_URLS, skipping monitoring checks")
        return MonitoringResult(
            total_urls=0,
            healthy_count=0,
            unhealthy_count=0,
            results=[],
            alert_message=None,
            alerts_sent=False,
        )

    results: list[UrlCheckResult] = []
    with ThreadPoolExecutor(max_workers=min(len(urls), MAX_CONCURRENT_CHECKS)) as executor:
        future_to_url = {executor.submit(check_url, url): url for url in urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            logger.info("Checking URL: %s", url)
            try:
                result = future.result()
            except Exception as e:
                logger.error("Unexpected error checking URL %s: %s", url, e)
                result = UrlCheckResult(
                    url=url,
                    is_healthy=False,
                    errors=[f"Unexpected error: {str(e)[:100]}"],
                )
            results.append(result)
            if result.is_healthy:
                logger.info("URL %s is healthy", url)
            else:
                logger.warning("URL %s has issues: %s", url, result.errors)

    unhealthy = [r for r in results if not r.is_healthy]
    healthy_count = len(results) - len(unhealthy)

    alert_message: Optional[str] = None
    alerts_sent = False

    if unhealthy:
        alert_message = _format_alert_message(unhealthy)
        alerts_sent = _send_alert(alert_message)
        if alerts_sent:
            logger.info("Alert sent for %d unhealthy URL(s)", len(unhealthy))
        else:
            logger.warning("Failed to send alert for unhealthy URLs")

    return MonitoringResult(
        total_urls=len(urls),
        healthy_count=healthy_count,
        unhealthy_count=len(unhealthy),
        results=results,
        alert_message=alert_message,
        alerts_sent=alerts_sent,
    )


class MonitoringService:
    """
    Service class for website monitoring, compatible with Bigas provider pattern.
    """

    @staticmethod
    def is_configured() -> bool:
        """Check if monitoring is configured (MONITOR_URLS is set)."""
        return bool(_get_configured_urls())

    def run_checks(self) -> MonitoringResult:
        """Run monitoring checks on all configured URLs."""
        return run_monitoring_checks()
