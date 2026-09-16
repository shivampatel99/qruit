from __future__ import annotations

import shutil
from pathlib import Path

from app.config import Settings
from app.connectors.base import StorageConnector, verify_approval
from app.crypto import ApprovalTokenSigner
from app.models import (
    AuthState, AuthStatus, BrowseEntry, BrowseResult, InboundItem, OutboundMessage,
    SendResult, utcnow,
)

CV_HINTS = ("cv", "resume")
JD_HINTS = ("jd", "job_desc", "jobdescription")


def _looks_like(name: str, hints: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(h in lowered for h in hints)


class LocalFolderChannel(StorageConnector):
    """No-auth connector over a directory the QRUIT process can read (LF-1).

    Also the stand-in every other channel falls back to conceptually until
    real credentials exist — but concretely it just reads `root`.
    """

    name = "local_folder"

    def __init__(self, settings: Settings, root: Path, signer: ApprovalTokenSigner):
        self.settings = settings
        self.root = root
        self.signer = signer
        self.root.mkdir(parents=True, exist_ok=True)

    async def auth_status(self) -> AuthStatus:
        return AuthStatus(state=AuthState.READY, account_label=str(self.root))

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if self.root.resolve() not in target.parents and target != self.root.resolve():
            raise ValueError("path escapes the configured folder root")
        return target

    async def browse(self, path: str = "") -> BrowseResult:
        target = self._resolve(path)
        entries = []
        if target.exists():
            for child in sorted(target.iterdir()):
                is_folder = child.is_dir()
                rel = str(child.relative_to(self.root))
                entries.append(
                    BrowseEntry(
                        path=rel, name=child.name, is_folder=is_folder,
                        jd_count=1 if not is_folder and _looks_like(child.name, JD_HINTS) else 0,
                        cv_count=1 if not is_folder and _looks_like(child.name, CV_HINTS) else 0,
                        file_count=0 if is_folder else 1,
                    )
                )
        return BrowseResult(path=path, entries=entries)

    async def pull(self, query: str = "", limit: int = 25, folder: str = "") -> list[InboundItem]:
        target = self._resolve(folder)
        if not target.exists():
            return []
        items = []
        for child in sorted(target.iterdir()):
            if child.is_dir():
                continue
            if query and query.lower() not in child.name.lower():
                continue
            items.append(
                InboundItem(
                    source=self.name, item_id=str(child.relative_to(self.root)),
                    name=child.name, has_attachment=True, size_bytes=child.stat().st_size,
                    received_at=utcnow(),
                )
            )
            if len(items) >= limit:
                break
        return items

    async def fetch(self, item_id: str, dest_dir: str) -> str:
        source = self._resolve(item_id)
        if not source.exists():
            raise RuntimeError(f"local_folder: file {item_id} no longer exists")
        dest = Path(dest_dir) / source.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        return str(dest)

    async def send(self, message: OutboundMessage) -> SendResult:
        error = verify_approval(message, self.signer)
        if error:
            return SendResult(success=False, error=error)
        outbox = self.settings.outbox_dir
        outbox.mkdir(parents=True, exist_ok=True)
        message_id = f"local-{utcnow().strftime('%Y%m%dT%H%M%S%f')}"
        path = outbox / f"{message_id}.txt"
        path.write_text(
            f"To: {message.recipient}\nSubject: {message.subject}\n\n{message.body_text}\n",
            encoding="utf-8",
        )
        return SendResult(success=True, provider_message_id=message_id)
