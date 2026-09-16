from __future__ import annotations

import hashlib
import hmac
import json

from app.models import Candidate, Role


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _text_payload(phone: str, text: str, wamid: str = "wamid.123") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": phone, "id": wamid, "timestamp": "1700000000", "text": {"body": text}}
                            ]
                        }
                    }
                ]
            }
        ]
    }


def _media_payload(phone: str, media_id: str, wamid: str = "wamid.456") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": phone, "id": wamid, "timestamp": "1700000001",
                                    "document": {"id": media_id, "filename": "cv.pdf"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }


async def test_wa5_valid_signed_message_is_enqueued(client, container):
    container.settings.whatsapp_app_secret = "test-app-secret"
    payload = _text_payload("+15551234567", "PROCEED")
    body = json.dumps(payload).encode()
    signature = _sign("test-app-secret", body)

    response = client.post(
        "/webhooks/whatsapp", content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    items = await container.registry.get("whatsapp").pull()
    assert len(items) == 1
    assert items[0].body_text == "PROCEED"
    assert items[0].sender == "+15551234567"


async def test_wa6_invalid_signature_rejected_and_not_enqueued(client, container):
    container.settings.whatsapp_app_secret = "test-app-secret"
    payload = _text_payload("+15559999999", "PROCEED")
    body = json.dumps(payload).encode()

    response = client.post(
        "/webhooks/whatsapp", content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"},
    )

    assert response.status_code == 403
    items = await container.registry.get("whatsapp").pull()
    assert items == []


async def test_wa7_media_attachment_extracted_and_fetchable(client, container, tmp_path):
    container.settings.whatsapp_app_secret = "test-app-secret"
    payload = _media_payload("+15557654321", "media-abc-123")
    body = json.dumps(payload).encode()
    signature = _sign("test-app-secret", body)

    response = client.post(
        "/webhooks/whatsapp", content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 200

    items = await container.registry.get("whatsapp").pull()
    assert len(items) == 1
    assert items[0].has_attachment is True
    assert items[0].item_id == "media-abc-123"

    dest_dir = tmp_path / "fetched"
    path = await container.registry.get("whatsapp").fetch(items[0].item_id, str(dest_dir))
    assert path


async def test_wa8_known_candidate_phone_matched_to_role(container):
    role = Role(id="role-wa8", client_contact="")
    await container.storage.upsert_role(role)
    candidate = Candidate(id="cand-wa8", role_id=role.id, phone="+15550001234")
    await container.storage.upsert_candidate(candidate)

    matched = await container.approval._find_role_by_candidate_phone("+15550001234")
    assert matched is not None
    assert matched.id == role.id


async def test_wa9_unregistered_phone_not_matched(client, container):
    container.settings.whatsapp_app_secret = "test-app-secret"
    payload = _text_payload("+19998887777", "PROCEED")
    body = json.dumps(payload).encode()
    signature = _sign("test-app-secret", body)
    client.post(
        "/webhooks/whatsapp", content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )

    items = await container.registry.get("whatsapp").pull()
    outcome = await container.approval.process_inbound_item(items[0])
    assert outcome.applied is False
    assert "not registered" in outcome.reason


def test_ex4_webhook_handler_contains_only_verify_extract_enqueue():
    import inspect

    from app.api import webhooks

    source = inspect.getsource(webhooks)
    forbidden = ["classify(", "screening.run", "ScreeningPipeline", "ApprovalPipeline", "reqruit"]
    for term in forbidden:
        assert term not in source, f"webhook handler references '{term}' — it must stay minimal"
