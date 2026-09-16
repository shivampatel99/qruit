from __future__ import annotations

import httpx
import pytest
import redis

from app.services.reqruit_client import ReqruitAuthError, ReqruitClient


@pytest.fixture(autouse=True)
def _reset_circuit_state(settings):
    """The circuit breaker's state lives in the real local Redis (unlike
    Postgres, it isn't test-isolated — see tests/conftest.py), so a breaker
    left open by one test would wrongly short-circuit the next. Reset it
    before and after every test in this file."""
    r = redis.Redis.from_url(settings.redis_url)
    keys = ["circuit:reqruit:failures", "circuit:reqruit:open",
            "circuit:reqruit-interview:failures", "circuit:reqruit-interview:open"]
    r.delete(*keys)
    yield
    r.delete(*keys)


def _live_client(settings) -> ReqruitClient:
    """A ReqruitClient with mock mode off, so calls hit respx's mocked
    routes over the real httpx + retry/circuit-breaker code path."""
    settings.qruit_mock_api = False
    settings.reqruit_auth_token = "test-token"
    return ReqruitClient(settings)


@pytest.mark.respx(base_url="https://deep-screening.reqruit.ai")
async def test_extract_jd_retries_on_503_then_succeeds(settings, respx_mock):
    client = _live_client(settings)
    route = respx_mock.post("/jd/extract").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"role_title": "Backend Engineer"})]
    )

    result = await client.extract_jd("https://qruit.example/files/jd.pdf")

    assert route.call_count == 2  # first 503 was retried, not raised
    assert result == {"role_title": "Backend Engineer"}


@pytest.mark.respx(base_url="https://deep-screening.reqruit.ai")
async def test_extract_jd_surfaces_bad_token_without_retrying(settings, respx_mock):
    client = _live_client(settings)
    route = respx_mock.post("/jd/extract").mock(return_value=httpx.Response(401))

    with pytest.raises(ReqruitAuthError):
        await client.extract_jd("https://qruit.example/files/jd.pdf")

    assert route.call_count == 1  # auth errors aren't transient — retrying wastes time


@pytest.mark.respx(base_url="https://deep-screening.reqruit.ai")
async def test_run_deep_screening_gives_up_after_max_attempts_on_persistent_5xx(settings, respx_mock):
    client = _live_client(settings)
    route = respx_mock.post("/deep-screening/run").mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await client.run_deep_screening({"must_have_skills": ["python"]}, [{"id": "r1", "skills": ["python"]}])

    assert route.call_count == 3  # with_backoff()'s default max_attempts


@pytest.mark.respx(base_url="https://deep-screening.reqruit.ai")
async def test_circuit_opens_after_repeated_failures_and_stops_calling_out(settings, respx_mock):
    """Loop-doc §1.2/§5: once Reqruit.ai has failed enough times in a row,
    QRUIT must stop hammering it instead of retrying forever. with_backoff()
    burns through 3 attempts against a dead route, and each attempt records
    one breaker failure — with threshold=3 the breaker opens on the very
    call that exhausts those retries. The next call must then be
    short-circuited before any HTTP request is made."""
    from app.services.resilience import CircuitOpenError

    client = _live_client(settings)
    client._breaker.threshold = 3
    route = respx_mock.post("/jd/extract").mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await client.extract_jd("https://qruit.example/files/jd.pdf")

    assert route.call_count == 3
    assert client._breaker.is_open()

    with pytest.raises(CircuitOpenError):
        await client.extract_jd("https://qruit.example/files/jd.pdf")

    assert route.call_count == 3  # short-circuited — no network call made
