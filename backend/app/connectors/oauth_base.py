from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field

import httpx

from app.config import Settings
from app.crypto import TokenCipher
from app.models import AuthState, AuthStatus
from app.storage import Storage


def new_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(24)


@dataclass
class TokenBlob:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    scopes: list[str] = field(default_factory=list)
    account_label: str = ""

    def is_near_expiry(self, skew_seconds: int = 120) -> bool:
        return time.time() > (self.expires_at - skew_seconds)


class OAuthConnector:
    """Shared PKCE flow for Gmail, Drive, Outlook, SharePoint, OneDrive.

    A person completes the consent flow once; after that access_token()
    refreshes silently ahead of expiry so pull()/fetch()/send() never block
    on a human. No connector built on this ever requests a write/delete scope.
    """

    channel: str = "oauth"
    required_scopes: list[str] = []
    account_key: str = "default"
    display_name: str = ""  # falls back to `name`/`channel` in status messages

    def __init__(self, settings: Settings, storage: Storage, cipher: TokenCipher):
        self.settings = settings
        self.storage = storage
        self.cipher = cipher

    def client_id(self) -> str:
        raise NotImplementedError

    def client_secret(self) -> str:
        raise NotImplementedError

    def has_credentials(self) -> bool:
        return bool(self.client_id() and self.client_secret())

    def authorize_url(self, redirect_uri: str) -> tuple[str, str, str]:
        raise NotImplementedError

    async def exchange_code(
        self, code: str, redirect_uri: str, code_verifier: str
    ) -> TokenBlob:
        raise NotImplementedError

    async def refresh(self, blob: TokenBlob) -> TokenBlob:
        raise NotImplementedError

    async def store_token(self, blob: TokenBlob) -> None:
        payload = json.dumps(
            {
                "access_token": blob.access_token,
                "refresh_token": blob.refresh_token,
                "expires_at": blob.expires_at,
                "scopes": blob.scopes,
                "account_label": blob.account_label,
            }
        )
        await self.storage.save_channel_account(
            self.channel, self.account_key, self.cipher.encrypt(payload),
            blob.scopes, blob.account_label,
        )

    async def load_token(self) -> TokenBlob | None:
        row = await self.storage.get_channel_account(self.channel, self.account_key)
        if not row or not row["encrypted_token"]:
            return None
        try:
            data = json.loads(self.cipher.decrypt(row["encrypted_token"]))
        except ValueError:
            return None
        return TokenBlob(**data)

    async def access_token(self) -> str | None:
        blob = await self.load_token()
        if blob is None:
            return None
        if blob.is_near_expiry():
            blob = await self.refresh(blob)
            await self.store_token(blob)
        return blob.access_token

    async def auth_status(self) -> AuthStatus:
        label = self.display_name or self.channel
        if not self.has_credentials():
            return AuthStatus(
                state=AuthState.ERROR,
                reason=f"{label}: client ID/secret not configured",
            )
        blob = await self.load_token()
        if blob is None:
            return AuthStatus(
                state=AuthState.NEEDS_AUTH,
                reason=f"{label}: not yet connected — click Connect",
            )
        missing = sorted(set(self.required_scopes) - set(blob.scopes))
        if missing:
            return AuthStatus(
                state=AuthState.NEEDS_AUTH,
                reason=f"reconnect to grant permission: {', '.join(missing)}",
                account_label=blob.account_label,
            )
        return AuthStatus(state=AuthState.READY, account_label=blob.account_label)

    @staticmethod
    def http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=30.0)
