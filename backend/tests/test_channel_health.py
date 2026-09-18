from __future__ import annotations

from app.worker import check_channel_health


async def test_reauth_alert_sent_once_then_deduped(client, container):
    """gmail is configured (real client_id/secret) but never connected, so
    auth_status() is NEEDS_AUTH. Outlook has no credentials of its own, so
    it stays in its mock/ready fallback — exactly the "send the alert about
    the broken channel from whichever other channel still works" case."""
    resp = client.post(
        "/api/channels/gmail/credentials",
        json={"client_id": "cid", "client_secret": "csecret", "notify_email": "ops@agency.example"},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "needs_auth"

    result = await check_channel_health({"container": container})
    assert result["alerted"] == ["gmail"]

    config = await container.storage.get_connector_config("gmail")
    assert config["last_reauth_alert_at"]

    audit = await container.storage.list_audit()
    sent = [a for a in audit if a.alert_type == "system_alert" and a.recipient == "ops@agency.example"]
    assert len(sent) == 1
    assert sent[0].status == "sent"
    assert sent[0].channel == "outlook"  # picked as the still-working connector

    # Immediately re-running must not re-send — cooldown window still open.
    result_again = await check_channel_health({"container": container})
    assert result_again["alerted"] == []
    audit_after = await container.storage.list_audit()
    assert len([a for a in audit_after if a.alert_type == "system_alert"]) == 1


async def test_no_alert_when_notify_email_not_configured(client, container):
    resp = client.post(
        "/api/channels/gmail/credentials",
        json={"client_id": "cid", "client_secret": "csecret"},  # no notify_email
    )
    assert resp.status_code == 200

    result = await check_channel_health({"container": container})
    assert result["alerted"] == []


async def test_no_alert_when_channel_is_ready(client, container):
    resp = client.post(
        "/api/channels/whatsapp/credentials",
        json={"token": "wa-token", "phone_id": "12345", "notify_email": "ops@agency.example"},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "ready"

    # whatsapp isn't even in the checked set (it doesn't do OAuth refresh),
    # and gmail/outlook are untouched here — nothing should fire.
    result = await check_channel_health({"container": container})
    assert result["alerted"] == []
