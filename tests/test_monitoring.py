"""
Unit tests for the website monitoring functionality.
"""
import os
import socket
import ssl
from datetime import datetime, timezone, timedelta
from unittest import mock

import pytest
import requests

from bigas.providers.monitoring.service import (
    UrlCheckResult,
    MonitoringResult,
    check_url,
    run_monitoring_checks,
    _check_http_status,
    _check_ssl_certificate,
    _get_configured_urls,
    _format_alert_message,
    MonitoringService,
)


class TestGetConfiguredUrls:
    """Tests for URL configuration parsing."""

    def test_empty_env_var(self):
        """Empty MONITOR_URLS returns empty list."""
        with mock.patch.dict(os.environ, {"MONITOR_URLS": ""}, clear=False):
            assert _get_configured_urls() == []

    def test_missing_env_var(self):
        """Missing MONITOR_URLS returns empty list."""
        env = os.environ.copy()
        env.pop("MONITOR_URLS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            assert _get_configured_urls() == []

    def test_single_url(self):
        """Single URL is parsed correctly."""
        with mock.patch.dict(os.environ, {"MONITOR_URLS": "https://example.com"}, clear=False):
            assert _get_configured_urls() == ["https://example.com"]

    def test_multiple_urls(self):
        """Multiple comma-separated URLs are parsed correctly."""
        with mock.patch.dict(os.environ, {"MONITOR_URLS": "https://a.com,https://b.com,https://c.com"}, clear=False):
            assert _get_configured_urls() == ["https://a.com", "https://b.com", "https://c.com"]

    def test_whitespace_handling(self):
        """Whitespace around URLs is trimmed."""
        with mock.patch.dict(os.environ, {"MONITOR_URLS": " https://a.com , https://b.com "}, clear=False):
            assert _get_configured_urls() == ["https://a.com", "https://b.com"]

    def test_empty_entries_filtered(self):
        """Empty entries from extra commas are filtered."""
        with mock.patch.dict(os.environ, {"MONITOR_URLS": "https://a.com,,https://b.com,"}, clear=False):
            assert _get_configured_urls() == ["https://a.com", "https://b.com"]


class TestCheckHttpStatus:
    """Tests for HTTP status checking."""

    def test_successful_request(self):
        """HTTP 200 returns status, no error, and not a connection failure."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        with mock.patch("requests.get", return_value=mock_response):
            status, error, is_conn_failure = _check_http_status("https://example.com")
            assert status == 200
            assert error is None
            assert is_conn_failure is False

    def test_redirect_success(self):
        """HTTP redirect followed to 200 returns success."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        with mock.patch("requests.get", return_value=mock_response):
            status, error, is_conn_failure = _check_http_status("https://example.com")
            assert status == 200
            assert error is None
            assert is_conn_failure is False

    def test_404_error(self):
        """HTTP 404 returns status, error, and not a connection failure."""
        mock_response = mock.Mock()
        mock_response.status_code = 404
        with mock.patch("requests.get", return_value=mock_response):
            status, error, is_conn_failure = _check_http_status("https://example.com")
            assert status == 404
            assert error == "HTTP 404"
            assert is_conn_failure is False

    def test_500_error(self):
        """HTTP 500 returns status, error, and not a connection failure."""
        mock_response = mock.Mock()
        mock_response.status_code = 500
        with mock.patch("requests.get", return_value=mock_response):
            status, error, is_conn_failure = _check_http_status("https://example.com")
            assert status == 500
            assert error == "HTTP 500"
            assert is_conn_failure is False

    def test_timeout(self):
        """Timeout returns None status, error message, and is a connection failure."""
        with mock.patch("requests.get", side_effect=requests.exceptions.Timeout()):
            status, error, is_conn_failure = _check_http_status("https://example.com")
            assert status is None
            assert error == "Connection timed out"
            assert is_conn_failure is True

    def test_connection_error(self):
        """Connection error returns None status, error message, and is a connection failure."""
        with mock.patch("requests.get", side_effect=requests.exceptions.ConnectionError("DNS failed")):
            status, error, is_conn_failure = _check_http_status("https://example.com")
            assert status is None
            assert "Connection error" in error
            assert is_conn_failure is True

    def test_ssl_error_in_http_check(self):
        """SSL error during HTTP check returns error and is not a connection failure."""
        with mock.patch("requests.get", side_effect=requests.exceptions.SSLError("cert verify failed")):
            status, error, is_conn_failure = _check_http_status("https://example.com")
            assert status is None
            assert "SSL error" in error
            assert is_conn_failure is False


class TestCheckSslCertificate:
    """Tests for SSL certificate checking."""

    def test_certificate_valid_far_future(self):
        """Certificate valid for > 14 days returns days and no error."""
        future_date = datetime.now(timezone.utc) + timedelta(days=90)
        mock_cert = {"notAfter": future_date.strftime("%b %d %H:%M:%S %Y GMT")}

        mock_sock = mock.MagicMock()
        mock_ssock = mock.MagicMock()
        mock_ssock.getpeercert.return_value = mock_cert
        mock_sock.__enter__ = mock.Mock(return_value=mock_sock)
        mock_sock.__exit__ = mock.Mock(return_value=False)
        mock_ssock.__enter__ = mock.Mock(return_value=mock_ssock)
        mock_ssock.__exit__ = mock.Mock(return_value=False)

        with mock.patch("socket.create_connection", return_value=mock_sock):
            with mock.patch("ssl.SSLContext.wrap_socket", return_value=mock_ssock):
                days, error = _check_ssl_certificate("example.com")
                assert days is not None
                assert days >= 89  # Allow for test timing
                assert error is None

    def test_certificate_expiring_soon(self):
        """Certificate expiring in < 14 days returns warning."""
        future_date = datetime.now(timezone.utc) + timedelta(days=7)
        mock_cert = {"notAfter": future_date.strftime("%b %d %H:%M:%S %Y GMT")}

        mock_sock = mock.MagicMock()
        mock_ssock = mock.MagicMock()
        mock_ssock.getpeercert.return_value = mock_cert
        mock_sock.__enter__ = mock.Mock(return_value=mock_sock)
        mock_sock.__exit__ = mock.Mock(return_value=False)
        mock_ssock.__enter__ = mock.Mock(return_value=mock_ssock)
        mock_ssock.__exit__ = mock.Mock(return_value=False)

        with mock.patch("socket.create_connection", return_value=mock_sock):
            with mock.patch("ssl.SSLContext.wrap_socket", return_value=mock_ssock):
                days, error = _check_ssl_certificate("example.com")
                assert days is not None
                assert days < 14
                assert "expires in" in error

    def test_certificate_expired_in_production_raises_ssl_error(self):
        """In production, expired certificates raise SSLError during handshake.

        Note: ssl.create_default_context() enforces certificate validation by default,
        so wrap_socket raises ssl.SSLCertVerificationError if the certificate is expired.
        This test verifies that SSLError is handled correctly.
        """
        with mock.patch("socket.create_connection") as mock_conn:
            mock_sock = mock.MagicMock()
            mock_conn.return_value.__enter__ = mock.Mock(return_value=mock_sock)
            mock_conn.return_value.__exit__ = mock.Mock(return_value=False)
            with mock.patch("ssl.SSLContext.wrap_socket", side_effect=ssl.SSLError("certificate verify failed")):
                days, error = _check_ssl_certificate("example.com")
                assert days is None
                assert "SSL error" in error

    def test_ssl_connection_error(self):
        """SSL connection error returns error message."""
        with mock.patch("socket.create_connection", side_effect=ssl.SSLError("handshake failed")):
            days, error = _check_ssl_certificate("example.com")
            assert days is None
            assert "SSL error" in error

    def test_socket_timeout(self):
        """Socket timeout returns error message."""
        with mock.patch("socket.create_connection", side_effect=socket.timeout()):
            days, error = _check_ssl_certificate("example.com")
            assert days is None
            assert "timed out" in error


class TestCheckUrl:
    """Tests for the combined URL check."""

    def test_healthy_https_url(self):
        """Healthy HTTPS URL returns no errors."""
        future_date = datetime.now(timezone.utc) + timedelta(days=90)
        mock_cert = {"notAfter": future_date.strftime("%b %d %H:%M:%S %Y GMT")}

        mock_response = mock.Mock()
        mock_response.status_code = 200

        mock_sock = mock.MagicMock()
        mock_ssock = mock.MagicMock()
        mock_ssock.getpeercert.return_value = mock_cert
        mock_sock.__enter__ = mock.Mock(return_value=mock_sock)
        mock_sock.__exit__ = mock.Mock(return_value=False)
        mock_ssock.__enter__ = mock.Mock(return_value=mock_ssock)
        mock_ssock.__exit__ = mock.Mock(return_value=False)

        with mock.patch("requests.get", return_value=mock_response):
            with mock.patch("socket.create_connection", return_value=mock_sock):
                with mock.patch("ssl.SSLContext.wrap_socket", return_value=mock_ssock):
                    result = check_url("https://example.com")
                    assert result.is_healthy is True
                    assert result.errors == []
                    assert result.http_status == 200
                    assert result.ssl_days_until_expiry >= 89

    def test_unhealthy_http_error(self):
        """HTTP error returns unhealthy result."""
        mock_response = mock.Mock()
        mock_response.status_code = 503

        with mock.patch("requests.get", return_value=mock_response):
            result = check_url("http://example.com")
            assert result.is_healthy is False
            assert "HTTP 503" in result.errors
            assert result.http_status == 503

    def test_unhealthy_ssl_expiring(self):
        """SSL expiring soon returns unhealthy result."""
        future_date = datetime.now(timezone.utc) + timedelta(days=5)
        mock_cert = {"notAfter": future_date.strftime("%b %d %H:%M:%S %Y GMT")}

        mock_response = mock.Mock()
        mock_response.status_code = 200

        mock_sock = mock.MagicMock()
        mock_ssock = mock.MagicMock()
        mock_ssock.getpeercert.return_value = mock_cert
        mock_sock.__enter__ = mock.Mock(return_value=mock_sock)
        mock_sock.__exit__ = mock.Mock(return_value=False)
        mock_ssock.__enter__ = mock.Mock(return_value=mock_ssock)
        mock_ssock.__exit__ = mock.Mock(return_value=False)

        with mock.patch("requests.get", return_value=mock_response):
            with mock.patch("socket.create_connection", return_value=mock_sock):
                with mock.patch("ssl.SSLContext.wrap_socket", return_value=mock_ssock):
                    result = check_url("https://example.com")
                    assert result.is_healthy is False
                    assert any("expires" in e for e in result.errors)

    def test_http_url_no_ssl_check(self):
        """HTTP URL does not trigger SSL check."""
        mock_response = mock.Mock()
        mock_response.status_code = 200

        with mock.patch("requests.get", return_value=mock_response):
            result = check_url("http://example.com")
            assert result.is_healthy is True
            assert result.ssl_days_until_expiry is None


class TestFormatAlertMessage:
    """Tests for alert message formatting."""

    def test_single_error(self):
        """Single unhealthy URL formats correctly."""
        results = [
            UrlCheckResult(
                url="https://example.com",
                is_healthy=False,
                errors=["HTTP 500"],
            )
        ]
        message = _format_alert_message(results)
        assert "Website Monitoring Alerts" in message
        assert "https://example.com" in message
        assert "HTTP 500" in message

    def test_multiple_errors(self):
        """Multiple unhealthy URLs format correctly."""
        results = [
            UrlCheckResult(url="https://a.com", is_healthy=False, errors=["HTTP 500"]),
            UrlCheckResult(url="https://b.com", is_healthy=False, errors=["Connection timed out"]),
        ]
        message = _format_alert_message(results)
        assert "https://a.com" in message
        assert "https://b.com" in message
        assert "HTTP 500" in message
        assert "Connection timed out" in message


class TestRunMonitoringChecks:
    """Tests for the main monitoring function."""

    def test_no_urls_configured(self):
        """No URLs configured returns empty result."""
        env = os.environ.copy()
        env.pop("MONITOR_URLS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            result = run_monitoring_checks()
            assert result.total_urls == 0
            assert result.healthy_count == 0
            assert result.unhealthy_count == 0
            assert result.alerts_sent is False

    def test_all_healthy(self):
        """All healthy URLs returns success, no alerts."""
        mock_response = mock.Mock()
        mock_response.status_code = 200

        with mock.patch.dict(os.environ, {"MONITOR_URLS": "http://a.com,http://b.com"}, clear=False):
            with mock.patch("requests.get", return_value=mock_response):
                with mock.patch("bigas.providers.monitoring.service._send_alert") as mock_alert:
                    result = run_monitoring_checks()
                    assert result.total_urls == 2
                    assert result.healthy_count == 2
                    assert result.unhealthy_count == 0
                    assert result.alerts_sent is False
                    mock_alert.assert_not_called()

    def test_some_unhealthy_sends_alert(self):
        """Unhealthy URLs trigger alert."""
        def mock_get(url, **kwargs):
            response = mock.Mock()
            if "good" in url:
                response.status_code = 200
            else:
                response.status_code = 500
            return response

        with mock.patch.dict(os.environ, {"MONITOR_URLS": "http://good.com,http://bad.com"}, clear=False):
            with mock.patch("requests.get", side_effect=mock_get):
                with mock.patch("bigas.providers.monitoring.service._send_alert", return_value=True) as mock_alert:
                    result = run_monitoring_checks()
                    assert result.total_urls == 2
                    assert result.healthy_count == 1
                    assert result.unhealthy_count == 1
                    assert result.alerts_sent is True
                    mock_alert.assert_called_once()


class TestMonitoringService:
    """Tests for the MonitoringService class."""

    def test_is_configured_true(self):
        """is_configured returns True when MONITOR_URLS is set."""
        with mock.patch.dict(os.environ, {"MONITOR_URLS": "https://example.com"}, clear=False):
            assert MonitoringService.is_configured() is True

    def test_is_configured_false(self):
        """is_configured returns False when MONITOR_URLS is empty."""
        env = os.environ.copy()
        env.pop("MONITOR_URLS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            assert MonitoringService.is_configured() is False

    def test_run_checks(self):
        """run_checks delegates to run_monitoring_checks."""
        with mock.patch("bigas.providers.monitoring.service.run_monitoring_checks") as mock_run:
            mock_run.return_value = MonitoringResult(
                total_urls=1, healthy_count=1, unhealthy_count=0, results=[]
            )
            service = MonitoringService()
            result = service.run_checks()
            assert result.total_urls == 1
            mock_run.assert_called_once()
