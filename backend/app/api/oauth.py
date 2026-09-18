from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.bootstrap import Container, get_container
from app.connectors.oauth_base import OAuthConnector

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# gdrive/sharepoint/onedrive share their token with gmail/outlook (see each
# connector's `channel` attribute) — there is one consent flow per provider,
# not per connector, matching QRUIT_Connector_Dependencies.html GD-1/§6.
CONNECTABLE = ("gmail", "outlook")


class ConnectResult(BaseModel):
    authorize_url: str


class CallbackResult(BaseModel):
    connected: bool
    channel: str = ""
    account_label: str = ""
    error: str = ""


async def build_authorize_url(channel: str, container: Container) -> str:
    """Shared by the /connect route and the reauth-alert cron job (worker.py)
    — both need a fresh, actually-usable authorize link, which means saving
    the pending state so /callback will recognize it, not just building the URL."""
    connector = container.registry.get(channel)
    if not isinstance(connector, OAuthConnector):
        raise ValueError(f"'{channel}' is not an OAuth connector")
    redirect_uri = f"{container.settings.qruit_public_base_url}/api/oauth/callback"
    authorize_url, state, verifier = connector.authorize_url(redirect_uri)
    await container.storage.save_oauth_pending(state, channel, verifier, redirect_uri)
    return authorize_url


@router.get(
    "/{channel}/connect",
    response_model=ConnectResult,
    summary="Start the OAuth consent flow for Gmail or Outlook",
)
async def connect(channel: str, container: Container = Depends(get_container)) -> ConnectResult:
    if channel not in CONNECTABLE:
        raise HTTPException(404, f"'{channel}' has no OAuth flow of its own — connect 'gmail' or 'outlook'")
    if not isinstance(container.registry.get(channel), OAuthConnector):
        raise HTTPException(404, f"'{channel}' is not an OAuth connector")
    return ConnectResult(authorize_url=await build_authorize_url(channel, container))


@router.get(
    "/callback",
    response_model=CallbackResult,
    summary="OAuth redirect target — register this exact URL with Google/Microsoft",
)
async def callback(
    code: str = "", state: str = "", error: str = "",
    container: Container = Depends(get_container),
) -> CallbackResult:
    if error:
        return CallbackResult(connected=False, error=error)

    pending = await container.storage.pop_oauth_pending(state)
    if pending is None:
        # AU-4: an unrecognized/replayed state must not trigger a token exchange.
        return CallbackResult(connected=False, error="unknown or expired state — no token exchange attempted")

    connector = container.registry.get(pending["channel"])
    assert isinstance(connector, OAuthConnector)
    blob = await connector.exchange_code(code, pending["redirect_uri"], pending["code_verifier"])
    await connector.store_token(blob)
    return CallbackResult(connected=True, channel=pending["channel"], account_label=blob.account_label)
