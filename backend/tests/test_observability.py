from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services.resilience import CircuitBreaker, CircuitOpenError


def test_api_star_rejected_via_public_hostname(client):
    # QRUIT_PUBLIC_BASE_URL defaults to http://localhost:8787 in Settings,
    # so a request whose Host header matches "localhost" must be rejected —
    # /api/* is internal/VPN-only (FRD §5.6, NW-3); this is the DoD item
    # demanding an actual test, not just trusting the router config.
    resp = client.get("/api/roles", headers={"Host": "localhost"})
    assert resp.status_code == 403


def test_api_star_allowed_via_internal_hostname(client):
    resp = client.get("/api/roles", headers={"Host": "qruit-internal.local"})
    assert resp.status_code == 200


def test_oauth_callback_is_exempt_from_hostname_guard(client):
    # The OAuth redirect URI is registered as {PUBLIC_BASE_URL}/api/oauth/callback,
    # so Google/Microsoft necessarily call it back via the public hostname —
    # a named, narrow exception (see PublicHostnameGuardMiddleware docstring).
    resp = client.get(
        "/api/oauth/callback", params={"state": "unknown"}, headers={"Host": "localhost"},
    )
    assert resp.status_code == 200


def test_config_fails_fast_when_live_email_mode_missing_credentials():
    # Explicitly blank the credential fields rather than relying on a clean
    # environment — a developer's real backend/.env (needed to actually test
    # live connectors) would otherwise leak real values in here and falsify
    # this test's premise.
    settings = Settings(
        qruit_email_mock=False, qruit_approval_secret="a-real-secret",
        google_client_id="", google_client_secret="",
        ms_client_id="", ms_client_secret="", ms_tenant="",
    )
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        settings.validate_for_boot()


def test_config_boots_fine_in_default_mock_mode():
    Settings().validate_for_boot()  # must not raise


class FakeRedis:
    """Minimal in-memory stand-in — avoids a fakeredis dependency edge case
    with EXPIRE/TTL semantics while still exercising the real breaker logic."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def incr(self, key: str) -> int:
        self._store[key] = str(int(self._store.get(key, "0")) + 1)
        return int(self._store[key])

    def expire(self, key: str, seconds: int) -> None:
        pass

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value


def test_circuit_breaker_opens_after_threshold_failures():
    breaker = CircuitBreaker(FakeRedis(), service="reqruit", threshold=3, cooldown_seconds=30)
    for _ in range(3):
        with pytest.raises(httpx.TimeoutException):
            with breaker.guard():
                raise httpx.TimeoutException("boom")
    assert breaker.is_open() is True
    with pytest.raises(CircuitOpenError):
        with breaker.guard():
            pass  # never reached — circuit is open


def test_circuit_breaker_ignores_non_network_errors():
    # A business-logic error (bad JD data, validation failure) must not trip
    # an infrastructure circuit breaker meant for provider outages.
    breaker = CircuitBreaker(FakeRedis(), service="reqruit", threshold=1, cooldown_seconds=30)
    with pytest.raises(ValueError):
        with breaker.guard():
            raise ValueError("not a network problem")
    assert breaker.is_open() is False


def test_circuit_breaker_recloses_after_success():
    breaker = CircuitBreaker(FakeRedis(), service="reqruit", threshold=2, cooldown_seconds=30)
    with pytest.raises(httpx.TimeoutException):
        with breaker.guard():
            raise httpx.TimeoutException("boom")
    with breaker.guard():
        pass  # a success before hitting threshold clears the failure count
    assert breaker.is_open() is False
