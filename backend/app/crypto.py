from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


def _fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class TokenCipher:
    """Encrypts OAuth token blobs at rest using QRUIT_APPROVAL_SECRET."""

    def __init__(self, settings: Settings):
        self._fernet = Fernet(_fernet_key(settings.qruit_approval_secret))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("token could not be decrypted with the current secret") from exc


class ApprovalTokenSigner:
    """Signs and verifies opaque approval tokens gating every send()."""

    def __init__(self, settings: Settings):
        self._secret = settings.qruit_approval_secret.encode("utf-8")

    def issue(self, role_id: str, checkpoint_type: str) -> str:
        payload = {
            "role_id": role_id,
            "checkpoint_type": checkpoint_type,
            "nonce": secrets.token_urlsafe(12),
            "issued_at": time.time(),
        }
        body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
        signature = self._sign(body)
        return f"{body}.{signature}"

    def verify(self, token: str) -> dict | None:
        try:
            body, signature = token.split(".", 1)
        except ValueError:
            return None
        if not hmac.compare_digest(signature, self._sign(body)):
            return None
        try:
            return json.loads(base64.urlsafe_b64decode(body.encode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            return None

    def _sign(self, body: str) -> str:
        mac = hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode("utf-8")
