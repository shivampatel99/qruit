from __future__ import annotations

from app.models import AuthState


async def test_mock_ready_is_flagged_as_not_a_real_connection(client):
    resp = client.get("/api/channels")
    body = resp.json()
    assert body["gmail"]["state"] == AuthState.READY.value
    assert body["gmail"]["mock"] is True  # no real credentials configured — this is the fallback, not a real link
    assert body["whatsapp"]["mock"] is True


async def test_disconnect_gmail_clears_credentials_and_falls_back_to_mock(client, settings):
    client.post("/api/channels/gmail/credentials", json={"client_id": "cid", "client_secret": "csecret"})
    assert settings.google_client_id == "cid"
    assert settings.qruit_email_mock is False

    resp = client.post("/api/channels/gmail/disconnect")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == AuthState.READY.value
    assert body["mock"] is True

    assert settings.google_client_id == ""
    assert settings.google_client_secret == ""
    assert settings.qruit_email_mock is True  # safe to re-enable — outlook has no credentials either


async def test_disconnecting_gmail_does_not_break_a_still_configured_outlook(client, settings):
    client.post("/api/channels/gmail/credentials", json={"client_id": "cid", "client_secret": "csecret"})
    client.post(
        "/api/channels/outlook/credentials",
        json={"client_id": "ms-id", "client_secret": "ms-secret", "tenant": "tenant-1"},
    )
    assert settings.qruit_email_mock is False

    client.post("/api/channels/gmail/disconnect")

    # The shared mock flag must NOT flip back on — outlook still has real credentials.
    assert settings.qruit_email_mock is False
    assert settings.ms_client_id == "ms-id"


async def test_disconnect_removes_oauth_token(client, container):
    from app.connectors.oauth_base import TokenBlob

    connector = container.registry.get("gmail")
    connector.settings.google_client_id = "cid"
    connector.settings.google_client_secret = "csecret"
    await connector.store_token(
        TokenBlob(access_token="tok", refresh_token="rtok", expires_at=9_999_999_999, scopes=connector.required_scopes)
    )
    assert (await connector.load_token()) is not None

    resp = client.post("/api/channels/gmail/disconnect")
    assert resp.status_code == 200

    assert (await connector.load_token()) is None


async def test_disconnecting_gdrive_does_not_remove_gmails_shared_oauth_token(client, container):
    from app.connectors.oauth_base import TokenBlob

    gmail = container.registry.get("gmail")
    gmail.settings.google_client_id = "cid"
    gmail.settings.google_client_secret = "csecret"
    await gmail.store_token(
        TokenBlob(access_token="tok", refresh_token="rtok", expires_at=9_999_999_999, scopes=gmail.required_scopes)
    )

    resp = client.post("/api/channels/gdrive/disconnect")
    assert resp.status_code == 200

    # gdrive only owns the folder_id config, not the shared gmail token.
    assert (await gmail.load_token()) is not None


async def test_disconnect_unknown_channel_returns_404(client):
    resp = client.post("/api/channels/not-a-channel/disconnect")
    assert resp.status_code == 404
