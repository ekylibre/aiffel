"""La mesure de consommation quitte le cœur : usage_metrics est confiée aux extensions

Le cœur transmet la consommation de chaque tour aux extensions (``UsageEvent``) ; celle qui la
mesure crée sa propre table dans ses migrations. Les données existantes sont perdues.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CURRENT_FARM = "NULLIF(current_setting('aiffel.farm_id', true), '')::uuid"


def upgrade() -> None:
    op.drop_table("usage_metrics", schema="aiffel")


def downgrade() -> None:
    op.create_table(
        "usage_metrics",
        sa.Column("farm_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("stt_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("platform_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["aiffel.farms.id"],
            name=op.f("fk_usage_metrics_farm_id_farms"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("farm_id", "day", "model", name=op.f("pk_usage_metrics")),
        schema="aiffel",
    )
    op.execute("ALTER TABLE aiffel.usage_metrics ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY farm_isolation ON aiffel.usage_metrics "
        f"USING (farm_id = {_CURRENT_FARM}) WITH CHECK (farm_id = {_CURRENT_FARM})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON aiffel.usage_metrics TO aiffel_app")
