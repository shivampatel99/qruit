from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.bootstrap import Container, get_container
from app.connectors.oauth_base import OAuthConnector
from app.models import AuthStatus
from app.services.connector_config import REQUIRED_FIELDS, apply_config, reset_config

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


class ChannelCredentials(BaseModel):
    """Every field is optional here — which ones are actually required
    depends on `channel` (see the table in `POST /{channel}/credentials`'s
    description) and is enforced server-side, not by this schema."""

    client_id: str = Field("", description="gmail / outlook — OAuth client ID")
    client_secret: str = Field("", description="gmail / outlook — OAuth client secret")
    tenant: str = Field("", description="outlook — Entra ID directory (tenant) ID")
    folder_id: str = Field("", description="gdrive — Drive folder ID to watch")
    site_id: str = Field("", description="sharepoint — site ID; blank = the signed-in user's OneDrive")
    folder_path: str = Field("", description="sharepoint — folder path within that site/library")
    token: str = Field("", description="whatsapp — permanent access token")
    phone_id: str = Field("", description="whatsapp — WhatsApp phone number ID")
    app_secret: str = Field("", description="whatsapp — app secret, signs webhook payloads")
    verify_token: str = Field("", description="whatsapp — arbitrary string, also entered in Meta's webhook config")
    notify_email: str = Field(
        "", description="any channel — where to email a reconnect link if this channel later goes down"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"client_id": "982...apps.googleusercontent.com", "client_secret": "GOCSPX-...",
                 "notify_email": "ops@agency.example"},
                {"token": "EAAP...", "phone_id": "1302500432952785", "notify_email": "ops@agency.example"},
            ]
        }
    }


# Maps this request body's field names onto the Settings attribute names
# CHANNEL_FIELDS/REQUIRED_FIELDS use, per channel.
_REQUEST_TO_SETTINGS: dict[str, dict[str, str]] = {
    "gmail": {"client_id": "google_client_id", "client_secret": "google_client_secret"},
    "outlook": {"client_id": "ms_client_id", "client_secret": "ms_client_secret", "tenant": "ms_tenant"},
    "gdrive": {"folder_id": "google_drive_folder_id"},
    "sharepoint": {"site_id": "sharepoint_site_id", "folder_path": "sharepoint_folder_path"},
    "whatsapp": {
        "token": "whatsapp_token", "phone_id": "whatsapp_phone_id",
        "app_secret": "whatsapp_app_secret", "verify_token": "whatsapp_verify_token",
    },
}


@router.post(
    "/{channel}/credentials",
    response_model=AuthStatus,
    summary="Submit this channel's credentials and connect it",
    description=(
        "No auth on this route yet — deliberately open until this API is embedded behind a "
        "product with its own auth layer (discussed with the client, not yet built).\n\n"
        "Required fields per channel:\n"
        "- **gmail**: `client_id`, `client_secret`\n"
        "- **outlook**: `client_id`, `client_secret`, `tenant`\n"
        "- **gdrive**: `folder_id`\n"
        "- **sharepoint**: none (all optional — blank `site_id` falls back to OneDrive)\n"
        "- **whatsapp**: `token`, `phone_id`\n\n"
        "`notify_email` is optional on every channel and is where a reconnect link gets "
        "emailed if the channel later needs reauthorizing (see the `check_channel_health` "
        "background job).\n\n"
        "For gmail/outlook, follow up with `GET /api/oauth/{channel}/connect` to get the "
        "authorize URL to open in a new tab; gdrive/sharepoint/whatsapp are ready immediately, "
        "no separate OAuth step."
    ),
    responses={
        400: {"description": "Missing a required field for this channel"},
        404: {"description": "Unknown channel — must be one of gmail, outlook, gdrive, sharepoint, whatsapp"},
    },
)
async def submit_channel_credentials(
    body: ChannelCredentials,
    channel: str = Path(description="gmail, outlook, gdrive, sharepoint, or whatsapp"),
    container: Container = Depends(get_container),
) -> AuthStatus:
    if channel not in _REQUEST_TO_SETTINGS:
        raise HTTPException(404, f"'{channel}' has no configurable credentials")

    field_map = _REQUEST_TO_SETTINGS[channel]
    config = {
        settings_field: value
        for req_field, settings_field in field_map.items()
        if (value := getattr(body, req_field))
    }
    missing = [f for f in REQUIRED_FIELDS[channel] if f not in config]
    if missing:
        raise HTTPException(400, f"{channel}: missing required field(s): {', '.join(missing)}")

    encrypted = container.cipher.encrypt(json.dumps(config))
    await container.storage.save_connector_config(channel, encrypted, body.notify_email)
    apply_config(container.settings, channel, config)

    connector = container.registry.get(channel)
    return await connector.auth_status()


@router.post(
    "/{channel}/disconnect",
    response_model=AuthStatus,
    summary="Disconnect this channel and forget its credentials",
    description=(
        "Clears whatever was submitted through /credentials for this channel, and — for "
        "gmail/outlook specifically — also revokes the stored OAuth token, so a fresh "
        "/credentials + /connect is needed to use it again. Falls back to mock mode "
        "automatically if nothing else still needs the real credentials (gmail/outlook "
        "share one mock flag: disconnecting one doesn't break the other if it's still "
        "configured)."
    ),
)
async def disconnect_channel(
    channel: str = Path(description="gmail, outlook, gdrive, sharepoint, or whatsapp"),
    container: Container = Depends(get_container),
) -> AuthStatus:
    if channel not in _REQUEST_TO_SETTINGS:
        raise HTTPException(404, f"'{channel}' has no configurable credentials")

    connector = container.registry.get(channel)
    # gdrive/sharepoint piggyback on gmail/outlook's OAuth token — only
    # delete the stored token when disconnecting the channel that actually
    # owns it, not the ones just borrowing it for a folder/site setting.
    if isinstance(connector, OAuthConnector) and connector.channel == channel:
        await container.storage.delete_channel_account(connector.channel, connector.account_key)

    await container.storage.delete_connector_config(channel)
    reset_config(container.settings, channel)

    return await connector.auth_status()
