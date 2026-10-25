"""Droits de validation (F-ECR-4) : l'exploitant décide quels salariés peuvent valider les
enregistrements dans Ekylibre.

Un salarié peut toujours proposer ; valider exige ``memberships.can_validate``, relu à chaque
validation par ``ActionService``. L'exploitant valide toujours. Les boutons sont traités hors
modèle.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.db import audit
from aiffel.db.models import Membership, User
from aiffel.db.session import farm_session

CALLBACK_PREFIX = "rights"


class RightsError(Exception):
    """Message destiné à l'utilisateur."""


@dataclass(frozen=True)
class Member:
    membership_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    role: str
    can_validate: bool


@dataclass
class RightsService:
    sessions: async_sessionmaker[AsyncSession]

    async def members(self, farm_id: uuid.UUID) -> list[Member]:
        async with farm_session(self.sessions, farm_id) as session:
            rows = await session.execute(
                select(Membership, User.display_name)
                .join(User, User.id == Membership.user_id)
                .order_by(Membership.role.desc(), User.display_name)
            )
            return [
                Member(m.id, m.user_id, name, m.role, m.role == "owner" or m.can_validate)
                for m, name in rows.all()
            ]

    async def toggle(
        self, farm_id: uuid.UUID, owner_id: uuid.UUID, membership_id: uuid.UUID
    ) -> str:
        """Accorde ou retire à un salarié le droit de valider ; réservé à l'exploitant."""
        async with farm_session(self.sessions, farm_id) as session:
            role = await session.scalar(
                select(Membership.role).where(
                    Membership.farm_id == farm_id, Membership.user_id == owner_id
                )
            )
            if role != "owner":
                raise RightsError("Seul l'exploitant de la ferme règle les droits de validation.")
            target = await session.get(Membership, membership_id, with_for_update=True)
            if target is None:
                raise RightsError("Ce membre n'existe plus.")
            if target.role == "owner":
                raise RightsError("L'exploitant valide toujours.")
            name = await session.scalar(select(User.display_name).where(User.id == target.user_id))
            target.can_validate = not target.can_validate
            await audit.append(
                session,
                farm_id,
                "membership.can_validate_changed",
                actor_user_id=owner_id,
                target=str(target.user_id),
                details={"can_validate": target.can_validate},
            )
            if target.can_validate:
                return f"{name} peut désormais valider les enregistrements dans Ekylibre."
            return f"{name} ne peut plus valider : ses propositions attendront votre validation."
