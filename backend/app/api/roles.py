from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.bootstrap import Container, get_container
from app.models import Candidate, Role
from app.services.alerts import AlertType

router = APIRouter(prefix="/api/roles", tags=["roles"])


class CreateRoleRequest(BaseModel):
    title: str = ""
    client_contact: str = ""
    notify_email: str = ""
    sla_hours: int = 48
    notify_all_candidates: bool = False


class PullRequest(BaseModel):
    connector: str
    query: str = ""
    folder: str = ""
    limit: int = 25


class CheckRepliesRequest(BaseModel):
    connector: str
    query: str = ""
    limit: int = 25


class AlertOutcome(BaseModel):
    sent: bool
    error: str = ""
    skipped_reason: str = ""


class JobEnqueued(BaseModel):
    """Returned by every trigger that does real work against an external
    service (Gmail/Outlook/Reqruit.ai/WhatsApp) — queued so a slow or flaky
    provider never blocks the API, per loop-doc §1.4. Poll
    GET /api/jobs/{job_id} for the result."""

    job_id: str


@router.post("", response_model=Role, summary="Create a role")
async def create_role(body: CreateRoleRequest, container: Container = Depends(get_container)) -> Role:
    role = Role(
        id=uuid.uuid4().hex, title=body.title, client_contact=body.client_contact,
        notify_email=body.notify_email, sla_hours=body.sla_hours,
        notify_all_candidates=body.notify_all_candidates,
    )
    await container.storage.upsert_role(role)
    return role


@router.get("", response_model=list[Role], summary="List all roles")
async def list_roles(container: Container = Depends(get_container)) -> list[Role]:
    return await container.storage.list_roles()


async def _get_role_or_404(role_id: str, container: Container) -> Role:
    role = await container.storage.get_role(role_id)
    if role is None:
        raise HTTPException(404, f"role {role_id} not found")
    return role


@router.get("/{role_id}", response_model=Role, summary="Get one role")
async def get_role(role_id: str, container: Container = Depends(get_container)) -> Role:
    return await _get_role_or_404(role_id, container)


@router.get(
    "/{role_id}/candidates",
    response_model=list[Candidate],
    summary="List candidates for a role",
)
async def list_candidates(role_id: str, container: Container = Depends(get_container)) -> list[Candidate]:
    await _get_role_or_404(role_id, container)
    return await container.storage.list_candidates(role_id)


@router.post(
    "/{role_id}/pull",
    response_model=JobEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Pull JDs/CVs for a role from one connector",
    description="Queues a job that fetches, normalizes, classifies and de-duplicates "
    "whatever the connector returns. Source-agnostic — same pipeline for every channel. "
    "Poll GET /api/jobs/{job_id} for the result.",
)
async def pull_for_role(role_id: str, body: PullRequest, container: Container = Depends(get_container)) -> JobEnqueued:
    await _get_role_or_404(role_id, container)
    job_id = await container.jobs.enqueue("pull_for_role", role_id, body.connector, body.query, body.folder, body.limit)
    return JobEnqueued(job_id=job_id)


@router.post(
    "/{role_id}/run_screening",
    response_model=JobEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue deep screening and the shortlist checkpoint",
)
async def run_screening(role_id: str, container: Container = Depends(get_container)) -> JobEnqueued:
    await _get_role_or_404(role_id, container)
    job_id = await container.jobs.enqueue("run_screening", role_id)
    return JobEnqueued(job_id=job_id)


@router.post(
    "/{role_id}/check_replies",
    response_model=JobEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a pull-and-apply of approval-command replies",
    description="Reads PROCEED / PAUSE / REVISE WEIGHTS / SKIP INTERVIEW replies "
    "from the connector, case-insensitively, matched by sender to this role.",
)
async def check_replies(
    role_id: str, body: CheckRepliesRequest, container: Container = Depends(get_container),
) -> JobEnqueued:
    await _get_role_or_404(role_id, container)
    job_id = await container.jobs.enqueue("check_replies", role_id, body.connector, body.query, body.limit)
    return JobEnqueued(job_id=job_id)


@router.post("/{role_id}/status_update", response_model=AlertOutcome, summary="Send an on-demand status update")
async def status_update(role_id: str, container: Container = Depends(get_container)) -> AlertOutcome:
    role = await _get_role_or_404(role_id, container)
    outcome = await container.delivery.send_alert(
        role, AlertType.STATUS_UPDATE, role.client_contact,
        {"role_title": role.title or role.id, "summary": f"Role is currently: {role.status.value}"},
    )
    return AlertOutcome(sent=outcome.sent, error=outcome.error, skipped_reason=outcome.skipped_reason)


@router.post(
    "/{role_id}/start_interviews",
    response_model=JobEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue Reqruit.ai interview starts for shortlisted candidates",
)
async def start_interviews(role_id: str, container: Container = Depends(get_container)) -> JobEnqueued:
    await _get_role_or_404(role_id, container)
    job_id = await container.jobs.enqueue("start_interviews", role_id)
    return JobEnqueued(job_id=job_id)


@router.post(
    "/{role_id}/poll_interviews",
    response_model=JobEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an interview-session poll; advances the role once all are done",
)
async def poll_interviews(role_id: str, container: Container = Depends(get_container)) -> JobEnqueued:
    await _get_role_or_404(role_id, container)
    job_id = await container.jobs.enqueue("poll_interviews", role_id)
    return JobEnqueued(job_id=job_id)


@router.post(
    "/{role_id}/approve_report",
    response_model=JobEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue approval and delivery of the final recommendation report",
    description="The Module 4 checkpoint: queues a job that builds the PDF and sends "
    "report_email — there is no auto-send path (RP-3). The job itself atomically "
    "claims the role so a duplicate/racing enqueue is a no-op, not a double-send.",
)
async def approve_report(role_id: str, container: Container = Depends(get_container)) -> JobEnqueued:
    await _get_role_or_404(role_id, container)
    job_id = await container.jobs.enqueue("approve_report", role_id)
    return JobEnqueued(job_id=job_id)
