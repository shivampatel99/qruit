from __future__ import annotations

from app.models import Role, RoleStatus, OutboundMessage, utcnow
from app.services.alerts import AlertType


async def test_al1_email_mock_writes_to_outbox_not_network(container):
    role = Role(id="role-al1", client_contact="client@clientco.example")
    await container.storage.upsert_role(role)

    outcome = await container.delivery.send_alert(
        role, AlertType.STATUS_UPDATE, role.client_contact,
        {"role_title": "Test Role", "summary": "still screening"},
    )

    assert outcome.sent is True
    outbox_files = list(container.settings.outbox_dir.glob("*.txt"))
    assert len(outbox_files) >= 1


async def test_al3_no_ready_connector_fails_clearly(container):
    role = Role(id="role-al3", client_contact="client@clientco.example")
    await container.storage.upsert_role(role)

    container.settings.qruit_email_mock = False
    container.settings.google_client_id = "configured-cid"
    container.settings.google_client_secret = "configured-secret"
    container.settings.ms_client_id = "configured-cid"
    container.settings.ms_client_secret = "configured-secret"

    outcome = await container.delivery.send_alert(
        role, AlertType.STATUS_UPDATE, role.client_contact, {"role_title": "x", "summary": "y"},
    )

    assert outcome.sent is False
    assert outcome.error
    audit = await container.storage.list_audit(role.id)
    assert any(a.status == "failed" for a in audit)


async def test_wa1_whatsapp_mock_writes_to_outbox(container):
    whatsapp = container.registry.get("whatsapp")
    token = container.signer.issue("role-wa1", AlertType.INTERVIEW_INVITE.value)
    message = OutboundMessage(
        channel="whatsapp", recipient="+15550001111", body_text="hi",
        template_name="interview_invite", approval_token=token, role_id="role-wa1",
    )
    result = await whatsapp.send(message)
    assert result.success is True
    assert result.provider_message_id.startswith("mock-wamid-")


async def test_wa2_template_required_outside_window_and_rejected_if_unapproved(container):
    whatsapp = container.registry.get("whatsapp")
    token = container.signer.issue("role-wa2", "status_update")
    message = OutboundMessage(
        channel="whatsapp", recipient="+15550002222", body_text="hi",
        template_name="not_an_approved_template", approval_token=token, role_id="role-wa2",
    )
    result = await whatsapp.send(message)
    assert result.success is False
    assert "approved template" in result.error


async def test_wa3_freeform_allowed_inside_active_window(container):
    whatsapp = container.registry.get("whatsapp")
    phone = "+15550003333"
    await container.storage.record_whatsapp_inbound(phone, utcnow().isoformat())

    token = container.signer.issue("role-wa3", "status_update")
    message = OutboundMessage(
        channel="whatsapp", recipient=phone, body_text="freeform reply", template_name="",
        approval_token=token, role_id="role-wa3",
    )
    result = await whatsapp.send(message)
    assert result.success is True


async def test_wa4_send_without_approval_token_rejected(container):
    whatsapp = container.registry.get("whatsapp")
    message = OutboundMessage(channel="whatsapp", recipient="+15550004444", body_text="hi")
    result = await whatsapp.send(message)
    assert result.success is False
    assert "approval" in result.error
