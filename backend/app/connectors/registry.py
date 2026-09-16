from __future__ import annotations

from app.config import Settings
from app.connectors.base import Connector
from app.connectors.gdrive import GoogleDriveChannel
from app.connectors.gmail import GmailChannel
from app.connectors.local_folder import LocalFolderChannel
from app.connectors.manual_upload import ManualUploadChannel
from app.connectors.onedrive import OneDriveChannel
from app.connectors.outlook import OutlookChannel
from app.connectors.sharepoint import SharePointChannel
from app.connectors.whatsapp import WhatsAppChannel
from app.crypto import ApprovalTokenSigner, TokenCipher
from app.storage import Storage


class ConnectorRegistry:
    """Looks up a connector instance by name; the pipeline never constructs
    a connector directly, so adding a channel means registering it here once."""

    def __init__(self, settings: Settings, storage: Storage):
        cipher = TokenCipher(settings)
        signer = ApprovalTokenSigner(settings)
        self.signer = signer
        self._connectors: dict[str, Connector] = {
            "gmail": GmailChannel(settings, storage, cipher, signer),
            "outlook": OutlookChannel(settings, storage, cipher, signer),
            "gdrive": GoogleDriveChannel(settings, storage, cipher),
            "sharepoint": SharePointChannel(settings, storage, cipher),
            "onedrive": OneDriveChannel(settings, storage, cipher),
            "local_folder": LocalFolderChannel(
                settings, settings.qruit_data_dir / "local_sources", signer
            ),
            "manual_upload": ManualUploadChannel(
                settings, settings.qruit_data_dir / "uploads", signer
            ),
            "whatsapp": WhatsAppChannel(settings, storage, signer),
        }

    def get(self, name: str) -> Connector:
        try:
            return self._connectors[name]
        except KeyError as exc:
            raise ValueError(f"unknown connector: {name}") from exc

    def all(self) -> dict[str, Connector]:
        return dict(self._connectors)

    def pull_capable(self) -> dict[str, Connector]:
        return {k: v for k, v in self._connectors.items() if k != "whatsapp"}
