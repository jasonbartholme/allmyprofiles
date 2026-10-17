"""seed SMTP/email settings rows (Gmail SMTP epic)

The ``settings`` table is a key/value store, so no schema change is needed —
this migration only inserts the new email-delivery keys if they are missing
(idempotent). Existing rows are never overwritten, matching the append-only
guarantee used elsewhere in this project. Works on both SQLite and PostgreSQL.

Revision ID: 0003smtp_settings
Revises: 0002oauth
Create Date: 2026-10-17
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '0003smtp_settings'
down_revision = '0002oauth'
branch_labels = None
depends_on = None

# key -> default value (mirrors models.DEFAULT_SETTINGS; keep in sync).
SMTP_SETTINGS = [
    ('smtp_enabled', 'false'),
    ('smtp_host', ''),
    ('smtp_port', '465'),
    ('smtp_security', 'ssl'),
    ('smtp_username', ''),
    ('smtp_password', ''),
    ('mail_from', ''),
    ('mail_from_name', 'AllMyProfiles'),
]


def upgrade():
    bind = op.get_bind()
    for key, value in SMTP_SETTINGS:
        exists = bind.execute(
            "SELECT 1 FROM settings WHERE key = :k", {'k': key}).fetchone()
        if not exists:
            bind.execute(
                "INSERT INTO settings (key, value) VALUES (:k, :v)",
                {'k': key, 'v': value})


def downgrade():
    bind = op.get_bind()
    for key, _value in SMTP_SETTINGS:
        bind.execute("DELETE FROM settings WHERE key = :k", {'k': key})
