from __future__ import annotations

import time
import uuid
from pathlib import Path

import httpx

from app.config import Settings
from app.connectors.base import Connector, verify_approval
from app.connectors.oauth_base import OAuthConnector, TokenBlob, new_pkce_pair, new_state
from app.crypto import ApprovalTokenSigner, TokenCipher
from app.models import AuthState, AuthStatus, InboundItem, OutboundMessage, SendResult, utcnow
from app.services.resilience import with_backoff
from app.storage import Storage

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

SCOPES = [
    "openid", "email", "offline_access", "User.Read", "Mail.Read", "Mail.Send",
]

# Microsoft's identity platform never echoes "openid"/"email"/"offline_access"
# back in the token response's `scope` field — they're OIDC/consent-type
# scopes, not resource scopes — so comparing against the full SCOPES list
# would report needs_auth forever even after a real, successful consent.
# Still requested (needed for the id token/email claim and refresh tokens),
# just not part of what auth_status() checks was actually granted.
REQUIRED_SCOPES = ["User.Read", "Mail.Read", "Mail.Send"]

_MOCK_INBOX = [
    {
        "item_id": "mock-outlook-1",
        "name": "product_manager_jd.docx",
        "sender": "recruiter@clientco.example",
        "subject": "JD - Product Manager",
        "body_text": "Attached is the JD for the Product Manager opening.",
        "has_attachment": True,
    },
]


