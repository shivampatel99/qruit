from __future__ import annotations

from abc import ABC, abstractmethod

from app.crypto import ApprovalTokenSigner
from app.models import AuthStatus, BrowseResult, InboundItem, OutboundMessage, SendResult


def verify_approval(message: OutboundMessage, signer: ApprovalTokenSigner) -> str | None:
    """Returns an error string if `message` may not be sent, else None.

    Every connector's send() must call this before making any network call —
    this is the single choke point enforcing "no send without approval".
    """
    if not message.approval_token:
        return "no approval token present for this message"
    payload = signer.verify(message.approval_token)
    if payload is None:
        return "approval token is invalid or has been tampered with"
    if message.role_id and payload.get("role_id") != message.role_id:
        return "approval token does not match this message's role"
    return None


class Connector(ABC):
    """Shared shape every channel implements so the pipeline is source-agnostic.

    WhatsApp is the sanctioned exception: it has no browse() and its pull() is
    webhook-fed rather than request-driven (see connectors/whatsapp.py).
    """

    name: str

    @abstractmethod
    async def auth_status(self) -> AuthStatus: ...

    @abstractmethod
    async def pull(self, query: str = "", limit: int = 25, folder: str = "") -> list[InboundItem]: ...

    @abstractmethod
    async def fetch(self, item_id: str, dest_dir: str) -> str: ...

    @abstractmethod
    async def send(self, message: OutboundMessage) -> SendResult: ...


class StorageConnector(Connector):
    """Adds browse() for folder-based services (Drive, SharePoint, local folder)."""

    @abstractmethod
    async def browse(self, path: str = "") -> BrowseResult: ...
