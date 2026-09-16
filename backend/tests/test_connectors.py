from __future__ import annotations

import time

from app.connectors.gdrive import GoogleDriveChannel
from app.connectors.gmail import SCOPES as GMAIL_SCOPES
from app.connectors.gmail import GmailChannel
from app.connectors.oauth_base import TokenBlob
from app.connectors.outlook import SCOPES as OUTLOOK_SCOPES
from app.connectors.sharepoint import SCOPES as SHAREPOINT_SCOPES
from app.crypto import ApprovalTokenSigner, TokenCipher
from app.models import AuthState, OutboundMessage

WRITE_KEYWORDS = ("write", "modify", ".delete", "full_control", "readwrite")


def make_gmail(settings, storage, google_client_id="", google_client_secret="", email_mock=True):
    settings.google_client_id = google_client_id
    settings.google_client_secret = google_client_secret
    settings.qruit_email_mock = email_mock
    cipher = TokenCipher(settings)
    signer = ApprovalTokenSigner(settings)
    return GmailChannel(settings, storage, cipher, signer), signer


async def test_cn1_no_credentials_returns_error(settings, container):
    settings.google_client_id = ""
    settings.google_client_secret = ""
    drive = GoogleDriveChannel(settings, container.storage, container.cipher)
    status = await drive.auth_status()
    assert status.state == AuthState.ERROR
    assert "not configured" in status.reason


async def test_cn2_missing_scope_reports_needs_auth(settings, container):
    connector, _ = make_gmail(settings, container.storage, "client-id", "client-secret", email_mock=False)
    await connector.store_token(
        TokenBlob(
            access_token="tok", refresh_token="rtok", expires_at=time.time() + 3600,
            scopes=["openid", "email", "https://www.googleapis.com/auth/gmail.readonly"],
            account_label="recruiting@clientco.example",
        )
    )
    status = await connector.auth_status()
    assert status.state == AuthState.NEEDS_AUTH
    assert "gmail.send" in status.reason


async def test_cn3_fully_authorized_returns_ready(settings, container):
    connector, _ = make_gmail(settings, container.storage, "client-id", "client-secret", email_mock=False)
    await connector.store_token(
        TokenBlob(
            access_token="tok", refresh_token="rtok", expires_at=time.time() + 3600,
            scopes=GMAIL_SCOPES, account_label="recruiting@clientco.example",
        )
    )
    status = await connector.auth_status()
    assert status.state == AuthState.READY
    assert status.account_label == "recruiting@clientco.example"


async def test_cn4_pull_with_no_query_does_not_raise(container):
    connector = container.registry.get("gmail")
    items = await connector.pull()
    assert isinstance(items, list)
    assert len(items) >= 1


async def test_cn5_repeated_pull_does_not_duplicate_downstream(container, tmp_path):
    source_dir = tmp_path / "shared_folder"
    source_dir.mkdir()
    (source_dir / "candidate_cv.txt").write_text(
        "Jane Roe\nCurriculum Vitae\nProfessional Experience: 5 years Python.\njane.roe@example.com"
    )
    local = container.registry.get("local_folder")
    local.root = source_dir

    from app.models import Role

    role = Role(id="role-cn5", title="Test role")
    await container.storage.upsert_role(role)

    first = await container.ingestion.ingest_for_role(role, "local_folder")
    second = await container.ingestion.ingest_for_role(role, "local_folder")

    assert len(first.candidates_added) == 1
    assert len(second.candidates_added) == 0
    assert "candidate_cv.txt" in second.skipped_duplicates


async def test_cn7_browse_empty_folder_returns_zero_counts(container):
    local = container.registry.get("local_folder")
    result = await local.browse("")
    assert result.entries == []


async def test_cn8_send_without_approval_token_is_rejected(container):
    connector = container.registry.get("gmail")
    message = OutboundMessage(channel="gmail", recipient="client@example.com", subject="x", body_text="y")
    result = await connector.send(message)
    assert result.success is False
    assert "approval" in result.error


async def test_cn9_send_with_valid_token_succeeds(container):
    connector = container.registry.get("gmail")
    token = container.signer.issue("role-1", "status_update")
    message = OutboundMessage(
        channel="gmail", recipient="client@example.com", subject="x", body_text="y",
        approval_token=token, role_id="role-1",
    )
    result = await connector.send(message)
    assert result.success is True
    assert result.provider_message_id


def test_cn10_no_connector_requests_write_scopes():
    all_scopes = " ".join(GMAIL_SCOPES + OUTLOOK_SCOPES + SHAREPOINT_SCOPES).lower()
    for keyword in WRITE_KEYWORDS:
        assert keyword not in all_scopes, f"found disallowed scope keyword: {keyword}"
