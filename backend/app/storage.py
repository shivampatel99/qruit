from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.models import (
    ApprovalToken,
    AuditRecord,
    Candidate,
    Role,
    RoleStatus,
)

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS roles (
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
    """,
    """
    CREATE TABLE IF NOT EXISTS candidates (
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
    """,
    """
    CREATE TABLE IF NOT EXISTS approval_tokens (
        token TEXT PRIMARY KEY,
        role_id TEXT NOT NULL,
        checkpoint_type TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        resolved_at TEXT,
        resolution TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_records (
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
    """,
    """
    CREATE TABLE IF NOT EXISTS dedup_index (
        dedup_key TEXT PRIMARY KEY,
        role_id TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inbound_queue (
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
    """,
    """
    CREATE TABLE IF NOT EXISTS whatsapp_windows (
        phone TEXT PRIMARY KEY,
        last_inbound_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS channel_accounts (
        channel TEXT NOT NULL,
        account_key TEXT NOT NULL,
        encrypted_token TEXT NOT NULL DEFAULT '',
        granted_scopes TEXT NOT NULL DEFAULT '[]',
        account_label TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (channel, account_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_pending (
        state TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        code_verifier TEXT NOT NULL,
        redirect_uri TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS public_files (
        token TEXT PRIMARY KEY,
        local_path TEXT NOT NULL,
        content_type TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS connector_configs (
        channel TEXT PRIMARY KEY,
        encrypted_config TEXT NOT NULL DEFAULT '',
        notify_email TEXT NOT NULL DEFAULT '',
        last_reauth_alert_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )
    """,
]


def _json_dumps(value: Any) -> str:
    return json.dumps(value)


def _json_loads(value: str) -> Any:
    return json.loads(value)


class Storage:
    """Postgres-backed persistence (SQLAlchemy Core, async, pooled).

    Hand-written SQL rather than an ORM — same style the sync sqlite3/Postgres
    versions this replaced had, just async now so a DB round-trip never blocks
    the event loop underneath an async connector/pipeline call.

    `__init__` only builds the engine (cheap, no I/O); call `await
    storage.init_schema()` once before first use (app startup, or the test
    `container` fixture) to run the CREATE TABLE IF NOT EXISTS statements —
    Alembic (alembic/versions/) is the versioned, reversible source of truth
    for production deploys, this is a dev/test convenience that's a no-op
    once those tables already exist.
    """

    def __init__(self, database_url: str):
        self.engine: AsyncEngine = create_async_engine(
            database_url, pool_size=10, max_overflow=20, pool_pre_ping=True,
        )

    async def init_schema(self) -> None:
        async with self.engine.begin() as conn:
            for statement in SCHEMA_STATEMENTS:
                await conn.execute(text(statement))

    # -- roles ---------------------------------------------------------

    async def upsert_role(self, role: Role) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO roles (id, title, client_contact, notify_email, sla_hours,
                        status, jd_data, jd_file_token, screening_weights, notify_all_candidates,
                        created_at, updated_at)
                    VALUES (:id, :title, :client_contact, :notify_email, :sla_hours,
                        :status, :jd_data, :jd_file_token, :screening_weights, :notify_all_candidates,
                        :created_at, :updated_at)
                    ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title, client_contact=excluded.client_contact,
                        notify_email=excluded.notify_email, sla_hours=excluded.sla_hours,
                        status=excluded.status, jd_data=excluded.jd_data,
                        jd_file_token=excluded.jd_file_token,
                        screening_weights=excluded.screening_weights,
                        notify_all_candidates=excluded.notify_all_candidates,
                        updated_at=excluded.updated_at
                    """
                ),
                {
                    "id": role.id, "title": role.title, "client_contact": role.client_contact,
                    "notify_email": role.notify_email, "sla_hours": role.sla_hours,
                    "status": role.status.value, "jd_data": _json_dumps(role.jd_data),
                    "jd_file_token": role.jd_file_token,
                    "screening_weights": _json_dumps(role.screening_weights),
                    "notify_all_candidates": int(role.notify_all_candidates),
                    "created_at": role.created_at.isoformat(), "updated_at": role.updated_at.isoformat(),
                },
            )

    async def get_role(self, role_id: str) -> Role | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(text("SELECT * FROM roles WHERE id = :id"), {"id": role_id})
            row = result.mappings().fetchone()
        return self._row_to_role(row) if row else None

    async def list_roles(self) -> list[Role]:
        async with self.engine.begin() as conn:
            result = await conn.execute(text("SELECT * FROM roles ORDER BY created_at DESC"))
            rows = result.mappings().fetchall()
        return [self._row_to_role(row) for row in rows]

    async def compare_and_swap_role_status(
        self, role_id: str, expected_status: RoleStatus, new_status: RoleStatus,
    ) -> bool:
        """Atomic guard against concurrent triggers of the same role (double-
        click "approve", two workers picking up the same job): only the
        caller that actually flips the row may proceed."""
        from app.models import utcnow

        async with self.engine.begin() as conn:
            result = await conn.execute(
                text(
                    "UPDATE roles SET status = :new_status, updated_at = :updated_at "
                    "WHERE id = :id AND status = :expected_status RETURNING id"
                ),
                {
                    "new_status": new_status.value, "expected_status": expected_status.value,
                    "id": role_id, "updated_at": utcnow().isoformat(),
                },
            )
            return result.fetchone() is not None

    async def get_gap_fields_requested(self, role_id: str) -> list[str]:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT gap_fields_requested FROM roles WHERE id = :id"), {"id": role_id}
            )
            row = result.mappings().fetchone()
        return _json_loads(row["gap_fields_requested"]) if row else []

    async def set_gap_fields_requested(self, role_id: str, fields: list[str]) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text("UPDATE roles SET gap_fields_requested = :fields WHERE id = :id"),
                {"fields": _json_dumps(fields), "id": role_id},
            )

    def _row_to_role(self, row: RowMapping) -> Role:
        return Role(
            id=row["id"], title=row["title"], client_contact=row["client_contact"],
            notify_email=row["notify_email"], sla_hours=row["sla_hours"],
            status=RoleStatus(row["status"]), jd_data=_json_loads(row["jd_data"]),
            jd_file_token=row["jd_file_token"],
            screening_weights=_json_loads(row["screening_weights"]),
            notify_all_candidates=bool(row["notify_all_candidates"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # -- candidates ------------------------------------------------------

    async def upsert_candidate(self, candidate: Candidate, email_missing_flag: bool = False) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO candidates (id, role_id, name, email, phone, cv_ref,
                        extracted, score, shortlisted, interview_session_id,
                        interview_status, skip_interview, deep_screen_file_token,
                        email_missing_flag, created_at)
                    VALUES (:id, :role_id, :name, :email, :phone, :cv_ref,
                        :extracted, :score, :shortlisted, :interview_session_id,
                        :interview_status, :skip_interview, :deep_screen_file_token,
                        :email_missing_flag, :created_at)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, email=excluded.email, phone=excluded.phone,
                        cv_ref=excluded.cv_ref, extracted=excluded.extracted,
                        score=excluded.score, shortlisted=excluded.shortlisted,
                        interview_session_id=excluded.interview_session_id,
                        interview_status=excluded.interview_status,
                        skip_interview=excluded.skip_interview,
                        deep_screen_file_token=excluded.deep_screen_file_token,
                        email_missing_flag=excluded.email_missing_flag
                    """
                ),
                {
                    "id": candidate.id, "role_id": candidate.role_id, "name": candidate.name,
                    "email": candidate.email, "phone": candidate.phone, "cv_ref": candidate.cv_ref,
                    "extracted": _json_dumps(candidate.extracted), "score": candidate.score,
                    "shortlisted": int(candidate.shortlisted),
                    "interview_session_id": candidate.interview_session_id,
                    "interview_status": candidate.interview_status,
                    "skip_interview": int(candidate.skip_interview),
                    "deep_screen_file_token": candidate.deep_screen_file_token,
                    "email_missing_flag": int(email_missing_flag),
                    "created_at": candidate.created_at.isoformat(),
                },
            )

    async def list_candidates(self, role_id: str) -> list[Candidate]:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM candidates WHERE role_id = :role_id"), {"role_id": role_id}
            )
            rows = result.mappings().fetchall()
        return [self._row_to_candidate(row) for row in rows]

    def _row_to_candidate(self, row: RowMapping) -> Candidate:
        return Candidate(
            id=row["id"], role_id=row["role_id"], name=row["name"], email=row["email"],
            phone=row["phone"], cv_ref=row["cv_ref"], extracted=_json_loads(row["extracted"]),
            score=row["score"], shortlisted=bool(row["shortlisted"]),
            interview_session_id=row["interview_session_id"],
            interview_status=row["interview_status"],
            skip_interview=bool(row["skip_interview"]),
            deep_screen_file_token=row["deep_screen_file_token"], created_at=row["created_at"],
        )

    # -- approval tokens ---------------------------------------------------

    async def save_approval_token(self, token: ApprovalToken) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO approval_tokens (token, role_id, checkpoint_type, issued_at,
                        resolved, resolved_at, resolution)
                    VALUES (:token, :role_id, :checkpoint_type, :issued_at, :resolved, :resolved_at, :resolution)
                    ON CONFLICT(token) DO UPDATE SET
                        resolved=excluded.resolved, resolved_at=excluded.resolved_at,
                        resolution=excluded.resolution
                    """
                ),
                {
                    "token": token.token, "role_id": token.role_id, "checkpoint_type": token.checkpoint_type,
                    "issued_at": token.issued_at.isoformat(), "resolved": int(token.resolved),
                    "resolved_at": token.resolved_at.isoformat() if token.resolved_at else None,
                    "resolution": token.resolution,
                },
            )

    async def get_approval_token(self, token: str) -> ApprovalToken | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM approval_tokens WHERE token = :token"), {"token": token}
            )
            row = result.mappings().fetchone()
        if not row:
            return None
        return ApprovalToken(
            token=row["token"], role_id=row["role_id"],
            checkpoint_type=row["checkpoint_type"], issued_at=row["issued_at"],
            resolved=bool(row["resolved"]), resolved_at=row["resolved_at"],
            resolution=row["resolution"],
        )

    async def latest_unresolved_token(self, role_id: str, checkpoint_type: str) -> ApprovalToken | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT * FROM approval_tokens
                    WHERE role_id = :role_id AND checkpoint_type = :checkpoint_type AND resolved = 0
                    ORDER BY issued_at DESC LIMIT 1
                    """
                ),
                {"role_id": role_id, "checkpoint_type": checkpoint_type},
            )
            row = result.mappings().fetchone()
        if not row:
            return None
        return ApprovalToken(
            token=row["token"], role_id=row["role_id"],
            checkpoint_type=row["checkpoint_type"], issued_at=row["issued_at"],
            resolved=bool(row["resolved"]),
        )

    async def resolve_all_unresolved(self, role_id: str, checkpoint_type: str, resolution: str) -> bool:
        """Returns whether this call actually resolved a (still-open)
        checkpoint. Two concurrent replies to the same checkpoint must not
        both get True — only the one that really flips it should trigger
        `_apply()` (fixes the double-apply race in ApprovalPipeline)."""
        from app.models import utcnow

        async with self.engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    UPDATE approval_tokens SET resolved = 1, resolved_at = :resolved_at, resolution = :resolution
                    WHERE role_id = :role_id AND checkpoint_type = :checkpoint_type AND resolved = 0
                    RETURNING token
                    """
                ),
                {
                    "resolved_at": utcnow().isoformat(), "resolution": resolution,
                    "role_id": role_id, "checkpoint_type": checkpoint_type,
                },
            )
            return result.fetchone() is not None

    # -- audit -------------------------------------------------------------

    async def record_audit(self, record: AuditRecord) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO audit_records (id, role_id, channel, alert_type, recipient,
                        status, provider_message_id, error, created_at)
                    VALUES (:id, :role_id, :channel, :alert_type, :recipient, :status,
                        :provider_message_id, :error, :created_at)
                    """
                ),
                {
                    "id": record.id, "role_id": record.role_id, "channel": record.channel,
                    "alert_type": record.alert_type, "recipient": record.recipient, "status": record.status,
                    "provider_message_id": record.provider_message_id, "error": record.error,
                    "created_at": record.created_at.isoformat(),
                },
            )

    async def list_audit(self, role_id: str | None = None) -> list[AuditRecord]:
        async with self.engine.begin() as conn:
            if role_id:
                result = await conn.execute(
                    text("SELECT * FROM audit_records WHERE role_id = :role_id ORDER BY created_at DESC"),
                    {"role_id": role_id},
                )
            else:
                result = await conn.execute(text("SELECT * FROM audit_records ORDER BY created_at DESC"))
            rows = result.mappings().fetchall()
        return [
            AuditRecord(
                id=r["id"], role_id=r["role_id"], channel=r["channel"],
                alert_type=r["alert_type"], recipient=r["recipient"], status=r["status"],
                provider_message_id=r["provider_message_id"], error=r["error"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # -- dedup ---------------------------------------------------------------

    async def claim_dedup_key(self, dedup_key: str, role_id: str, kind: str) -> bool:
        """Atomically claims a dedup key. Returns True the first time (the
        caller should proceed), False if another request already claimed it
        (the caller must skip) — one round-trip, no separate seen()+mark()
        steps, so two concurrent pulls can never both pass the check."""
        from app.models import utcnow

        async with self.engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    INSERT INTO dedup_index (dedup_key, role_id, kind, created_at)
                    VALUES (:dedup_key, :role_id, :kind, :created_at)
                    ON CONFLICT (dedup_key) DO NOTHING
                    RETURNING dedup_key
                    """
                ),
                {"dedup_key": dedup_key, "role_id": role_id, "kind": kind, "created_at": utcnow().isoformat()},
            )
            return result.fetchone() is not None

    async def dedup_seen(self, dedup_key: str) -> bool:
        """Read-only check, kept for call sites that just want to know
        (not claim) — claim_dedup_key is the concurrency-safe primitive."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM dedup_index WHERE dedup_key = :dedup_key"), {"dedup_key": dedup_key}
            )
            return result.fetchone() is not None

    # -- inbound queue (fed by the WhatsApp webhook; drained by pull()) -------

    async def enqueue_inbound(
        self, source: str, item_id: str, sender: str, subject: str, body_text: str,
        has_attachment: bool, received_at_iso: str,
    ) -> str:
        row_id = uuid.uuid4().hex
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO inbound_queue (id, source, item_id, sender, subject, body_text,
                        has_attachment, received_at, consumed)
                    VALUES (:id, :source, :item_id, :sender, :subject, :body_text,
                        :has_attachment, :received_at, 0)
                    """
                ),
                {
                    "id": row_id, "source": source, "item_id": item_id, "sender": sender,
                    "subject": subject, "body_text": body_text, "has_attachment": int(has_attachment),
                    "received_at": received_at_iso,
                },
            )
        return row_id

    async def drain_inbound(self, source: str, limit: int = 25) -> list[RowMapping]:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT * FROM inbound_queue WHERE source = :source AND consumed = 0
                    ORDER BY received_at ASC LIMIT :limit
                    """
                ),
                {"source": source, "limit": limit},
            )
            rows = result.mappings().fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                await conn.execute(
                    text("UPDATE inbound_queue SET consumed = 1 WHERE id = ANY(:ids)"), {"ids": ids}
                )
        return rows

    # -- whatsapp 24h conversation window ------------------------------------

    async def record_whatsapp_inbound(self, phone: str, at_iso: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO whatsapp_windows (phone, last_inbound_at) VALUES (:phone, :at_iso)
                    ON CONFLICT(phone) DO UPDATE SET last_inbound_at = excluded.last_inbound_at
                    """
                ),
                {"phone": phone, "at_iso": at_iso},
            )

    async def whatsapp_last_inbound_at(self, phone: str) -> str | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT last_inbound_at FROM whatsapp_windows WHERE phone = :phone"), {"phone": phone}
            )
            row = result.mappings().fetchone()
        return row["last_inbound_at"] if row else None

    # -- channel accounts (encrypted OAuth tokens) ----------------------------

    async def save_channel_account(
        self, channel: str, account_key: str, encrypted_token: str,
        granted_scopes: list[str], account_label: str,
    ) -> None:
        from app.models import utcnow

        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO channel_accounts (channel, account_key, encrypted_token,
                        granted_scopes, account_label, created_at)
                    VALUES (:channel, :account_key, :encrypted_token, :granted_scopes, :account_label, :created_at)
                    ON CONFLICT(channel, account_key) DO UPDATE SET
                        encrypted_token=excluded.encrypted_token,
                        granted_scopes=excluded.granted_scopes,
                        account_label=excluded.account_label
                    """
                ),
                {
                    "channel": channel, "account_key": account_key, "encrypted_token": encrypted_token,
                    "granted_scopes": _json_dumps(granted_scopes), "account_label": account_label,
                    "created_at": utcnow().isoformat(),
                },
            )

    async def delete_channel_account(self, channel: str, account_key: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM channel_accounts WHERE channel = :channel AND account_key = :account_key"),
                {"channel": channel, "account_key": account_key},
            )

    async def get_channel_account(self, channel: str, account_key: str) -> dict | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM channel_accounts WHERE channel = :channel AND account_key = :account_key"),
                {"channel": channel, "account_key": account_key},
            )
            row = result.mappings().fetchone()
        if not row:
            return None
        return {
            "channel": row["channel"], "account_key": row["account_key"],
            "encrypted_token": row["encrypted_token"],
            "granted_scopes": _json_loads(row["granted_scopes"]),
            "account_label": row["account_label"],
        }

    # -- connector configs (client credentials submitted via the API,
    # distinct from channel_accounts which stores the resulting OAuth token) -

    async def save_connector_config(
        self, channel: str, encrypted_config: str, notify_email: str = "",
    ) -> None:
        from app.models import utcnow

        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO connector_configs (channel, encrypted_config, notify_email, updated_at)
                    VALUES (:channel, :encrypted_config, :notify_email, :updated_at)
                    ON CONFLICT(channel) DO UPDATE SET
                        encrypted_config=excluded.encrypted_config,
                        notify_email=excluded.notify_email,
                        updated_at=excluded.updated_at
                    """
                ),
                {
                    "channel": channel, "encrypted_config": encrypted_config,
                    "notify_email": notify_email, "updated_at": utcnow().isoformat(),
                },
            )

    async def get_connector_config(self, channel: str) -> dict | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM connector_configs WHERE channel = :channel"), {"channel": channel}
            )
            row = result.mappings().fetchone()
        return dict(row) if row else None

    async def list_connector_configs(self) -> list[dict]:
        async with self.engine.begin() as conn:
            result = await conn.execute(text("SELECT * FROM connector_configs"))
            rows = result.mappings().fetchall()
        return [dict(row) for row in rows]

    async def delete_connector_config(self, channel: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text("DELETE FROM connector_configs WHERE channel = :channel"), {"channel": channel})

    async def mark_reauth_alert_sent(self, channel: str) -> None:
        from app.models import utcnow

        async with self.engine.begin() as conn:
            await conn.execute(
                text("UPDATE connector_configs SET last_reauth_alert_at = :at WHERE channel = :channel"),
                {"at": utcnow().isoformat(), "channel": channel},
            )

    # -- OAuth CSRF state (issued by /connect, consumed once by /callback) ---

    async def save_oauth_pending(self, state: str, channel: str, code_verifier: str, redirect_uri: str) -> None:
        from app.models import utcnow

        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO oauth_pending (state, channel, code_verifier, redirect_uri, created_at)
                    VALUES (:state, :channel, :code_verifier, :redirect_uri, :created_at)
                    """
                ),
                {
                    "state": state, "channel": channel, "code_verifier": code_verifier,
                    "redirect_uri": redirect_uri, "created_at": utcnow().isoformat(),
                },
            )

    async def pop_oauth_pending(self, state: str) -> dict | None:
        """Single-use: a state that doesn't match anything issued (or is
        replayed) returns None so the caller never exchanges a code for it
        (AU-4 — CSRF protection on the OAuth callback)."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM oauth_pending WHERE state = :state RETURNING channel, code_verifier, redirect_uri"),
                {"state": state},
            )
            row = result.mappings().fetchone()
            if row is None:
                return None
        return {"channel": row["channel"], "code_verifier": row["code_verifier"], "redirect_uri": row["redirect_uri"]}

    # -- public file hosting (Reqruit.ai fetches JD/CV/deep-screen by token) --

    async def register_public_file(self, local_path: str, content_type: str = "") -> str:
        from app.models import utcnow

        token = uuid.uuid4().hex
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO public_files (token, local_path, content_type, created_at)
                    VALUES (:token, :local_path, :content_type, :created_at)
                    """
                ),
                {"token": token, "local_path": local_path, "content_type": content_type, "created_at": utcnow().isoformat()},
            )
        return token

    async def get_public_file(self, token: str) -> dict | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("SELECT * FROM public_files WHERE token = :token"), {"token": token}
            )
            row = result.mappings().fetchone()
        if not row:
            return None
        return {"local_path": row["local_path"], "content_type": row["content_type"]}
