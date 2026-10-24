"""Journal d'audit en ajout seul et chaîné (NF-TRA, F-ECR-3).

L'empreinte de chaque ligne est calculée par Postgres (trigger ``audit_log_chain``) : l'application
ne peut ni la choisir, ni modifier ou supprimer une ligne (droits ``INSERT`` et ``SELECT``).
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aiffel.db.models import AuditLog


async def append(
    session: AsyncSession,
    farm_id: uuid.UUID,
    action: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            farm_id=farm_id,
            actor_user_id=actor_user_id,
            action=action,
            target=target,
            details=details or {},
        )
    )
    await session.flush()


@dataclass(frozen=True)
class ChainCheck:
    rows: int
    broken_ids: list[int]

    @property
    def ok(self) -> bool:
        return not self.broken_ids


_VERIFY_SQL = text(
    """
    SELECT id,
           hash = aiffel.audit_log_row_hash(prev_hash, farm_id, at, actor_user_id,
                                            action, target, details)
             AND prev_hash IS NOT DISTINCT FROM lag(hash) OVER (ORDER BY id) AS ok
    FROM aiffel.audit_log
    WHERE farm_id = :farm_id
    ORDER BY id
    """
)


async def verify_chain(session: AsyncSession, farm_id: uuid.UUID) -> ChainCheck:
    """Recalcule la chaîne d'une ferme : signale toute ligne modifiée, insérée ou supprimée."""
    result = (await session.execute(_VERIFY_SQL, {"farm_id": farm_id})).all()
    return ChainCheck(rows=len(result), broken_ids=[row.id for row in result if not row.ok])
