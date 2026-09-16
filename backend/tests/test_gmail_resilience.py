from __future__ import annotations

import time

import httpx
import pytest
import respx

from app.connectors.gmail import API_BASE, GmailChannel
from app.connectors.oauth_base import TokenBlob
from app.crypto import ApprovalTokenSigner, TokenCipher


def _live_gmail(settings, storage):
    """A GmailChannel with mock mode off and a stored token, so pull()/
    fetch() actually exercise the real httpx + retry/backoff code path
    instead of the mock branch."""
    settings.google_client_id = "client-id"
    settings.google_client_secret = "client-secret"
    settings.qruit_email_mock = False
    cipher = TokenCipher(settings)
    signer = ApprovalTokenSigner(settings)
    connector = GmailChannel(settings, storage, cipher, signer)
    return connector, signer


@pytest.mark.respx(base_url=API_BASE)
async def test_pull_retries_on_429_then_succeeds(settings, container, respx_mock):
    connector, _ = _live_gmail(settings, container.storage)
    await connector.store_token(
        TokenBlob(access_token="tok", refresh_token="rtok", expires_at=time.time() + 3600,
                  scopes=connector.required_scopes, account_label="a@b.example")
    )

    route = respx_mock.get("/messages").mock(
        side_effect=[
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(200, json={"messages": []}),
        ]
    )

    items = await connector.pull(query="has:attachment")

    assert route.call_count == 2  # first 429 was retried, not raised
    assert items == []


@pytest.mark.respx(base_url=API_BASE)
async def test_fetch_surfaces_404_as_runtime_error_not_retried(settings, container, respx_mock):
    connector, _ = _live_gmail(settings, container.storage)
    await connector.store_token(
        TokenBlob(access_token="tok", refresh_token="rtok", expires_at=time.time() + 3600,
                  scopes=connector.required_scopes, account_label="a@b.example")
    )

    route = respx_mock.get("/messages/deleted-msg").mock(return_value=httpx.Response(404))

    with pytest.raises(RuntimeError, match="no longer exists"):
        await connector.fetch("deleted-msg", "/tmp/qruit-test-fetch")

    assert route.call_count == 1  # 404 is not retryable — no backoff attempts wasted


@pytest.mark.respx(base_url=API_BASE)
async def test_pull_gives_up_after_max_attempts_on_persistent_5xx(settings, container, respx_mock):
    connector, _ = _live_gmail(settings, container.storage)
    await connector.store_token(
        TokenBlob(access_token="tok", refresh_token="rtok", expires_at=time.time() + 3600,
                  scopes=connector.required_scopes, account_label="a@b.example")
    )

    route = respx_mock.get("/messages").mock(return_value=httpx.Response(503))

    with pytest.raises(httpx.HTTPStatusError):
        await connector.pull(query="has:attachment")

    assert route.call_count == 3  # with_backoff()'s default max_attempts