class OutlookChannel(OAuthConnector, Connector):
    name = "outlook"
    channel = "outlook"
    required_scopes = REQUIRED_SCOPES

    def __init__(
        self, settings: Settings, storage: Storage, cipher: TokenCipher,
        signer: ApprovalTokenSigner,
    ):
        super().__init__(settings, storage, cipher)
        self.signer = signer

    def client_id(self) -> str:
        return self.settings.ms_client_id

    def client_secret(self) -> str:
        return self.settings.ms_client_secret

    def _authority(self) -> str:
        tenant = self.settings.ms_tenant or "common"
        return f"https://login.microsoftonline.com/{tenant}"

    def _mock_active(self) -> bool:
        return self.settings.qruit_email_mock or not self.has_credentials()

    def authorize_url(self, redirect_uri: str) -> tuple[str, str, str]:
        verifier, challenge = new_pkce_pair()
        state = new_state()
        params = httpx.QueryParams(
            {
                "client_id": self.client_id(),
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self._authority()}/oauth2/v2.0/authorize?{params}", state, verifier

    @with_backoff()
    async def _post(self, client: httpx.AsyncClient, url: str, **kwargs):
        resp = await client.post(url, **kwargs)
        resp.raise_for_status()
        return resp

    @with_backoff()
    async def _get(self, client: httpx.AsyncClient, url: str, **kwargs):
        resp = await client.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    async def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> TokenBlob:
        async with self.http_client() as client:
            resp = await self._post(
                client, f"{self._authority()}/oauth2/v2.0/token",
                data={
                    "client_id": self.client_id(),
                    "client_secret": self.client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
            )
            data = resp.json()
            who = await self._get(
                client, f"{GRAPH_BASE}/me", headers={"Authorization": f"Bearer {data['access_token']}"}
            )
            label = who.json().get("userPrincipalName", "")
        return TokenBlob(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            expires_at=time.time() + data.get("expires_in", 3600),
            scopes=data.get("scope", " ".join(SCOPES)).split(),
            account_label=label,
        )

    async def refresh(self, blob: TokenBlob) -> TokenBlob:
        async with self.http_client() as client:
            resp = await self._post(
                client, f"{self._authority()}/oauth2/v2.0/token",
                data={
                    "client_id": self.client_id(),
                    "client_secret": self.client_secret(),
                    "refresh_token": blob.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            data = resp.json()
        blob.access_token = data["access_token"]
        blob.expires_at = time.time() + data.get("expires_in", 3600)
        return blob

    async def auth_status(self) -> AuthStatus:
        if self._mock_active():
            return AuthStatus(state=AuthState.READY, account_label="mock-outlook@qruit.local")
        return await super().auth_status()

    async def pull(self, query: str = "", limit: int = 25, folder: str = "") -> list[InboundItem]:
        if self._mock_active():
            return [
                InboundItem(
                    source=self.name, item_id=m["item_id"], name=m["name"],
                    sender=m["sender"], subject=m["subject"], body_text=m["body_text"],
                    has_attachment=m["has_attachment"], size_bytes=2048,
                )
                for m in _MOCK_INBOX[:limit]
            ]
        token = await self.access_token()
        if token is None:
            return []
        async with self.http_client() as client:
            headers = {"Authorization": f"Bearer {token}"}
            if query:
                resp = await self._get(
                    client, f"{GRAPH_BASE}/me/messages", params={"$search": f'"{query}"'}, headers=headers,
                )
            else:
                # Graph rejects $filter=hasAttachments combined with $orderby
                # on a different property (400 "too complex" for this
                # non-indexed field) — sort client-side by receivedDateTime
                # instead of asking Graph to do it.
                resp = await self._get(
                    client, f"{GRAPH_BASE}/me/messages",
                    params={"$filter": "hasAttachments eq true", "$top": limit},
                    headers=headers,
                )
            messages = sorted(
                resp.json().get("value", []),
                key=lambda m: m.get("receivedDateTime", ""), reverse=True,
            )
            return [
                InboundItem(
                    source=self.name, item_id=m["id"], name=m.get("subject", m["id"]),
                    sender=m.get("from", {}).get("emailAddress", {}).get("address", ""),
                    subject=m.get("subject", ""),
                    body_text=m.get("bodyPreview", ""),
                    has_attachment=m.get("hasAttachments", False),
                    received_at=m.get("receivedDateTime") or utcnow(),
                )
                for m in messages
            ]

    async def fetch(self, item_id: str, dest_dir: str) -> str:
        dest = Path(dest_dir) / f"{item_id}.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self._mock_active():
            dest.write_bytes(b"mock outlook attachment content")
            return str(dest)
        token = await self.access_token()
        if token is None:
            raise RuntimeError("outlook: not authenticated")
        async with self.http_client() as client:
            headers = {"Authorization": f"Bearer {token}"}
            try:
                listing = await self._get(client, f"{GRAPH_BASE}/me/messages/{item_id}/attachments", headers=headers)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise RuntimeError(f"outlook: message {item_id} no longer exists") from exc
                raise
            attachments = listing.json().get("value", [])
            if not attachments:
                raise RuntimeError(f"outlook: message {item_id} has no attachment")
            import base64

            dest.write_bytes(base64.b64decode(attachments[0]["contentBytes"]))
        return str(dest)

    async def send(self, message: OutboundMessage) -> SendResult:
        error = verify_approval(message, self.signer)
        if error:
            return SendResult(success=False, error=error)
        if self._mock_active():
            return self._mock_send(message)
        token = await self.access_token()
        if token is None:
            return SendResult(success=False, error="outlook: not authenticated")
        payload = {
            "message": {
                "subject": message.subject,
                "body": {"contentType": "Text", "content": message.body_text},
                "toRecipients": [{"emailAddress": {"address": message.recipient}}],
            },
            "saveToSentItems": True,
        }
        async with self.http_client() as client:
            try:
                await self._post(
                    client, f"{GRAPH_BASE}/me/sendMail", json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPStatusError as exc:
                return SendResult(success=False, error=f"outlook send failed: {exc.response.status_code}")
        return SendResult(success=True, provider_message_id=f"outlook-{uuid.uuid4().hex[:12]}")

    def _mock_send(self, message: OutboundMessage) -> SendResult:
        outbox = self.settings.outbox_dir
        outbox.mkdir(parents=True, exist_ok=True)
        message_id = f"mock-outlook-{uuid.uuid4().hex[:12]}"
        path = outbox / f"{utcnow().strftime('%Y%m%dT%H%M%S%f')}_{message_id}.txt"
        path.write_text(
            f"To: {message.recipient}\nSubject: {message.subject}\n"
            f"Template: {message.template_name}\n\n{message.body_text}\n"
            f"Attachments: {message.attachments}\n",
            encoding="utf-8",
        )
        return SendResult(success=True, provider_message_id=message_id)
