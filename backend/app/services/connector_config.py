from __future__ import annotations

import json
import logging

from app.config import Settings
from app.crypto import TokenCipher
from app.storage import Storage

# Which Settings attributes each channel's submitted credentials map onto.
# onedrive has no entry — it piggybacks on outlook's client/token (same
# assumption the existing OAuth connect flow already makes).
CHANNEL_FIELDS: dict[str, list[str]] = {
    "gmail": ["google_client_id", "google_client_secret"],
    "outlook": ["ms_client_id", "ms_client_secret", "ms_tenant"],
    "gdrive": ["google_drive_folder_id"],
    "sharepoint": ["sharepoint_site_id", "sharepoint_folder_path"],
    "whatsapp": ["whatsapp_token", "whatsapp_phone_id", "whatsapp_app_secret", "whatsapp_verify_token"],
}

# Fields that must be present for the channel to be usable at all (a bare
# minimum, not every field in CHANNEL_FIELDS — e.g. sharepoint_site_id is
# optional, blank means "use the signed-in user's OneDrive").
REQUIRED_FIELDS: dict[str, list[str]] = {
    "gmail": ["google_client_id", "google_client_secret"],
    "outlook": ["ms_client_id", "ms_client_secret", "ms_tenant"],
    "gdrive": ["google_drive_folder_id"],
    "sharepoint": [],
    "whatsapp": ["whatsapp_token", "whatsapp_phone_id"],
}

# Flipping the relevant mock flag off is what actually makes a channel go
# live the moment credentials are saved — matches "they click, we connect
# for them," no separate manual .env edit.
MOCK_FLAGS: dict[str, str] = {
    "gmail": "qruit_email_mock",
    "outlook": "qruit_email_mock",
    "whatsapp": "whatsapp_mock",
}


def apply_config(settings: Settings, channel: str, config: dict) -> None:
    for field in CHANNEL_FIELDS[channel]:
        if field in config:
            setattr(settings, field, config[field])
    mock_flag = MOCK_FLAGS.get(channel)
    if mock_flag:
        setattr(settings, mock_flag, False)


def reset_config(settings: Settings, channel: str) -> None:
    """Undoes apply_config — blanks this channel's fields and, where safe,
    re-enables its mock flag."""
    for field in CHANNEL_FIELDS[channel]:
        setattr(settings, field, "")
    mock_flag = MOCK_FLAGS.get(channel)
    if mock_flag == "qruit_email_mock":
        # gmail and outlook share this one flag — only re-enable mock mode
        # if the *other* email channel also has no real credentials left,
        # so disconnecting one doesn't silently break the other.
        other = "outlook" if channel == "gmail" else "gmail"
        if not any(getattr(settings, f) for f in CHANNEL_FIELDS[other]):
            settings.qruit_email_mock = True
    elif mock_flag:
        setattr(settings, mock_flag, True)


async def load_all_configs(settings: Settings, storage: Storage, cipher: TokenCipher) -> None:
    """Called once at container build time so a restart doesn't fall back
    to .env defaults for anything already configured through the API —
    the DB is the source of truth for a channel from the first time its
    credentials are submitted.

    A single row that fails to decrypt (e.g. QRUIT_APPROVAL_SECRET rotated,
    per RUNBOOK.md) must not take down the whole server at boot — that
    channel just falls back to whatever's in .env, same as if it had never
    been configured through the API, and gets logged so it's not a silent
    mystery."""
    for row in await storage.list_connector_configs():
        if not row["encrypted_config"]:
            continue
        try:
            config = json.loads(cipher.decrypt(row["encrypted_config"]))
        except ValueError:
            logging.getLogger(__name__).warning(
                "connector_configs: could not decrypt stored config for %r "
                "(QRUIT_APPROVAL_SECRET rotated? see RUNBOOK.md) — falling back to .env", row["channel"],
            )
            continue
        apply_config(settings, row["channel"], config)
