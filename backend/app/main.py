from __future__ import annotations

import json
from urllib.parse import urlparse

import redis
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import text

from app.api import channels, interview, jobs, oauth, public, roles, webhooks
from app.bootstrap import Container, get_container
from app.config import get_settings
from app.observability import (
    PublicHostnameGuardMiddleware,
    RequestIDMiddleware,
    configure_logging,
    metrics_response,
)

TAGS_METADATA = [
    {
        "name": "channels",
        "description": "Connector auth status (Gmail, Outlook, Drive, SharePoint, "
        "OneDrive, WhatsApp, local folder, manual upload). Internal/VPN only.",
    },
    {
        "name": "oauth",
        "description": "PKCE consent flow for Gmail and Outlook (Drive/SharePoint/OneDrive "
        "share these tokens). The /callback route is internet-facing; /connect is not.",
    },
    {
        "name": "roles",
        "description": "Role lifecycle: JD/CV intake, screening, approval checkpoints, "
        "interviews, final report. Internal/VPN only.",
    },
    {
        "name": "jobs",
        "description": "Poll the status/result of a queued job (arq + Redis). Internal/VPN only.",
    },
    {
        "name": "public",
        "description": "Tokenised file hosting Reqruit.ai fetches JD/CV/deep-screen "
        "inputs from. Internet-facing (FRD §5.6).",
    },
    {
        "name": "interview",
        "description": "Candidate-facing interview link (FRD Stage 5). Internet-facing.",
    },
    {
        "name": "webhooks",
        "description": "The one internet-facing exception: WhatsApp inbound. "
        "Verifies the request and enqueues the message — nothing else.",
    },
]

def _public_hostname(base_url: str) -> str:
    return urlparse(base_url).hostname or ""


configure_logging()
_settings = get_settings()
_settings.validate_for_boot()

app = FastAPI(
    title="QRUIT backend",
    description=(
        "Pulls job descriptions and CVs from connected channels, screens candidates "
        "via Reqruit.ai, and keeps a human in the loop with an approval checkpoint "
        "before anything is sent. See FRD.md at the repo root for the full spec."
    ),
    version="0.1.0",
    openapi_tags=TAGS_METADATA,
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(PublicHostnameGuardMiddleware, public_hostname=_public_hostname(_settings.qruit_public_base_url))
# Lets the frontend (a different origin) call this API directly. These
# routes already have no auth of their own yet (deliberate, discussed with
# the client) — scoped to CORS_ALLOWED_ORIGINS, not "*", so this doesn't
# widen exposure any further than the frontend actually needs.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _settings.cors_allowed_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# /api/* — internal network/VPN only, never internet-facing (FRD §5.6).
app.include_router(channels.router)
app.include_router(roles.router)
app.include_router(jobs.router)
app.include_router(oauth.router)

# Internet-facing routes (FRD §5.6): /public/*, /interview/*, /webhooks/whatsapp.
app.include_router(public.router)
app.include_router(interview.router)
app.include_router(webhooks.router)


@app.get("/healthz", tags=["health"], summary="Liveness check — DB + Redis reachability")
async def healthz(container: Container = Depends(get_container)) -> Response:
    problems = []
    try:
        async with container.storage.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        problems.append(f"database unreachable: {exc}")
    try:
        redis.Redis.from_url(container.settings.redis_url, socket_connect_timeout=2).ping()
    except Exception as exc:
        problems.append(f"redis unreachable: {exc}")

    if problems:
        return Response(
            content=json.dumps({"status": "unhealthy", "problems": problems}),
            status_code=503, media_type="application/json",
        )
    return Response(content=json.dumps({"status": "ok"}), media_type="application/json")


@app.get("/metrics", tags=["health"], summary="Prometheus metrics")
def metrics() -> Response:
    return metrics_response()
