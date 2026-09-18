from __future__ import annotations

from app.config import Settings
from app.models import AuthState
from app.services.connector_config import load_all_configs


async def test_submit_gmail_credentials_configures_and_flips_mock_off(client, settings):
    resp = client.post(
        "/api/channels/gmail/credentials",
        json={"client_id": "cid-123", "client_secret": "csecret-456", "notify_email": "ops@agency.example"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Configured but not yet connected — no OAuth token exchanged yet.
    assert body["state"] == AuthState.NEEDS_AUTH.value
    assert "click Connect" in body["reason"] or "not yet connected" in body["reason"]

    assert settings.google_client_id == "cid-123"
    assert settings.google_client_secret == "csecret-456"
    assert settings.qruit_email_mock is False


async def test_submit_whatsapp_credentials_is_ready_immediately(client, settings):
    resp = client.post(
        "/api/channels/whatsapp/credentials",
        json={"token": "wa-token", "phone_id": "12345"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == AuthState.READY.value
    assert settings.whatsapp_mock is False


async def test_missing_required_field_returns_400_naming_it(client):
    resp = client.post("/api/channels/gmail/credentials", json={"client_id": "cid-only"})
    assert resp.status_code == 400
    assert "google_client_secret" in resp.json()["detail"]


async def test_unknown_channel_returns_404(client):
    resp = client.post("/api/channels/not-a-real-channel/credentials", json={})
    assert resp.status_code == 404


async def test_sharepoint_credentials_are_all_optional(client):
    # sharepoint_site_id blank is a valid, intentional choice (falls back to
    # the signed-in user's OneDrive) — REQUIRED_FIELDS["sharepoint"] is empty.
    resp = client.post("/api/channels/sharepoint/credentials", json={"folder_path": "Recruiting/Active"})
    assert resp.status_code == 200


async def test_config_survives_a_simulated_restart(client, container, settings):
    """The DB row, not .env, is the source of truth once something's been
    submitted via the API — proven by applying it onto a brand new Settings
    object (standing in for a fresh process) instead of the live one."""
    client.post(
        "/api/channels/outlook/credentials",
        json={"client_id": "ms-id", "client_secret": "ms-secret", "tenant": "tenant-123"},
    )

    # Explicitly blank the MS fields rather than relying on a clean
    # environment — a developer's real backend/.env (needed to test live
    # connectors) would otherwise leak real values in here (see the same
    # fix in tests/test_observability.py).
    fresh_settings = Settings(
        qruit_data_dir=settings.qruit_data_dir, database_url=settings.database_url,
        ms_client_id="", ms_client_secret="", ms_tenant="",
    )
    assert fresh_settings.ms_client_id == ""  # confirms it really is a blank slate first

    await load_all_configs(fresh_settings, container.storage, container.cipher)

    assert fresh_settings.ms_client_id == "ms-id"
    assert fresh_settings.ms_client_secret == "ms-secret"
    assert fresh_settings.ms_tenant == "tenant-123"
    assert fresh_settings.qruit_email_mock is False
