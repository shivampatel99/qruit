from __future__ import annotations

import re
import uuid
from enum import StrEnum
from string import Template

from app.config import Settings
from app.connectors.base import Connector
from app.connectors.registry import ConnectorRegistry
from app.crypto import ApprovalTokenSigner
from app.models import AuditRecord, AuthState, OutboundMessage, Role, utcnow
from app.storage import Storage

PHONE_RE = re.compile(r"^\+?[\d\s()-]{7,}$")


class AlertType(StrEnum):
    CHECKPOINT_REQUEST = "checkpoint_request"
    STATUS_UPDATE = "status_update"
    GAP_QUESTION = "gap_question"
    CLIENT_JD_CONFIRMATION = "client_jd_confirmation"
    REPORT_EMAIL = "report_email"
    CANDIDATE_ACK = "candidate_ack"
    INTERVIEW_INVITE = "interview_invite"
    INTERVIEW_REMINDER = "interview_reminder"
    INTERVIEW_NUDGE = "interview_nudge"


# Alerts that raise or represent a checkpoint themselves — the system is
# allowed to send these without a prior human approval, per FRD §3 rule 2
# ("purely informational" pings and the ask itself don't need pre-approval).
SELF_ISSUING_ALERTS = {
    AlertType.CHECKPOINT_REQUEST,
    AlertType.STATUS_UPDATE,
    AlertType.GAP_QUESTION,
    AlertType.CLIENT_JD_CONFIRMATION,
}

# Everything else is a *consequence* of an already-approved checkpoint, so
# the caller must hand in the approval token that unblocked that checkpoint.
APPROVAL_REQUIRED_ALERTS = set(AlertType) - SELF_ISSUING_ALERTS

TEMPLATES: dict[AlertType, tuple[str, str]] = {
    AlertType.CHECKPOINT_REQUEST: (
        "Action needed: shortlist ready for $role_title",
        "Hi,\n\nDeep screening is complete for $role_title. "
        "$summary\n\nReply PROCEED, PAUSE, REVISE WEIGHTS, or SKIP INTERVIEW to continue.\n\n— QRUIT",
    ),
    AlertType.STATUS_UPDATE: (
        "Status update: $role_title",
        "Hi,\n\n$summary\n\n— QRUIT",
    ),
    AlertType.GAP_QUESTION: (
        "Quick info needed for $role_title",
        "Hi,\n\nThe job description for $role_title is missing: $missing_fields. "
        "Could you reply with these details so screening can begin?\n\n— QRUIT",
    ),
    AlertType.CLIENT_JD_CONFIRMATION: (
        "Please confirm the parsed JD for $role_title",
        "Hi,\n\nHere is what QRUIT parsed for $role_title:\n$summary\n\n"
        "Reply PROCEED to confirm and begin screening, or PAUSE to hold.\n\n— QRUIT",
    ),
    AlertType.REPORT_EMAIL: (
        "Final recommendation report: $role_title",
        "Hi,\n\nAttached is the final recommendation report for $role_title.\n\n— QRUIT",
    ),
    AlertType.CANDIDATE_ACK: (
        "Thanks for applying — $role_title",
        "Hi $candidate_name,\n\nThanks for applying to $role_title. "
        "We're reviewing your application and will be in touch.\n\n— QRUIT",
    ),
    AlertType.INTERVIEW_INVITE: (
        "You're invited to interview — $role_title",
        "Hi $candidate_name,\n\nPlease complete your interview for $role_title here: "
        "$interview_link\n\n— QRUIT",
    ),
    AlertType.INTERVIEW_REMINDER: (
        "Reminder: complete your interview — $role_title",
        "Hi $candidate_name,\n\nJust a reminder to complete your interview for $role_title: "
        "$interview_link\n\n— QRUIT",
    ),
    AlertType.INTERVIEW_NUDGE: (
        "Last call: your interview for $role_title",
        "Hi $candidate_name,\n\nThis is a final nudge to complete your interview for "
        "$role_title before the slot closes: $interview_link\n\n— QRUIT",
    ),
}

WHATSAPP_TEMPLATE_NAMES: dict[AlertType, str] = {
    AlertType.INTERVIEW_INVITE: "interview_invite",
    AlertType.INTERVIEW_REMINDER: "interview_reminder",
    AlertType.STATUS_UPDATE: "status_update",
    AlertType.REPORT_EMAIL: "report_digest",
}


