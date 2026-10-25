"""Mémoire de la ferme (F-MEM-1, F-MEM-2) : alias de parcelles proposés par l'agent, retenus
seulement après confirmation d'un utilisateur de la ferme, consultables et effaçables par
l'exploitant.

Une proposition reste sans effet tant qu'elle n'est pas confirmée : l'agent ne lit que les faits
confirmés (``confirmed_at``). Les boutons de confirmation sont traités hors modèle.
"""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.db import audit
from aiffel.db.models import FarmMemory, Membership
from aiffel.db.session import farm_session

CALLBACK_PREFIX = "mem"
ALIAS = "alias"
SOURCE = "agent"


class FarmMemoryError(Exception):
    """Message destiné à l'utilisateur."""


@dataclass(frozen=True)
class AliasProposal:
    id: uuid.UUID
    alias: str
    target: str


@dataclass(frozen=True)
class Fact:
    id: uuid.UUID
    alias: str
    target: str


def _key(text: str) -> str:
    """Forme de comparaison : sans casse, accents ni ponctuation (« Bas-champ » = « bas champ »)."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in letters).split())


def _pair(row: FarmMemory) -> tuple[str, str]:
    (alias, target), *_ = row.content.items()
    return str(alias), str(target)


@dataclass
class MemoryService:
    sessions: async_sessionmaker[AsyncSession]

    async def propose_alias(
        self, farm_id: uuid.UUID, alias: str, target: str
    ) -> AliasProposal | None:
        """Proposition en attente ; ``None`` s'il n'y a rien à retenir (alias déjà retenu, ou nom
        identique à celui d'Ekylibre)."""
        alias, target = " ".join(alias.split()), " ".join(target.split())
        if not alias or not target:
            raise FarmMemoryError("Alias ou parcelle manquant.")
        if _key(alias) == _key(target):
            return None
        async with farm_session(self.sessions, farm_id) as session:
            for row in await self._aliases(session, confirmed=True):
                known_alias, known_target = _pair(row)
                if _key(known_alias) == _key(alias) and _key(known_target) == _key(target):
                    return None
            row = FarmMemory(farm_id=farm_id, kind=ALIAS, content={alias: target}, source=SOURCE)
            session.add(row)
            await session.flush()
            return AliasProposal(row.id, alias, target)

    async def confirm(self, farm_id: uuid.UUID, user_id: uuid.UUID, memory_id: uuid.UUID) -> str:
        async with farm_session(self.sessions, farm_id) as session:
            row = await session.get(FarmMemory, memory_id, with_for_update=True)
            if row is None or row.kind != ALIAS:
                raise FarmMemoryError("Cette proposition n'existe plus.")
            alias, target = _pair(row)
            if row.confirmed_at is not None:
                return f"C'est déjà retenu : « {alias} » = {target}."
            # Un même nom usuel ne désigne qu'une parcelle : l'ancien alias est remplacé.
            for other in await self._aliases(session, confirmed=True):
                if other.id != row.id and _key(_pair(other)[0]) == _key(alias):
                    await session.delete(other)
            row.confirmed_by = user_id
            row.confirmed_at = datetime.now(UTC)
            await audit.append(session, farm_id, "memory.confirmed", actor_user_id=user_id)
            return f"C'est noté : « {alias} » désigne désormais {target}."

    async def reject(self, farm_id: uuid.UUID, memory_id: uuid.UUID) -> str:
        async with farm_session(self.sessions, farm_id) as session:
            row = await session.get(FarmMemory, memory_id, with_for_update=True)
            if row is not None and row.confirmed_at is None:
                await session.delete(row)
        return "D'accord, je ne le retiens pas."

    async def facts(self, farm_id: uuid.UUID) -> list[Fact]:
        async with farm_session(self.sessions, farm_id) as session:
            rows = await self._aliases(session, confirmed=True)
            return sorted(
                (Fact(row.id, *_pair(row)) for row in rows), key=lambda fact: _key(fact.alias)
            )

    async def forget(self, farm_id: uuid.UUID, user_id: uuid.UUID, memory_id: uuid.UUID) -> str:
        async with farm_session(self.sessions, farm_id) as session:
            role = await session.scalar(
                select(Membership.role).where(
                    Membership.farm_id == farm_id, Membership.user_id == user_id
                )
            )
            if role != "owner":
                raise FarmMemoryError("Seul l'exploitant peut effacer ce que je retiens.")
            row = await session.get(FarmMemory, memory_id, with_for_update=True)
            if row is None or row.kind != ALIAS or row.confirmed_at is None:
                raise FarmMemoryError("Ce fait n'existe plus.")
            alias, target = _pair(row)
            await session.delete(row)
            await audit.append(session, farm_id, "memory.forgotten", actor_user_id=user_id)
            return f"J'ai oublié « {alias} » = {target}."

    @staticmethod
    async def _aliases(session: AsyncSession, *, confirmed: bool) -> list[FarmMemory]:
        query = select(FarmMemory).where(FarmMemory.kind == ALIAS)
        query = query.where(
            FarmMemory.confirmed_at.is_not(None) if confirmed else FarmMemory.confirmed_at.is_(None)
        )
        return list(await session.scalars(query))
