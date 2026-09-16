from __future__ import annotations

from app.connectors.local_folder import LocalFolderChannel


class ManualUploadChannel(LocalFolderChannel):
    """Drag-and-drop uploads (POST /api/uploads) land here and flow through
    the exact same pipeline as anything pulled from a connected channel."""

    name = "manual_upload"

    def save_upload(self, filename: str, content: bytes) -> str:
        dest = self.root / filename
        dest.write_bytes(content)
        return str(dest.relative_to(self.root))
