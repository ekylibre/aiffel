"""Contexte de ferme d'un utilisateur appairé : sa ferme, son rôle et son client Ekylibre."""

import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.connectors.ekylibre.auth import (
    EkylibreCredentials,
    EkylibreInstance,
    SimpleTokenAuth,
)
from aiffel.connectors.ekylibre.client import EkylibreClient
from aiffel.db import audit
from aiffel.db.models import Connection
from aiffel.db.session import farm_session, global_session
from aiffel.pairing import CONNECTOR, SECRET_EMAIL, SECRET_TOKEN
from aiffel.vault import Keyring, SecretStore


@dataclass(frozen=True)
class FarmRef:
    farm_id: uuid.UUID
    name: str
    role: str
    can_validate: bool


@dataclass(frozen=True)
class EkylibreAccess:
    connection_id: uuid.UUID
    client: EkylibreClient | None  # None : connexion à refaire


@dataclass
class FarmContextService:
    sessions: async_sessionmaker[AsyncSession]
    keyring: Keyring
    http: httpx.AsyncClient

    async def farm_of(self, user_id: uuid.UUID) -> FarmRef | None:
        """Ferme de l'utilisateur (phase 1 : la plus ancienne s'il en a plusieurs)."""
        async with global_session(self.sessions) as session:
            row = (
                await session.execute(
                    text("SELECT * FROM aiffel.farms_of_user(:u) LIMIT 1"), {"u": user_id}
                )
            ).first()
        if row is None:
            return None
        return FarmRef(row.farm_id, row.farm_name, row.role, row.can_validate)

    async def ekylibre(self, farm: FarmRef, user_id: uuid.UUID) -> EkylibreAccess | None:
        """Client Ekylibre de l'utilisateur ; le jeton est déchiffré ici, jamais ailleurs."""
        async with farm_session(self.sessions, farm.farm_id) as session:
            connection = await session.scalar(
                select(Connection).where(
                    Connection.connector == CONNECTOR, Connection.user_id == user_id
                )
            )
            if connection is None:
                return None
            if connection.status != "active":
                return EkylibreAccess(connection.id, None)
            store = SecretStore(self.keyring)
            token = await store.get(session, farm.farm_id, connection.id, SECRET_TOKEN)
            email = await store.get(session, farm.farm_id, connection.id, SECRET_EMAIL)
            if token is None or email is None or connection.tenant is None:
                return EkylibreAccess(connection.id, None)
            instance = EkylibreInstance(base_url=connection.base_url, tenant=connection.tenant)
            client = EkylibreClient(
                self.http, SimpleTokenAuth(), instance, EkylibreCredentials(email, token)
            )
            return EkylibreAccess(connection.id, client)

    async def mark_needs_reconnect(
        self, farm: FarmRef, connection_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        async with farm_session(self.sessions, farm.farm_id) as session:
            connection = await session.get(Connection, connection_id)
            if connection is not None and connection.status == "active":
                connection.status = "needs_reconnect"
                await audit.append(
                    session,
                    farm.farm_id,
                    "connection.needs_reconnect",
                    actor_user_id=user_id,
                    target=f"{CONNECTOR}:{connection.tenant}",
                )
