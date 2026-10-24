"""``SecretStore`` : lecture et écriture des secrets d'une ferme (ADR-06).

Les méthodes prennent une session ouverte par :func:`aiffel.db.session.farm_session` : la RLS
garantit qu'on ne lit que la clé et les secrets de la ferme courante. Seuls les connecteurs
appellent ``get``, au moment de l'appel externe ; la valeur ne doit jamais rejoindre un prompt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiffel.db.models import Farm, Secret
from aiffel.vault.crypto import (
    Keyring,
    SecretValue,
    VaultError,
    decrypt_secret,
    encrypt_secret,
    secret_aad,
    unwrap_data_key,
)


class SecretStore:
    def __init__(self, keyring: Keyring) -> None:
        self._keyring = keyring

    async def _data_key(self, session: AsyncSession, farm_id: uuid.UUID) -> bytes:
        wrapped = await session.scalar(select(Farm.data_key_enc).where(Farm.id == farm_id))
        if wrapped is None:
            raise VaultError("ferme introuvable dans le contexte courant")
        return unwrap_data_key(self._keyring, farm_id, wrapped)

    async def _row(
        self, session: AsyncSession, connection_id: uuid.UUID, kind: str
    ) -> Secret | None:
        return await session.scalar(
            select(Secret).where(Secret.connection_id == connection_id, Secret.kind == kind)
        )

    async def put(
        self,
        session: AsyncSession,
        farm_id: uuid.UUID,
        connection_id: uuid.UUID,
        kind: str,
        value: SecretValue,
    ) -> None:
        """Enregistre ou remplace un secret (le remplacement compte comme une rotation)."""
        ciphertext, nonce = encrypt_secret(
            await self._data_key(session, farm_id), secret_aad(farm_id, connection_id, kind), value
        )
        row = await self._row(session, connection_id, kind)
        if row is None:
            session.add(
                Secret(
                    farm_id=farm_id,
                    connection_id=connection_id,
                    kind=kind,
                    ciphertext=ciphertext,
                    nonce=nonce,
                )
            )
        else:
            row.ciphertext, row.nonce = ciphertext, nonce
            row.rotated_at, row.revoked_at = datetime.now(UTC), None
        await session.flush()

    async def get(
        self, session: AsyncSession, farm_id: uuid.UUID, connection_id: uuid.UUID, kind: str
    ) -> SecretValue | None:
        """Renvoie le secret, ou ``None`` s'il n'existe pas ou a été révoqué."""
        row = await self._row(session, connection_id, kind)
        if row is None or row.revoked_at is not None:
            return None
        return decrypt_secret(
            await self._data_key(session, farm_id),
            secret_aad(farm_id, connection_id, kind),
            row.ciphertext,
            row.nonce,
        )

    async def rotate(
        self,
        session: AsyncSession,
        farm_id: uuid.UUID,
        connection_id: uuid.UUID,
        kind: str,
        value: SecretValue,
    ) -> None:
        await self.put(session, farm_id, connection_id, kind, value)

    async def revoke(self, session: AsyncSession, connection_id: uuid.UUID, kind: str) -> None:
        """Efface le chiffré et garde la ligne datée (trace de la révocation)."""
        row = await self._row(session, connection_id, kind)
        if row is not None:
            row.ciphertext, row.nonce, row.revoked_at = b"", b"", datetime.now(UTC)
            await session.flush()
