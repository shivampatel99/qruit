from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.bootstrap import Container, get_container

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobStatusResult(BaseModel):
    status: str
    result: dict | list | None = None
    error: str = ""


@router.get(
    "/{job_id}",
    response_model=JobStatusResult,
    summary="Poll a queued job's status/result",
    description="Status is one of: queued, deferred, in_progress, complete, failed, not_found.",
)
async def job_status(job_id: str, container: Container = Depends(get_container)) -> JobStatusResult:
    info = await container.jobs.status(job_id)
    return JobStatusResult(**info)
