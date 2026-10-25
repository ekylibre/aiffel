"""Modèle d'IA par ferme : objet des liens à usage unique, coût et jetons sur la clé d'Aiffel

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Un lien sert soit à l'appairage (« pair »), soit à saisir la clé d'IA de la ferme (« llm_key ») :
    # un code de l'un n'ouvre jamais la page de l'autre.
    op.add_column(
        "pairing_codes",
        sa.Column("purpose", sa.String(length=20), server_default="pair", nullable=False),
        schema="aiffel",
    )
    op.add_column(
        "usage_metrics",
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        schema="aiffel",
    )
    op.add_column(
        "usage_metrics",
        sa.Column("platform_tokens", sa.BigInteger(), server_default="0", nullable=False),
        schema="aiffel",
    )


def downgrade() -> None:
    op.drop_column("usage_metrics", "platform_tokens", schema="aiffel")
    op.drop_column("usage_metrics", "cost_usd", schema="aiffel")
    op.drop_column("pairing_codes", "purpose", schema="aiffel")
