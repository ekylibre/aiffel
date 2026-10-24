"""Environnement Alembic : migrations exécutées par le propriétaire du schéma ``aiffel``."""

import asyncio

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from aiffel.db.models import SCHEMA, Base
from aiffel.settings import get_settings

target_metadata = Base.metadata


def _url() -> str:
    override = context.config.attributes.get("url")
    if override:
        return str(override)
    settings = get_settings()
    secret = settings.migration_database_url or settings.database_url
    if secret is None:
        raise RuntimeError("AIFFEL_MIGRATION_DATABASE_URL ou AIFFEL_DATABASE_URL est requis")
    return secret.get_secret_value()


def _include_name(name: str | None, type_: str, _parent_names: object) -> bool:
    # Alembic ne gère que le schéma aiffel (ni public, ni sa propre table de version).
    if type_ == "schema":
        return name == SCHEMA
    return not (type_ == "table" and name == "alembic_version")


def _run(connection: Connection) -> None:
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


asyncio.run(_main())
