"""initial schema

Revision ID: b37511c9fe4f
Revises: 
Create Date: 2026-09-06 14:31:50.793066

"""
from typing import Sequence, Union

from alembic import op

# Mirrors app/storage.py's SCHEMA_STATEMENTS exactly — that module still runs
# CREATE TABLE IF NOT EXISTS itself as a dev/test convenience (harmless once
# these tables already exist), but this migration is the versioned, reversible
# source of truth for production deploys.

# revision identifiers, used by Alembic.
revision: str = 'b37511c9fe4f'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "roles", "candidates", "approval_tokens", "audit_records", "dedup_index",
    "inbound_queue", "whatsapp_windows", "channel_accounts", "oauth_pending", "public_files",
]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE roles (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            client_contact TEXT NOT NULL DEFAULT '',
            notify_email TEXT NOT NULL DEFAULT '',
            sla_hours INTEGER NOT NULL DEFAULT 48,
            status TEXT NOT NULL DEFAULT 'jd_intake',
            jd_data TEXT NOT NULL DEFAULT '{}',
            jd_file_token TEXT NOT NULL DEFAULT '',
            screening_weights TEXT NOT NULL DEFAULT '{}',
            notify_all_candidates INTEGER NOT NULL DEFAULT 0,
            gap_fields_requested TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE candidates (
            id TEXT PRIMARY KEY,
            role_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            cv_ref TEXT NOT NULL DEFAULT '',
            extracted TEXT NOT NULL DEFAULT '{}',
            score REAL,
            shortlisted INTEGER NOT NULL DEFAULT 0,
            interview_session_id TEXT NOT NULL DEFAULT '',
            interview_status TEXT NOT NULL DEFAULT '',
            skip_interview INTEGER NOT NULL DEFAULT 0,
            deep_screen_file_token TEXT NOT NULL DEFAULT '',
            email_missing_flag INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE approval_tokens (
            token TEXT PRIMARY KEY,
            role_id TEXT NOT NULL,
            checkpoint_type TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            resolved_at TEXT,
            resolution TEXT NOT NULL DEFAULT ''
        )
    """)
    op.execute("""
        CREATE TABLE audit_records (
            id TEXT PRIMARY KEY,
            role_id TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL,
            alert_type TEXT NOT NULL DEFAULT '',
            recipient TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            provider_message_id TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE dedup_index (
            dedup_key TEXT PRIMARY KEY,
            role_id TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE inbound_queue (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            item_id TEXT NOT NULL,
            sender TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            body_text TEXT NOT NULL DEFAULT '',
            has_attachment INTEGER NOT NULL DEFAULT 0,
            received_at TEXT NOT NULL,
            consumed INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute("""
        CREATE TABLE whatsapp_windows (
            phone TEXT PRIMARY KEY,
            last_inbound_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE channel_accounts (
            channel TEXT NOT NULL,
            account_key TEXT NOT NULL,
            encrypted_token TEXT NOT NULL DEFAULT '',
            granted_scopes TEXT NOT NULL DEFAULT '[]',
            account_label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (channel, account_key)
        )
    """)
    op.execute("""
        CREATE TABLE oauth_pending (
            state TEXT PRIMARY KEY,
            channel TEXT NOT NULL,
            code_verifier TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE public_files (
            token TEXT PRIMARY KEY,
            local_path TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
