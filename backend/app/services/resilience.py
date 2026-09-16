from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class CircuitOpenError(Exception):
    """Raised instead of making a call when a service's circuit is open."""


def _is_retryable_httpx_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def with_backoff(max_attempts: int = 3):
    """Retries a call with exponential backoff + jitter on 429/5xx/timeouts.
    Wrap the specific httpx call, not the whole connector method, so a
    non-retryable error (404, bad auth) fails immediately instead of being
    retried pointlessly (loop-doc §1.2)."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=0.5, max=8),
        retry=retry_if_exception(_is_retryable_httpx_error),
    )


class CircuitBreaker:
    """A failure counter + open-state flag shared across worker processes via
    Redis, so one flaky provider trips the breaker for every worker at once,
    not just the process that happened to notice (loop-doc §1.2, §1.9).

    closed -> (>= threshold consecutive failures) -> open (blocks calls for
    cooldown_seconds) -> (cooldown elapses) -> a single trial call is let
    through to test recovery; success re-closes, failure re-opens.
    """

    def __init__(self, redis_client, service: str, threshold: int = 5, cooldown_seconds: int = 30):
        self.redis = redis_client
        self.service = service
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds

    def _key(self, suffix: str) -> str:
        return f"circuit:{self.service}:{suffix}"

    def is_open(self) -> bool:
        return self.redis.exists(self._key("open")) == 1

    def record_success(self) -> None:
        self.redis.delete(self._key("failures"))
        self.redis.delete(self._key("open"))

    def record_failure(self) -> None:
        failures = self.redis.incr(self._key("failures"))
        self.redis.expire(self._key("failures"), self.cooldown_seconds * 4)
        if failures >= self.threshold:
            self.redis.set(self._key("open"), "1", ex=self.cooldown_seconds)

    def guard(self) -> "_CircuitGuard":
        return _CircuitGuard(self)


class _CircuitGuard:
    """`with breaker.guard(): ...` — raises CircuitOpenError up front if the
    circuit is open; otherwise records success/failure around the call
    without ever swallowing the underlying exception."""

    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker

    def __enter__(self) -> "_CircuitGuard":
        if self.breaker.is_open():
            raise CircuitOpenError(f"{self.breaker.service}: circuit open, short-circuiting call")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.breaker.record_success()
        elif issubclass(exc_type, (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError)):
            self.breaker.record_failure()
        return False
