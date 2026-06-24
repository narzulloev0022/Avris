"""create training_pairs table and add users.stt_consent

Continuous Learning Pipeline — Data Collector. See STT/Continuous-Learning-Pipeline.md.

Revision ID: 0001_training_pairs
Revises:
Create Date: 2026-06-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_training_pairs"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    insp = sa.inspect(bind)

    # On Postgres, mirror the canonical DDL: native UUID PK defaulting to
    # gen_random_uuid(). sa.Uuid renders as native UUID on PG and CHAR(32)
    # elsewhere (SQLite dev). The server_default is PG-only — on SQLite the
    # ORM's python-side uuid4 default supplies the id on insert.
    id_server_default = sa.text("gen_random_uuid()") if dialect == "postgresql" else None
    false_default = sa.text("false") if dialect == "postgresql" else sa.text("0")

    # Guard: create_all() may have already created the table in dev/prod startup.
    if "training_pairs" not in insp.get_table_names():
        op.create_table(
            "training_pairs",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, server_default=id_server_default),
            sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("audio_s3_path", sa.Text(), nullable=True),
            sa.Column("raw_transcript", sa.Text(), nullable=True),
            sa.Column("corrected_transcript", sa.Text(), nullable=True),
            sa.Column("language", sa.String(length=10), nullable=True),
            sa.Column("specialty", sa.Text(), nullable=True),
            sa.Column("consent", sa.Boolean(), nullable=False, server_default=false_default),
            sa.Column("phi_cleaned", sa.Boolean(), nullable=False, server_default=false_default),
            sa.Column("quality_score", sa.Float(), nullable=True),
            sa.Column("trained", sa.Boolean(), nullable=False, server_default=false_default),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_training_pairs_session_id", "training_pairs", ["session_id"])
        op.create_index("ix_training_pairs_trained", "training_pairs", ["trained"])

    # users.stt_consent — opt-in flag (default OFF) for the pipeline.
    user_cols = {c["name"] for c in insp.get_columns("users")} if "users" in insp.get_table_names() else set()
    if "users" in insp.get_table_names() and "stt_consent" not in user_cols:
        op.add_column(
            "users",
            sa.Column("stt_consent", sa.Boolean(), nullable=False, server_default=false_default),
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "users" in insp.get_table_names():
        user_cols = {c["name"] for c in insp.get_columns("users")}
        if "stt_consent" in user_cols:
            op.drop_column("users", "stt_consent")

    if "training_pairs" in insp.get_table_names():
        op.drop_index("ix_training_pairs_trained", table_name="training_pairs")
        op.drop_index("ix_training_pairs_session_id", table_name="training_pairs")
        op.drop_table("training_pairs")
