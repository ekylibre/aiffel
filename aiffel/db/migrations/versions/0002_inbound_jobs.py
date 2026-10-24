"""File des messages entrants et identifiant de canal chiffré

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "channel_identities",
        sa.Column("external_id_enc", sa.LargeBinary(), nullable=True),
        schema="aiffel",
    )
    op.create_table(
        "inbound_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("external_update_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed')",
            name=op.f("ck_inbound_jobs_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inbound_jobs")),
        sa.UniqueConstraint("channel", "external_update_id", name=op.f("uq_inbound_jobs_channel")),
        schema="aiffel",
    )
    op.create_index(
        op.f("ix_inbound_jobs_status"), "inbound_jobs", ["status"], unique=False, schema="aiffel"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON aiffel.inbound_jobs TO aiffel_app")
    op.execute("GRANT USAGE ON SEQUENCE aiffel.inbound_jobs_id_seq TO aiffel_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_inbound_jobs_status"), table_name="inbound_jobs", schema="aiffel")
    op.drop_table("inbound_jobs", schema="aiffel")
    op.drop_column("channel_identities", "external_id_enc", schema="aiffel")
