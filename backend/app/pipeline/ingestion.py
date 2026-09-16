from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.config import Settings
from app.connectors.registry import ConnectorRegistry
from app.models import Candidate, DocumentClass, Role, RoleStatus, utcnow
from app.services import documents
from app.services.alerts import AlertType, DeliveryService
from app.services.reqruit_client import ReqruitClient
from app.storage import Storage

REQUIRED_JD_FIELDS = ("salary_band", "location", "seniority")


@dataclass
class PublishedFile:
    token: str
    url: str


@dataclass
class IngestResult:
    jd_updated: bool = False
    candidates_added: list[str] = field(default_factory=list)
    unrecognized: list[str] = field(default_factory=list)
    skipped_duplicates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class IngestionPipeline:
    """pull -> fetch -> normalize -> classify -> dedup, identical regardless
    of which connector supplied the item (SKILL.md rule 4)."""

    def __init__(
        self, settings: Settings, storage: Storage, registry: ConnectorRegistry,
        reqruit: ReqruitClient, delivery: DeliveryService,
    ):
        self.settings = settings
        self.storage = storage
        self.registry = registry
        self.reqruit = reqruit
        self.delivery = delivery

    async def ingest_for_role(
        self, role: Role, connector_name: str, query: str = "", folder: str = "", limit: int = 25,
    ) -> IngestResult:
        connector = self.registry.get(connector_name)
        result = IngestResult()
        try:
            items = await connector.pull(query=query, limit=limit, folder=folder)
        except Exception as exc:  # pull must never crash the job (CN-6-adjacent)
            result.errors.append(f"{connector_name}: pull failed — {exc}")
            return result

        for item in items:
            pull_key = documents.pull_dedup_key(item.source, item.item_id)
            if not await self.storage.claim_dedup_key(pull_key, role.id, "pulled"):
                result.skipped_duplicates.append(item.item_id)
                continue

            try:
                dest_dir = str(self.settings.files_dir / role.id)
                local_path = await connector.fetch(item.item_id, dest_dir)
                text = documents.normalize_to_text(local_path)
            except Exception as exc:
                result.errors.append(f"{item.item_id}: fetch/normalize failed — {exc}")
                continue

            doc_class = documents.classify(text, filename=item.name, subject=item.subject)
            if doc_class == DocumentClass.JD:
                await self._handle_jd(role, item, text, result)
            elif doc_class == DocumentClass.CV:
                await self._handle_cv(role, item, text, result)
            else:
                result.unrecognized.append(item.item_id)

        return result

    async def _publish_docx(self, role_id: str, stem: str, text: str) -> PublishedFile:
        """Renders the DOCX rendition Reqruit.ai requires and registers it
        under an opaque token, so QRUIT never hands Reqruit.ai a raw
        connector item id — only tokenised URLs it can serve (FRD §5.4, RQ-3)."""
        dest = str(self.settings.files_dir / role_id / f"{stem}.docx")
        documents.render_docx(text, dest)
        token = await self.storage.register_public_file(
            dest, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        return PublishedFile(token=token, url=f"{self.settings.qruit_public_base_url}/public/files/{token}")

    async def _handle_jd(self, role: Role, item, text: str, result: IngestResult) -> None:
        dedup_key = documents.content_dedup_key(role.id, text, "jd")
        if not await self.storage.claim_dedup_key(dedup_key, role.id, "content"):
            result.skipped_duplicates.append(item.item_id)
            return

        public_url = await self._publish_docx(role.id, f"jd-{item.item_id}", text)
        extracted = await self.reqruit.extract_jd(public_url.url, text=text)
        role.jd_data = extracted
        role.jd_file_token = public_url.token
        role.updated_at = utcnow()
        result.jd_updated = True

        missing = [f for f in REQUIRED_JD_FIELDS if not extracted.get(f)]
        already_asked = set(await self.storage.get_gap_fields_requested(role.id))
        new_gaps = [f for f in missing if f not in already_asked]
        if new_gaps:
            role.status = RoleStatus.AWAITING_GAP_REPLY
            await self.storage.upsert_role(role)
            await self.storage.set_gap_fields_requested(role.id, sorted(already_asked | set(new_gaps)))
            if role.client_contact:
                await self.delivery.send_alert(
                    role, AlertType.GAP_QUESTION, role.client_contact,
                    {"role_title": role.title or role.id, "missing_fields": ", ".join(new_gaps)},
                )
        else:
            role.status = RoleStatus.CV_INTAKE
            await self.storage.upsert_role(role)

    async def _handle_cv(self, role: Role, item, text: str, result: IngestResult) -> None:
        dedup_key = documents.content_dedup_key(role.id, text, "cv")
        if not await self.storage.claim_dedup_key(dedup_key, role.id, "content"):
            result.skipped_duplicates.append(item.item_id)
            return

        public_url = await self._publish_docx(role.id, f"cv-{item.item_id}", text)
        extracted = await self.reqruit.extract_resume(public_url.url, text=text)
        email = extracted.get("email") or (item.sender if "@" in item.sender else "")
        candidate = Candidate(
            id=uuid.uuid4().hex, role_id=role.id, name=extracted.get("name", ""),
            email=email, phone=extracted.get("phone", ""), cv_ref=public_url.token,
            extracted=extracted,
        )
        await self.storage.upsert_candidate(candidate, email_missing_flag=not email)
        result.candidates_added.append(candidate.id)
