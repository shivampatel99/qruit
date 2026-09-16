from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.bootstrap import Container, get_container
from app.connectors.whatsapp import parse_webhook_payload, verify_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# This is the ONE webhook in the entire system (FRD §3 rule 1, TEST_CASES EX-1).
# The handler below does exactly three things: verify the request is genuinely
# from Meta, extract the message(s), and enqueue them into the same inbound
# pipeline every other connector's pull() feeds. No classification, screening,
# or reply logic is allowed in this file — that all happens later, once the
# item is indistinguishable from anything pulled from Gmail or a folder.


@router.get(
    "/whatsapp",
    summary="Meta webhook verification handshake",
    description="Meta calls this once when the webhook URL is registered, to confirm ownership.",
)
async def verify_whatsapp_webhook(request: Request, container: Container = Depends(get_container)):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")
    if mode == "subscribe" and token == container.settings.whatsapp_verify_token:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


@router.post(
    "/whatsapp",
    summary="Receive an inbound WhatsApp message",
    description="Verifies the request is genuinely from Meta, extracts the message(s), "
    "and enqueues them into the same inbound pipeline every connector's pull() feeds. "
    "No classification, screening, or reply logic runs here.",
)
async def receive_whatsapp_webhook(request: Request, container: Container = Depends(get_container)):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if container.settings.whatsapp_app_secret:
        if not verify_signature(container.settings.whatsapp_app_secret, raw_body, signature):
            return Response(status_code=403)
    elif not container.settings.whatsapp_mock:
        return Response(status_code=403)

    payload = await request.json()
    messages = parse_webhook_payload(payload)
    await container.registry.get("whatsapp").enqueue(messages)
    return {"status": "enqueued", "count": len(messages)}
