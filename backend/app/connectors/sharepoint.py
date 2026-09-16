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

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

SCOPES = ["Files.Read.All", "Sites.Read.All"]

_MOCK_TREE: dict[str, list[dict]] = {
    "": [
        {"name": "Active Roles", "is_folder": True},
    ],
    "Active Roles": [
        {"name": "product_manager_jd.docx", "is_folder": False},
        {"name": "alice_wong_cv.pdf", "is_folder": False},
    ],
}


def _looks_like(name: str, kind: str) -> bool:
    lowered = name.lower()
    if kind == "jd":
        return "jd" in lowered or "job_desc" in lowered
    return "cv" in lowered or "resume" in lowered


class SharePointChannel(OAuthConnector, StorageConnector):
    """Reads the site's default document library (or OneDrive if no site ID set)."""

    name = "sharepoint"
    channel = "outlook"
    display_name = "sharepoint"
    required_scopes = SCOPES

    def client_id(self) -> str:
        return self.settings.ms_client_id

    def client_secret(self) -> str:
        return self.settings.ms_client_secret

    def _mock_active(self) -> bool:
        return not self.has_credentials()

    async def auth_status(self) -> AuthStatus:
        if self._mock_active():
            return AuthStatus(state=AuthState.ERROR, reason="sharepoint: Microsoft app not configured")
        return await super().auth_status()

    def _authority(self) -> str:
        tenant = self.settings.ms_tenant or "common"
        return f"https://login.microsoftonline.com/{tenant}"

    @with_backoff()
    async def _post(self, client: httpx.AsyncClient, url: str, **kwargs):
        resp = await client.post(url, **kwargs)
        resp.raise_for_status()
        return resp

    async def refresh(self, blob: TokenBlob) -> TokenBlob:
        """Shares Outlook's OAuth client/token (channel="outlook"), so this
        is the same refresh call OutlookChannel makes — without it,
        access_token() hits the base class's NotImplementedError the
        moment the token nears expiry."""
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

    def _drive_root(self) -> str:
        site_id = self.settings.sharepoint_site_id
        if site_id:
            return f"{GRAPH_BASE}/sites/{site_id}/drive"
        return f"{GRAPH_BASE}/me/drive"

    @with_backoff()
    async def _get(self, client: httpx.AsyncClient, url: str, **kwargs):
        resp = await client.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    async def browse(self, path: str = "") -> BrowseResult:
        path = path or self.settings.sharepoint_folder_path
        if self._mock_active():
            children = _MOCK_TREE.get(path, [])
            entries = [
                BrowseEntry(
                    path=f"{path}/{c['name']}".strip("/"), name=c["name"], is_folder=c["is_folder"],
                    jd_count=1 if _looks_like(c["name"], "jd") else 0,
                    cv_count=1 if _looks_like(c["name"], "cv") else 0,
                    file_count=0 if c["is_folder"] else 1,
                )
                for c in children
            ]
            return BrowseResult(path=path, entries=entries)
        token = await self.access_token()
        if token is None:
            return BrowseResult(path=path, entries=[])
        async with self.http_client() as client:
            suffix = f"root:/{path}:/children" if path else "root/children"
            resp = await self._get(
                client, f"{self._drive_root()}/{suffix}",
                headers={"Authorization": f"Bearer {token}"},
            )
        entries = []
        for item in resp.json().get("value", []):
            is_folder = "folder" in item
            entries.append(
                BrowseEntry(
                    path=f"{path}/{item['name']}".strip("/"), name=item["name"], is_folder=is_folder,
                    jd_count=1 if not is_folder and _looks_like(item["name"], "jd") else 0,
                    cv_count=1 if not is_folder and _looks_like(item["name"], "cv") else 0,
                    file_count=0 if is_folder else 1,
                )
            )
        return BrowseResult(path=path, entries=entries)

    async def pull(self, query: str = "", limit: int = 25, folder: str = "") -> list[InboundItem]:
        folder = folder or self.settings.sharepoint_folder_path
        if self._mock_active():
            children = [c for c in _MOCK_TREE.get(folder, []) if not c["is_folder"]]
            return [
                InboundItem(
                    source=self.name, item_id=f"{folder}/{c['name']}".strip("/"), name=c["name"],
                    has_attachment=True, size_bytes=4096,
                )
                for c in children[:limit]
            ]
        token = await self.access_token()
        if token is None:
            return []
        async with self.http_client() as client:
            suffix = f"root:/{folder}:/children" if folder else "root/children"
            resp = await self._get(
                client, f"{self._drive_root()}/{suffix}",
                headers={"Authorization": f"Bearer {token}"},
            )
        return [
            InboundItem(
                source=self.name, item_id=item["id"], name=item["name"],
                has_attachment=True, size_bytes=item.get("size", 0),
            )
            for item in resp.json().get("value", [])
            if "folder" not in item
        ][:limit]

    async def fetch(self, item_id: str, dest_dir: str) -> str:
        dest = Path(dest_dir) / f"{Path(item_id).name}.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self._mock_active():
            dest.write_bytes(b"mock sharepoint file content")
            return str(dest)
        token = await self.access_token()
        if token is None:
            raise RuntimeError("sharepoint: not authenticated")
        async with self.http_client() as client:
            try:
                resp = await self._get(
                    client, f"{self._drive_root()}/items/{item_id}/content",
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise RuntimeError(f"sharepoint: item {item_id} no longer exists") from exc
                raise
            dest.write_bytes(resp.content)
        return str(dest)

    async def send(self, message: OutboundMessage) -> SendResult:
        raise NotImplementedError("sharepoint is a read-only storage connector; it has no send()")
