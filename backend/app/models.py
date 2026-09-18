from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class AuthState(StrEnum):
    READY = "ready"
    NEEDS_AUTH = "needs_auth"
    ERROR = "error"


class AuthStatus(BaseModel):
    state: AuthState
    reason: str = ""
    account_label: str = ""
    # True when `state == ready` only because no real credentials are
    # configured yet and the connector fell back to safe mock data (so the
    # pipeline stays testable without live accounts) — not a real connection.
    # A production UI should show this as "not connected," not green.
    mock: bool = False


class InboundItem(BaseModel):
    """One pulled (or webhook-enqueued) item, uniform across every connector."""

    source: str
    item_id: str
    name: str
    sender: str = ""
    subject: str = ""
    body_text: str = ""
    size_bytes: int = 0
    received_at: dt.datetime = Field(default_factory=utcnow)
    has_attachment: bool = False
    raw_ref: str = ""


class OutboundMessage(BaseModel):
    """A rendered message queued for send(); requires an approval token."""

    channel: str
    recipient: str
    subject: str = ""
    body_text: str = ""
    template_name: str = ""
    attachments: list[str] = Field(default_factory=list)
    approval_token: str = ""
    role_id: str = ""
    alert_type: str = ""


class SendResult(BaseModel):
    success: bool
    provider_message_id: str = ""
    error: str = ""


class BrowseEntry(BaseModel):
    path: str
    name: str
    is_folder: bool
    jd_count: int = 0
    cv_count: int = 0
    file_count: int = 0


class BrowseResult(BaseModel):
    path: str
    entries: list[BrowseEntry] = Field(default_factory=list)


class DocumentClass(StrEnum):
    JD = "jd"
    CV = "cv"
    COMMAND = "command"
    UNRECOGNIZED = "unrecognized"


class RoleStatus(StrEnum):
    JD_INTAKE = "jd_intake"
    AWAITING_GAP_REPLY = "awaiting_gap_reply"
    CV_INTAKE = "cv_intake"
    SCREENING = "screening"
    AWAITING_SCREENING_APPROVAL = "awaiting_screening_approval"
    INTERVIEWING = "interviewing"
    AWAITING_REPORT_APPROVAL = "awaiting_report_approval"
    CLOSED = "closed"
    PAUSED = "paused"


class ApprovalCommand(StrEnum):
    PROCEED = "PROCEED"
    PAUSE = "PAUSE"
    REVISE_WEIGHTS = "REVISE WEIGHTS"
    SKIP_INTERVIEW = "SKIP INTERVIEW"


class Role(BaseModel):
    id: str
    title: str = ""
    client_contact: str = ""
    notify_email: str = ""
    sla_hours: int = 48
    status: RoleStatus = RoleStatus.JD_INTAKE
    jd_data: dict = Field(default_factory=dict)
    jd_file_token: str = ""
    screening_weights: dict = Field(default_factory=dict)
    notify_all_candidates: bool = False
    created_at: dt.datetime = Field(default_factory=utcnow)
    updated_at: dt.datetime = Field(default_factory=utcnow)


class Candidate(BaseModel):
    id: str
    role_id: str
    name: str = ""
    email: str = ""
    phone: str = ""
    cv_ref: str = ""
    extracted: dict = Field(default_factory=dict)
    score: float | None = None
    shortlisted: bool = False
    interview_session_id: str = ""
    interview_status: str = ""
    skip_interview: bool = False
    deep_screen_file_token: str = ""
    created_at: dt.datetime = Field(default_factory=utcnow)


class ApprovalToken(BaseModel):
    token: str
    role_id: str
    checkpoint_type: str
    issued_at: dt.datetime = Field(default_factory=utcnow)
    resolved: bool = False
    resolved_at: dt.datetime | None = None
    resolution: str = ""


class AuditRecord(BaseModel):
    id: str
    role_id: str
    channel: str
    alert_type: str
    recipient: str
    status: str
    provider_message_id: str = ""
    error: str = ""
    created_at: dt.datetime = Field(default_factory=utcnow)
