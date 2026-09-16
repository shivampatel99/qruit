from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.bootstrap import Container, get_container
from app.models import Candidate, Role

router = APIRouter(prefix="/interview", tags=["interview"])


class InterviewState(BaseModel):
    session_id: str
    role_title: str
    candidate_name: str
    status: str
    prompt: str = ""


class InterviewReply(BaseModel):
    message: str


async def _find_by_session(session_id: str, container: Container) -> tuple[Role, Candidate] | tuple[None, None]:
    for role in await container.storage.list_roles():
        for candidate in await container.storage.list_candidates(role.id):
            if candidate.interview_session_id == session_id:
                return role, candidate
    return None, None


@router.get(
    "/{session_id}",
    response_model=InterviewState,
    summary="Open an interview session (the link sent in interview_invite)",
)
async def get_interview(session_id: str, container: Container = Depends(get_container)) -> InterviewState:
    role, candidate = await _find_by_session(session_id, container)
    if candidate is None:
        raise HTTPException(404, "interview session not found")

    status = await container.interview_provider.session_status(session_id)
    prompt = "" if status == "completed" else (await container.interview_provider.next(session_id)).get("prompt", "")
    return InterviewState(
        session_id=session_id, role_title=role.title or role.id,
        candidate_name=candidate.name, status=status, prompt=prompt,
    )


@router.post(
    "/{session_id}/respond",
    response_model=InterviewState,
    summary="Submit an answer and receive the next prompt",
)
async def respond(
    session_id: str, body: InterviewReply, container: Container = Depends(get_container),
) -> InterviewState:
    role, candidate = await _find_by_session(session_id, container)
    if candidate is None:
        raise HTTPException(404, "interview session not found")

    result = await container.interview_provider.next(session_id, body.message)
    return InterviewState(
        session_id=session_id, role_title=role.title or role.id, candidate_name=candidate.name,
        status=result.get("status", "in_progress"), prompt=result.get("prompt", ""),
    )
