"""Sessions de base de données avec contexte de ferme (ADR-01).

Toute lecture ou écriture de données d'une ferme passe par :func:`farm_session`, qui fixe
``aiffel.farm_id`` pour la transaction. Postgres (RLS) filtre alors chaque requête sur cette ferme.
Le paramètre est local à la transaction (``set_config(..., true)``) : il ne fuit pas d'une requête
à l'autre via le pool de connexions.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def farm_session(
    sessionmaker: async_sessionmaker[AsyncSession], farm_id: uuid.UUID
) -> AsyncIterator[AsyncSession]:
    """Ouvre une transaction limitée à une ferme ; commit à la sortie, rollback en cas d'erreur."""
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('aiffel.farm_id', :farm_id, true)"),
            {"farm_id": str(farm_id)},
        )
        yield session


@asynccontextmanager
async def global_session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Transaction sans ferme : seules les tables globales (utilisateurs, canaux, appairage) sont
    visibles. Les tables isolées par ferme y apparaissent vides."""
    async with sessionmaker() as session, session.begin():
        yield session
