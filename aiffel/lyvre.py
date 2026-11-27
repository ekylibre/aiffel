"""Raccordement d'une ferme à Lyvre (comptabilité, paie) en lecture seule (T4, ADR-10).

- Une connexion par ferme (``connections.connector = 'lyvre'``), enregistrée par un exploitant
  depuis la page ``/lyvre`` : adresse de l'installation et clé ``lyv_…`` créée dans Lyvre.
- La clé est vérifiée chez Lyvre, puis chiffrée dans le coffre ; ``scopes`` garde les domaines
  qu'elle lit (relevés à la connexion) et ``tenant`` le nom de la structure.
- Les outils Lyvre ne sont proposés qu'aux exploitants : les données comptables ne sont pas
  ouvertes aux salariés.
- Une clé refusée pendant un tour (401) passe la connexion en ``needs_reconnect``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.connectors.lyvre.client import LyvreAccount, LyvreClient, LyvreError, parse_base_url
from aiffel.db import audit
from aiffel.db.models import Connection
from aiffel.db.session import farm_session
from aiffel.vault import Keyring, SecretStore, SecretValue

CONNECTOR = "lyvre"
SECRET_API_KEY = "lyvre_api_key"  # noqa: S105 (nom du type de secret)
KEY_PREFIX = "lyv_"


class LyvreConnectError(Exception):
    """Message destiné à l'exploitant, sans la clé."""


@dataclass(frozen=True)
class LyvreStatus:
    connection_id: uuid.UUID
    status: str  # active | needs_reconnect | revoked
    base_url: str
    structure: str | None
    reads: frozenset[str]


@dataclass(frozen=True)
class LyvreAccess:
    connection_id: uuid.UUID
    client: LyvreClient
    structure: str | None
    reads: frozenset[str]


@dataclass
class LyvreService:
    sessions: async_sessionmaker[AsyncSession]
    keyring: Keyring
    http: httpx.AsyncClient
    allowed_domains: tuple[str, ...]

    async def connect(
        self, farm_id: uuid.UUID, user_id: uuid.UUID, base_url: str, key: SecretValue
    ) -> LyvreAccount:
        """Vérifie la clé chez Lyvre puis l'enregistre ; remplace une connexion existante."""
        try:
            url = parse_base_url(base_url, self.allowed_domains)
        except LyvreError as exc:
            raise LyvreConnectError(str(exc)) from None
        if not key.reveal().startswith(KEY_PREFIX) or len(key.reveal()) < 20:
            raise LyvreConnectError("Ce n'est pas une clé Lyvre (elle commence par « lyv_ »).")
        try:
            account = await LyvreClient(self.http, url, key).account()
        except LyvreError as exc:
            raise LyvreConnectError(str(exc)) from None
        if not account.reads:
            raise LyvreConnectError(
                "Cette clé ne lit aucun domaine de Lyvre : cochez au moins la lecture de la "
                "Comptabilité dans Lyvre (Configuration, puis Accès API)."
            )
        async with farm_session(self.sessions, farm_id) as session:
            connection = await self._connection(session)
            if connection is None:
                connection = Connection(farm_id=farm_id, user_id=user_id, connector=CONNECTOR)
                session.add(connection)
            connection.user_id, connection.base_url = user_id, url
            connection.tenant, connection.scopes = account.structure, sorted(account.reads)
            connection.status = "active"
            await session.flush()
            store = SecretStore(self.keyring)
            await store.put(session, farm_id, connection.id, SECRET_API_KEY, key)
            await audit.append(
                session,
                farm_id,
                "connection.created",
                actor_user_id=user_id,
                target=f"{CONNECTOR}:{account.structure}",
                details={"reads": sorted(account.reads), "writes_ignored": bool(account.writes)},
            )
        return account

    async def status(self, farm_id: uuid.UUID) -> LyvreStatus | None:
        async with farm_session(self.sessions, farm_id) as session:
            connection = await self._connection(session)
        if connection is None:
            return None
        return LyvreStatus(
            connection.id,
            connection.status,
            connection.base_url,
            connection.tenant,
            frozenset(connection.scopes or ()),
        )

    async def access(self, farm_id: uuid.UUID) -> LyvreAccess | None:
        """Client Lyvre de la ferme si la connexion est active ; la clé est déchiffrée ici."""
        async with farm_session(self.sessions, farm_id) as session:
            connection = await self._connection(session)
            if connection is None or connection.status != "active":
                return None
            key = await SecretStore(self.keyring).get(
                session, farm_id, connection.id, SECRET_API_KEY
            )
            if key is None:
                return None
            return LyvreAccess(
                connection.id,
                LyvreClient(self.http, connection.base_url, key),
                connection.tenant,
                frozenset(connection.scopes or ()),
            )

    async def disconnect(self, farm_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        async with farm_session(self.sessions, farm_id) as session:
            connection = await self._connection(session)
            if connection is None or connection.status == "revoked":
                return False
            await SecretStore(self.keyring).revoke(session, connection.id, SECRET_API_KEY)
            connection.status = "revoked"
            await audit.append(
                session,
                farm_id,
                "connection.revoked",
                actor_user_id=user_id,
                target=f"{CONNECTOR}:{connection.tenant}",
            )
        return True

    async def mark_needs_reconnect(
        self, farm_id: uuid.UUID, connection_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        async with farm_session(self.sessions, farm_id) as session:
            connection = await session.get(Connection, connection_id)
            if connection is not None and connection.status == "active":
                connection.status = "needs_reconnect"
                await audit.append(
                    session,
                    farm_id,
                    "connection.needs_reconnect",
                    actor_user_id=user_id,
                    target=f"{CONNECTOR}:{connection.tenant}",
                )

    @staticmethod
    async def _connection(session: AsyncSession) -> Connection | None:
        return cast(
            Connection | None,
            await session.scalar(select(Connection).where(Connection.connector == CONNECTOR)),
        )
