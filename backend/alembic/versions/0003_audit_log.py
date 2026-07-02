"""create audit_log table — append-only who-did-what trail (PHI-free meta)

Revision ID: 0003_audit_log
Revises: 0002_auth_codes
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_audit_log"
down_revision: Union[str, Sequence[str], None] = "0002_auth_codes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Guard: create_all() may have already created the table in dev/prod startup.
    if "audit_log" not in insp.get_table_names():
        op.create_table(
            "audit_log",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("entity", sa.String(length=32), nullable=False),
            sa.Column("entity_id", sa.String(length=64), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
        op.create_index("ix_audit_log_entity", "audit_log", ["entity"])
        op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("audit_log")
