from __future__ import annotations

import re
from dataclasses import dataclass

from app.connectors.registry import ConnectorRegistry
from app.crypto import ApprovalTokenSigner
from app.models import ApprovalCommand, InboundItem, RoleStatus, utcnow
from app.pipeline.screening import SCREENING_CHECKPOINT, ScreeningPipeline
from app.services import documents
from app.services.alerts import AlertType, DeliveryService
from app.storage import Storage

COMMAND_PATTERN = re.compile(
    r"^\s*(PROCEED|PAUSE|REVISE\s+WEIGHTS|SKIP\s+INTERVIEW)\s*$", re.IGNORECASE,
)


def parse_command(text: str) -> ApprovalCommand | None:
    match = COMMAND_PATTERN.match(text.strip())
    if not match:
        return None
    normalized = re.sub(r"\s+", " ", match.group(1).upper())
    try:
        return ApprovalCommand(normalized)
    except ValueError:
        return None


@dataclass
class ReplyOutcome:
    applied: bool
    reason: str = ""


class ApprovalPipeline:
    """Applies PROCEED / PAUSE / REVISE WEIGHTS / SKIP INTERVIEW replies to
    the role they belong to, matched by sender address or phone number.
    Nothing here ever defaults to an implicit PROCEED (SC-7)."""

    def __init__(
        self, storage: Storage, registry: ConnectorRegistry, delivery: DeliveryService,
        screening: ScreeningPipeline, signer: ApprovalTokenSigner,
    ):
        self.storage = storage
        self.registry = registry
        self.delivery = delivery
        self.screening = screening
        self.signer = signer

    async def process_inbound_item(self, item: InboundItem) -> ReplyOutcome:
        doc_class = documents.classify(item.body_text, subject=item.subject)
        if doc_class != documents.DocumentClass.COMMAND:
            return ReplyOutcome(applied=False, reason="not a recognized command")

        sender = item.sender.strip()
        role = await self._find_role_by_contact(sender) or await self._find_role_by_candidate_phone(sender)
        if role is None:
            return ReplyOutcome(applied=False, reason=f"sender '{sender}' not registered to any waiting role")

        checkpoint = await self.storage.latest_unresolved_token(role.id, SCREENING_CHECKPOINT)
        if checkpoint is None:
            return ReplyOutcome(applied=False, reason="no open checkpoint for this role — late/duplicate reply ignored")

        command = parse_command(item.body_text)
        if command is None:
            return ReplyOutcome(applied=False, reason="reply text did not match PROCEED/PAUSE/REVISE WEIGHTS/SKIP INTERVIEW")

        # Atomic: only the reply that actually flips the checkpoint from open
        # to resolved may apply it — a second concurrent/redelivered reply
        # racing the same checkpoint must not double-send candidate_ack etc.
        resolved_now = await self.storage.resolve_all_unresolved(role.id, SCREENING_CHECKPOINT, command.value)
        if not resolved_now:
            return ReplyOutcome(applied=False, reason="checkpoint was already resolved by a concurrent reply")
        await self._apply(role.id, command)
        return ReplyOutcome(applied=True, reason=command.value)

    async def _find_role_by_contact(self, sender: str):
        sender_lower = sender.lower()
        for role in await self.storage.list_roles():
            if role.client_contact and role.client_contact.lower() == sender_lower:
                return role
        return None

    async def _find_role_by_candidate_phone(self, sender: str):
        """The WhatsApp equivalent of matching an e-mail reply by sender
        address (WA-8): a registered candidate's phone number resolves to
        their role, even when it's not the client-contact channel."""
        for role in await self.storage.list_roles():
            for candidate in await self.storage.list_candidates(role.id):
                if candidate.phone and candidate.phone == sender:
                    return role
        return None

    async def _apply(self, role_id: str, command: ApprovalCommand) -> None:
        role = await self.storage.get_role(role_id)
        if role is None:
            return

        if command == ApprovalCommand.PAUSE:
            role.status = RoleStatus.PAUSED
            role.updated_at = utcnow()
            await self.storage.upsert_role(role)
            return

        if command == ApprovalCommand.REVISE_WEIGHTS:
            role.status = RoleStatus.SCREENING
            await self.storage.upsert_role(role)
            await self.screening.run(role)
            return

        if command == ApprovalCommand.SKIP_INTERVIEW:
            for candidate in await self.storage.list_candidates(role.id):
                if candidate.shortlisted:
                    candidate.skip_interview = True
                    await self.storage.upsert_candidate(candidate)
            role.status = RoleStatus.AWAITING_REPORT_APPROVAL
            role.updated_at = utcnow()
            await self.storage.upsert_role(role)
            await self._notify_shortlisted_candidates(role)
            return

        if command == ApprovalCommand.PROCEED:
            role.status = RoleStatus.INTERVIEWING
            role.updated_at = utcnow()
            await self.storage.upsert_role(role)
            await self._notify_shortlisted_candidates(role)

    async def _notify_shortlisted_candidates(self, role) -> None:
        for candidate in await self.storage.list_candidates(role.id):
            if not candidate.shortlisted:
                continue
            recipient = candidate.email or candidate.phone
            if not recipient:
                continue
            token = self.signer.issue(role.id, AlertType.CANDIDATE_ACK.value)
            await self.delivery.send_alert(
                role, AlertType.CANDIDATE_ACK, recipient,
                {"role_title": role.title or role.id, "candidate_name": candidate.name or "there"},
                approval_token=token,
            )
