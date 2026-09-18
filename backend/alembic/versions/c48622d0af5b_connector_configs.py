"""connector configs

Revision ID: c48622d0af5b
Revises: b37511c9fe4f
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# Mirrors app/storage.py's SCHEMA_STATEMENTS — see b37511c9fe4f's note.

# revision identifiers, used by Alembic.
revision: str = 'c48622d0af5b'
down_revision: Union[str, None] = 'b37511c9fe4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE connector_configs (
            channel TEXT PRIMARY KEY,
            encrypted_config TEXT NOT NULL DEFAULT '',
            notify_email TEXT NOT NULL DEFAULT '',
            last_reauth_alert_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_configs")
