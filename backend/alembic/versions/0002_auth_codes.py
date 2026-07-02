"""create auth_codes table — DB-backed OTP + OAuth state

Replaces the in-memory dicts in auth.py (_verify_codes, _reset_codes,
_oauth_states, _resend_cooldowns) so auth state survives restarts and works
across multiple instances.

Revision ID: 0002_auth_codes
Revises: 0001_training_pairs
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_auth_codes"
down_revision: Union[str, Sequence[str], None] = "0001_training_pairs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Guard: create_all() may have already created the table in dev/prod startup.
    if "auth_codes" not in insp.get_table_names():
        op.create_table(
            "auth_codes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("purpose", sa.String(length=16), nullable=False),
            sa.Column("key", sa.String(length=255), nullable=False),
            sa.Column("code_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("resend_after", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("purpose", "key", name="uq_auth_codes_purpose_key"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("auth_codes")
