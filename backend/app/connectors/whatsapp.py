from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import Settings
from app.connectors.base import Connector, verify_approval
from app.crypto import ApprovalTokenSigner
from app.models import (
    AuthState, AuthStatus, InboundItem, OutboundMessage, SendResult, utcnow,
)
from app.services.resilience import with_backoff
from app.storage import Storage

GRAPH_BASE = "https://graph.facebook.com/v20.0"
CONVERSATION_WINDOW = dt.timedelta(hours=24)

APPROVED_TEMPLATES = {"interview_invite", "interview_reminder", "status_update", "report_digest"}


@dataclass
class WhatsAppInboundMessage:
    """What the webhook handler extracts before enqueueing — nothing more."""

    wamid: str
    from_phone: str
    text: str = ""
    media_id: str = ""
    media_type: str = ""
    timestamp: dt.datetime = field(default_factory=utcnow)


def verify_signature(app_secret: str, raw_body: bytes, signature_header: str) -> bool:
    """Meta signs the payload as `sha256=<hex hmac>` in X-Hub-Signature-256."""
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[len("sha256=") :], expected)


def parse_webhook_payload(payload: dict) -> list[WhatsAppInboundMessage]:
    """Extracts messages from a Meta Cloud API webhook payload. Pure parsing —
    no classification, matching, or side effects (that happens after enqueue)."""
    messages: list[WhatsAppInboundMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                from_phone = msg.get("from", "")
                wamid = msg.get("id", "")
                ts = msg.get("timestamp")
                timestamp = (
                    dt.datetime.fromtimestamp(int(ts), tz=dt.timezone.utc) if ts else utcnow()
                )
                text = msg.get("text", {}).get("body", "")
                media_id, media_type = "", ""
                for kind in ("document", "image", "audio", "video"):
                    if kind in msg:
                        media_id = msg[kind].get("id", "")
                        media_type = kind
                        break
                messages.append(
                    WhatsAppInboundMessage(
                        wamid=wamid, from_phone=from_phone, text=text,
                        media_id=media_id, media_type=media_type, timestamp=timestamp,
                    )
                )
    return messages


class WhatsAppChannel(Connector):
    """Send-and-webhook-receive connector for Meta's Cloud API.

    pull() intentionally always returns [] — the Cloud API has no "list
    messages" endpoint; inbound only ever arrives via the webhook, which
    enqueues directly into the same inbound queue pull() feeds elsewhere
    (see api/webhooks.py). This is the one sanctioned deviation from the
    request-driven connector shape (FRD §5.1, §5.1a).
    """

    name = "whatsapp"

    def __init__(self, settings: Settings, storage: Storage, signer: ApprovalTokenSigner):
        self.settings = settings
        self.storage = storage
        self.signer = signer

    def _mock_active(self) -> bool:
        return self.settings.whatsapp_mock or not (
            self.settings.whatsapp_token and self.settings.whatsapp_phone_id
        )

    async def auth_status(self) -> AuthStatus:
        if self._mock_active():
            return AuthStatus(state=AuthState.READY, account_label="mock-whatsapp-business-number")
        if not self.settings.whatsapp_token or not self.settings.whatsapp_phone_id:
            return AuthStatus(
                state=AuthState.ERROR,
                reason="whatsapp: WHATSAPP_TOKEN/WHATSAPP_PHONE_ID not configured",
            )
        return AuthStatus(state=AuthState.READY, account_label=self.settings.whatsapp_phone_id)

    async def pull(self, query: str = "", limit: int = 25, folder: str = "") -> list[InboundItem]:
        """Not request-driven against Meta (no list-messages endpoint exists);
        this drains the local queue the webhook enqueued into (FRD §5.1a)."""
        rows = await self.storage.drain_inbound(self.name, limit=limit)
        return [
            InboundItem(
                source=self.name, item_id=row["item_id"], name=row["item_id"],
                sender=row["sender"], subject=row["subject"], body_text=row["body_text"],
                has_attachment=bool(row["has_attachment"]), received_at=row["received_at"],
            )
            for row in rows
        ]

    @with_backoff()
    async def _get(self, client: httpx.AsyncClient, url: str, **kwargs):
        resp = await client.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    async def fetch(self, item_id: str, dest_dir: str) -> str:
        dest = Path(dest_dir) / f"{item_id}.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self._mock_active():
            dest.write_bytes(b"mock whatsapp media content")
            return str(dest)

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"Authorization": f"Bearer {self.settings.whatsapp_token}"}
            meta = await self._get(client, f"{GRAPH_BASE}/{item_id}", headers=headers)
            media_url = meta.json()["url"]
            media = await self._get(client, media_url, headers=headers)
            dest.write_bytes(media.content)
        return str(dest)

    async def enqueue(self, messages: list[WhatsAppInboundMessage]) -> None:
        """Called only by the webhook route — pushes each parsed message into
        the shared inbound queue and records the 24h conversation window."""
        for msg in messages:
            item_id = msg.media_id or msg.wamid
            await self.storage.enqueue_inbound(
                source=self.name, item_id=item_id, sender=msg.from_phone, subject="",
                body_text=msg.text, has_attachment=bool(msg.media_id),
                received_at_iso=msg.timestamp.isoformat(),
            )
            await self.storage.record_whatsapp_inbound(msg.from_phone, msg.timestamp.isoformat())

    async def window_open(self, phone: str) -> bool:
        last = await self.storage.whatsapp_last_inbound_at(phone)
        if not last:
            return False
        last_at = dt.datetime.fromisoformat(last)
        return utcnow() - last_at < CONVERSATION_WINDOW

    async def send(self, message: OutboundMessage) -> SendResult:
        error = verify_approval(message, self.signer)
        if error:
            return SendResult(success=False, error=error)

        inside_window = await self.window_open(message.recipient)
        if not inside_window:
            if message.template_name not in APPROVED_TEMPLATES:
                return SendResult(
                    success=False,
                    error=(
                        f"'{message.template_name}' is not an approved template; outside the "
                        "24h window a business-initiated message must use one (WA-2)"
                    ),
                )

        if self._mock_active():
            return self._mock_send(message, inside_window)

        if inside_window:
            payload = {
                "messaging_product": "whatsapp", "to": message.recipient,
                "type": "text", "text": {"body": message.body_text},
            }
        else:
            payload = {
                "messaging_product": "whatsapp", "to": message.recipient,
                "type": "template",
                "template": {
                    "name": message.template_name,
                    "language": {"code": "en"},
                },
            }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{self.settings.whatsapp_phone_id}/messages",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.whatsapp_token}"},
            )
        if resp.status_code >= 400:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            error_msg = body.get("error", {}).get("message", "")
            if "template" in error_msg.lower():
                return SendResult(success=False, error=f"whatsapp: template not approved — {error_msg}")
            return SendResult(success=False, error=f"whatsapp send failed: {resp.status_code} {error_msg}")
        wamid = resp.json().get("messages", [{}])[0].get("id", "")
        return SendResult(success=True, provider_message_id=wamid)

    def _mock_send(self, message: OutboundMessage, inside_window: bool) -> SendResult:
        outbox = self.settings.outbox_dir
        outbox.mkdir(parents=True, exist_ok=True)
        wamid = f"mock-wamid-{uuid.uuid4().hex[:16]}"
        kind = "freeform" if inside_window else f"template:{message.template_name}"
        path = outbox / f"{utcnow().strftime('%Y%m%dT%H%M%S%f')}_{wamid}.txt"
        path.write_text(
            f"To: {message.recipient}\nKind: {kind}\n\n{message.body_text}\n", encoding="utf-8",
        )
        return SendResult(success=True, provider_message_id=wamid)
