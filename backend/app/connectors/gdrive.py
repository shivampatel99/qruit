from __future__ import annotations

import time
from pathlib import Path

import httpx

from app.connectors.base import StorageConnector
from app.connectors.oauth_base import OAuthConnector, TokenBlob
from app.models import (
    AuthState, AuthStatus, BrowseEntry, BrowseResult, InboundItem, OutboundMessage,
    SendResult,
)
from app.services.resilience import with_backoff

API_BASE = "https://www.googleapis.com/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

_MOCK_TREE: dict[str, list[dict]] = {
    "": [
        {"id": "mock-folder-active-roles", "name": "Active Roles", "is_folder": True},
    ],
    "mock-folder-active-roles": [
        {"id": "mock-drive-jd-1", "name": "senior_backend_engineer_jd.docx", "is_folder": False},
        {"id": "mock-drive-cv-1", "name": "john_smith_cv.pdf", "is_folder": False},
    ],
}


def _looks_like(name: str, kind: str) -> bool:
    lowered = name.lower()
    if kind == "jd":
        return "jd" in lowered or "job_desc" in lowered or "jobdescription" in lowered
    return "cv" in lowered or "resume" in lowered


class GoogleDriveChannel(OAuthConnector, StorageConnector):
    """Read-only browse/pull; shares the Gmail OAuth client and token (GD-1)."""

    name = "gdrive"
    channel = "gmail"
    display_name = "gdrive"
    required_scopes = SCOPES

    def client_id(self) -> str:
        return self.settings.google_client_id

    def client_secret(self) -> str:
        return self.settings.google_client_secret

    def _mock_active(self) -> bool:
        return not self.has_credentials()

    async def auth_status(self) -> AuthStatus:
        if self._mock_active():
            return AuthStatus(state=AuthState.ERROR, reason="gdrive: Google client ID/secret not configured")
        return await super().auth_status()

    @with_backoff()
    async def _get(self, client: httpx.AsyncClient, url: str, **kwargs):
        resp = await client.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    @with_backoff()
    async def _post(self, client: httpx.AsyncClient, url: str, **kwargs):
        resp = await client.post(url, **kwargs)
        resp.raise_for_status()
        return resp

    async def refresh(self, blob: TokenBlob) -> TokenBlob:
        """Shares Gmail's OAuth client/token (GD-1), so this is the same
        refresh call GmailChannel makes — without it, access_token() hits
        the base class's NotImplementedError the moment the token nears
        expiry, since the base OAuthConnector has no default refresh."""
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

    async def browse(self, path: str = "") -> BrowseResult:
        folder_id = path or self.settings.google_drive_folder_id
        if self._mock_active() or not folder_id:
            children = _MOCK_TREE.get(folder_id, [])
            entries = [
                BrowseEntry(
                    path=c["id"], name=c["name"], is_folder=c["is_folder"],
                    jd_count=1 if _looks_like(c["name"], "jd") else 0,
                    cv_count=1 if _looks_like(c["name"], "cv") else 0,
                    file_count=0 if c["is_folder"] else 1,
                )
                for c in children
            ]
            return BrowseResult(path=folder_id, entries=entries)
        token = await self.access_token()
        if token is None:
            return BrowseResult(path=folder_id, entries=[])
        async with self.http_client() as client:
            resp = await self._get(
                client, f"{API_BASE}/files",
                params={
                    "q": f"'{folder_id}' in parents and trashed = false",
                    "fields": "files(id,name,size,modifiedTime,mimeType)",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        entries = []
        for f in resp.json().get("files", []):
            is_folder = f["mimeType"] == "application/vnd.google-apps.folder"
            entries.append(
                BrowseEntry(
                    path=f["id"], name=f["name"], is_folder=is_folder,
                    jd_count=1 if not is_folder and _looks_like(f["name"], "jd") else 0,
                    cv_count=1 if not is_folder and _looks_like(f["name"], "cv") else 0,
                    file_count=0 if is_folder else 1,
                )
            )
        return BrowseResult(path=folder_id, entries=entries)

    async def pull(self, query: str = "", limit: int = 25, folder: str = "") -> list[InboundItem]:
        folder_id = folder or self.settings.google_drive_folder_id
        if self._mock_active() or not folder_id:
            children = [c for c in _MOCK_TREE.get(folder_id, []) if not c["is_folder"]]
            return [
                InboundItem(
                    source=self.name, item_id=c["id"], name=c["name"],
                    has_attachment=True, size_bytes=4096,
                )
                for c in children[:limit]
            ]
        token = await self.access_token()
        if token is None:
            return []
        async with self.http_client() as client:
            resp = await self._get(
                client, f"{API_BASE}/files",
                params={
                    "q": f"'{folder_id}' in parents and trashed = false",
                    "fields": "files(id,name,size,mimeType)",
                    "pageSize": limit,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        return [
            InboundItem(
                source=self.name, item_id=f["id"], name=f["name"],
                has_attachment=True, size_bytes=int(f.get("size", 0) or 0),
            )
            for f in resp.json().get("files", [])
            if f["mimeType"] != "application/vnd.google-apps.folder"
        ]

    async def fetch(self, item_id: str, dest_dir: str) -> str:
        dest = Path(dest_dir) / f"{item_id}.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self._mock_active():
            dest.write_bytes(b"mock drive file content")
            return str(dest)
        token = await self.access_token()
        if token is None:
            raise RuntimeError("gdrive: not authenticated")
        async with self.http_client() as client:
            try:
                resp = await self._get(
                    client, f"{API_BASE}/files/{item_id}", params={"alt": "media"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise RuntimeError(f"gdrive: file {item_id} no longer exists") from exc
                raise
            dest.write_bytes(resp.content)
        return str(dest)

    async def send(self, message: OutboundMessage) -> SendResult:
        raise NotImplementedError("gdrive is a read-only storage connector; it has no send()")
