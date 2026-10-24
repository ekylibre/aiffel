"""Appairage : essais par code, compte externe des connexions, recherche de ferme par instance

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pairing_codes",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        schema="aiffel",
    )
    op.add_column(
        "connections",
        sa.Column("external_account_id", sa.String(length=100), nullable=True),
        schema="aiffel",
    )
    # Seconde lecture inter-fermes : retrouver la ferme déjà raccordée à une instance logicielle
    # (même serveur, même tenant), pour qu'un salarié rejoigne la ferme de l'exploitant.
    # Elle ne renvoie qu'un identifiant de ferme.
    op.execute(
        """
        CREATE FUNCTION aiffel.farm_of_instance(p_connector varchar, p_base_url varchar, p_tenant varchar)
        RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = aiffel, pg_temp AS $$
          SELECT farm_id FROM aiffel.connections
          WHERE connector = p_connector AND base_url = p_base_url
            AND tenant IS NOT DISTINCT FROM p_tenant
          ORDER BY created_at LIMIT 1
        $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION aiffel.farm_of_instance(varchar, varchar, varchar) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION aiffel.farm_of_instance(varchar, varchar, varchar) TO aiffel_app"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS aiffel.farm_of_instance(varchar, varchar, varchar)")
    op.drop_column("connections", "external_account_id", schema="aiffel")
    op.drop_column("pairing_codes", "attempts", schema="aiffel")
