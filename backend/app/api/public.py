from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.bootstrap import Container, get_container

router = APIRouter(prefix="/public/files", tags=["public"])


@router.get("/{token}", summary="Serve a tokenised JD/CV/deep-screen file to Reqruit.ai")
async def get_public_file(token: str, container: Container = Depends(get_container)) -> FileResponse:
    record = await container.storage.get_public_file(token)
    if record is None:
        raise HTTPException(404, "file not found or token has expired")
    return FileResponse(record["local_path"], media_type=record["content_type"] or "application/octet-stream")