class AlertSendOutcome:
    def __init__(self, sent: bool, skipped_reason: str = "", error: str = "", provider_message_id: str = ""):
        self.sent = sent
        self.skipped_reason = skipped_reason
        self.error = error
        self.provider_message_id = provider_message_id


class DeliveryService:
    """Renders one of the 9 alert templates and hands it to the first ready
    connector that can reach the recipient — e-mail via Gmail/Outlook, or
    WhatsApp for a phone number. Every non-informational send must already
    carry an approval token (SKILL.md rule 2)."""

    def __init__(self, settings: Settings, storage: Storage, registry: ConnectorRegistry, signer: ApprovalTokenSigner):
        self.settings = settings
        self.storage = storage
        self.registry = registry
        self.signer = signer

    async def send_alert(
        self,
        role: Role,
        alert_type: AlertType,
        recipient: str,
        context: dict,
        approval_token: str = "",
        attachments: list[str] | None = None,
    ) -> AlertSendOutcome:
        if not recipient:
            return AlertSendOutcome(sent=False, skipped_reason="no recipient contact registered")

        if alert_type in SELF_ISSUING_ALERTS and not approval_token:
            approval_token = self.signer.issue(role.id, alert_type.value)
        if alert_type in APPROVAL_REQUIRED_ALERTS and not approval_token:
            return AlertSendOutcome(
                sent=False, error=f"{alert_type.value}: no approval token supplied for this send"
            )

        subject_tpl, body_tpl = TEMPLATES[alert_type]
        subject = Template(subject_tpl).safe_substitute(context)
        body = Template(body_tpl).safe_substitute(context)

        is_phone = bool(PHONE_RE.match(recipient)) and "@" not in recipient
        connector = await self._pick_connector(is_phone)
        if connector is None:
            await self._audit(role.id, "none", alert_type.value, recipient, "failed", error="no ready connector to send from")
            return AlertSendOutcome(sent=False, error="no connected mailbox/WhatsApp number ready to send")

        message = OutboundMessage(
            channel=connector.name, recipient=recipient, subject=subject, body_text=body,
            template_name=WHATSAPP_TEMPLATE_NAMES.get(alert_type, alert_type.value),
            attachments=attachments or [], approval_token=approval_token, role_id=role.id,
            alert_type=alert_type.value,
        )
        result = await connector.send(message)
        status = "sent" if result.success else "failed"
        await self._audit(
            role.id, connector.name, alert_type.value, recipient, status,
            provider_message_id=result.provider_message_id, error=result.error,
        )
        if result.success and role.notify_email and role.notify_email.lower() != recipient.lower():
            await self._send_recruiter_copy(role, alert_type, subject, body, approval_token)
        return AlertSendOutcome(
            sent=result.success, error=result.error, provider_message_id=result.provider_message_id,
        )

    async def _send_recruiter_copy(
        self, role: Role, alert_type: AlertType, subject: str, body: str, approval_token: str,
    ) -> None:
        """A copy of every client-facing alert to the workspace notification
        address (planned in FRD §7's alert table; best-effort — a failure
        here must never affect the primary send it's copying)."""
        connector = await self._pick_connector(is_phone=False)
        if connector is None:
            return
        copy_message = OutboundMessage(
            channel=connector.name, recipient=role.notify_email, subject=f"[copy] {subject}",
            body_text=body, template_name=alert_type.value, approval_token=approval_token,
            role_id=role.id, alert_type=alert_type.value,
        )
        result = await connector.send(copy_message)
        await self._audit(
            role.id, connector.name, f"{alert_type.value}_copy", role.notify_email,
            "sent" if result.success else "failed",
            provider_message_id=result.provider_message_id, error=result.error,
        )

    async def _pick_connector(self, is_phone: bool) -> Connector | None:
        if is_phone:
            whatsapp = self.registry.get("whatsapp")
            status = await whatsapp.auth_status()
            return whatsapp if status.state == AuthState.READY else None
        for name in ("gmail", "outlook"):
            connector = self.registry.get(name)
            status = await connector.auth_status()
            if status.state == AuthState.READY:
                return connector
        return None

    async def _audit(
        self, role_id: str, channel: str, alert_type: str, recipient: str, status: str,
        provider_message_id: str = "", error: str = "",
    ) -> None:
        await self.storage.record_audit(
            AuditRecord(
                id=uuid.uuid4().hex, role_id=role_id, channel=channel, alert_type=alert_type,
                recipient=recipient, status=status, provider_message_id=provider_message_id,
                error=error, created_at=utcnow(),
            )
        )
