from __future__ import annotations

from fastapi import APIRouter, Depends

from app.bootstrap import Container, get_container
from app.models import AuthStatus

router = APIRouter(prefix="/api/channels", tags=["channels"])


@router.get(
    "/{name}/status",
    response_model=AuthStatus,
    summary="Check one connector's auth status",
    description="Returns `ready`, `needs_auth`, or `error` with a plain-language reason.",
)
async def channel_status(name: str, container: Container = Depends(get_container)) -> AuthStatus:
    connector = container.registry.get(name)
    return await connector.auth_status()


@router.get(
    "",
    response_model=dict[str, AuthStatus],
    summary="Check every connector's auth status",
)
async def all_channel_statuses(container: Container = Depends(get_container)) -> dict[str, AuthStatus]:
    return {name: await c.auth_status() for name, c in container.registry.all().items()}
