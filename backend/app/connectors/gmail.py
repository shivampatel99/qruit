from __future__ import annotations

import base64
import time
import uuid
from email.message import EmailMessage
from pathlib import Path

import httpx

from app.config import Settings
from app.connectors.base import Connector, verify_approval
from app.connectors.oauth_base import OAuthConnector, TokenBlob, new_pkce_pair, new_state
from app.crypto import ApprovalTokenSigner, TokenCipher
from app.models import AuthState, AuthStatus, InboundItem, OutboundMessage, SendResult, utcnow
from app.services.resilience import with_backoff
from app.storage import Storage

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

SCOPES = [
    "openid",
    # Google's token response always reports this back as the canonical URL
    # below, never the shorthand "email" — requesting the canonical form
    # directly keeps required_scopes comparable to what auth_status() reads
    # back from storage (see GmailChannel.required_scopes).
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# GoogleDriveChannel shares this connector's token (channel="gmail"), so the
# consent screen must request Drive's scope too — otherwise Drive would show
# needs_auth after a Gmail-only connect, contradicting GD-1 ("one Connect
# covers Gmail and Drive"). GmailChannel.required_scopes stays SCOPES-only;
# this wider list is only ever used to build the authorize URL.
CONSENT_SCOPES = SCOPES + ["https://www.googleapis.com/auth/drive.readonly"]

_MOCK_INBOX = [
    {
        "item_id": "mock-gmail-1",
        "name": "senior_backend_engineer_jd.docx",
        "sender": "hiring.manager@clientco.example",
        "subject": "JD - Senior Backend Engineer",
        "body_text": "Please see attached JD for the Senior Backend Engineer role.",
        "has_attachment": True,
    },
    {
        "item_id": "mock-gmail-2",
        "name": "jane_doe_cv.pdf",
        "sender": "jane.doe@candidate.example",
        "subject": "Application - Senior Backend Engineer",
        "body_text": "Please find my CV attached.",
        "has_attachment": True,
    },
]


class GmailChannel(OAuthConnector, Connector):
    name = "gmail"
    channel = "gmail"
    required_scopes = SCOPES

    def __init__(
        self, settings: Settings, storage: Storage, cipher: TokenCipher,
        signer: ApprovalTokenSigner,
    ):
        super().__init__(settings, storage, cipher)
        self.signer = signer

    def client_id(self) -> str:
        return self.settings.google_client_id

    def client_secret(self) -> str:
        return self.settings.google_client_secret

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
                "scope": " ".join(CONSENT_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTH_URL}?{params}", state, verifier

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
                client, TOKEN_URL,
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
                client, USERINFO_URL, headers={"Authorization": f"Bearer {data['access_token']}"}
            )
            email = who.json().get("email", "")
        return TokenBlob(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            expires_at=time.time() + data.get("expires_in", 3600),
            scopes=data.get("scope", " ".join(SCOPES)).split(),
            account_label=email,
        )

    async def refresh(self, blob: TokenBlob) -> TokenBlob:
        async with self.http_client() as client:
            resp = await self._post(
                client, TOKEN_URL,
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
            return AuthStatus(state=AuthState.READY, account_label="mock-gmail@qruit.local")
        return await super().auth_status()

    async def pull(self, query: str = "", limit: int = 25, folder: str = "") -> list[InboundItem]:
        query = query or "newer_than:90d has:attachment"
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
        items: list[InboundItem] = []
        async with self.http_client() as client:
            headers = {"Authorization": f"Bearer {token}"}
            search = await self._get(
                client, f"{API_BASE}/messages", params={"q": query, "maxResults": limit}, headers=headers,
            )
            for ref in search.json().get("messages", []):
                detail = await self._get(
                    client, f"{API_BASE}/messages/{ref['id']}", params={"format": "full"}, headers=headers,
                )
                items.append(self._to_inbound_item(detail.json()))
        return items

    def _to_inbound_item(self, payload: dict) -> InboundItem:
        headers = {h["name"]: h["value"] for h in payload.get("payload", {}).get("headers", [])}
        parts = payload.get("payload", {}).get("parts", [])
        has_attachment = any(p.get("filename") for p in parts)
        name = next((p["filename"] for p in parts if p.get("filename")), payload["id"])
        return InboundItem(
            source=self.name, item_id=payload["id"], name=name,
            sender=headers.get("From", ""), subject=headers.get("Subject", ""),
            has_attachment=has_attachment, size_bytes=payload.get("sizeEstimate", 0),
        )

    async def fetch(self, item_id: str, dest_dir: str) -> str:
        dest = Path(dest_dir) / f"{item_id}.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self._mock_active():
            dest.write_bytes(b"mock gmail attachment content")
            return str(dest)
        token = await self.access_token()
        if token is None:
            raise RuntimeError("gmail: not authenticated")
        async with self.http_client() as client:
            headers = {"Authorization": f"Bearer {token}"}
            try:
                detail = await self._get(
                    client, f"{API_BASE}/messages/{item_id}", params={"format": "full"}, headers=headers,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise RuntimeError(f"gmail: message {item_id} no longer exists") from exc
                raise
            parts = detail.json().get("payload", {}).get("parts", [])
            attachment_part = next((p for p in parts if p.get("filename")), None)
            if attachment_part is None:
                raise RuntimeError(f"gmail: message {item_id} has no attachment")
            attachment_id = attachment_part["body"]["attachmentId"]
            att = await self._get(
                client, f"{API_BASE}/messages/{item_id}/attachments/{attachment_id}", headers=headers,
            )
            dest.write_bytes(base64.urlsafe_b64decode(att.json()["data"] + "=="))
        return str(dest)

    async def send(self, message: OutboundMessage) -> SendResult:
        error = verify_approval(message, self.signer)
        if error:
            return SendResult(success=False, error=error)
        if self._mock_active():
            return self._mock_send(message)
        token = await self.access_token()
        if token is None:
            return SendResult(success=False, error="gmail: not authenticated")
        mime = EmailMessage()
        mime["To"] = message.recipient
        mime["Subject"] = message.subject
        mime.set_content(message.body_text)
        for attachment in message.attachments:
            path = Path(attachment)
            mime.add_attachment(
                path.read_bytes(), maintype="application", subtype="pdf", filename=path.name,
            )
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")
        async with self.http_client() as client:
            try:
                resp = await self._post(
                    client, f"{API_BASE}/messages/send",
                    json={"raw": raw},
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPStatusError as exc:
                return SendResult(success=False, error=f"gmail send failed: {exc.response.status_code}")
        return SendResult(success=True, provider_message_id=resp.json().get("id", ""))

    def _mock_send(self, message: OutboundMessage) -> SendResult:
        outbox = self.settings.outbox_dir
        outbox.mkdir(parents=True, exist_ok=True)
        message_id = f"mock-gmail-{uuid.uuid4().hex[:12]}"
        path = outbox / f"{utcnow().strftime('%Y%m%dT%H%M%S%f')}_{message_id}.txt"
        path.write_text(
            f"To: {message.recipient}\nSubject: {message.subject}\n"
            f"Template: {message.template_name}\n\n{message.body_text}\n"
            f"Attachments: {message.attachments}\n",
            encoding="utf-8",
        )
        return SendResult(success=True, provider_message_id=message_id)
