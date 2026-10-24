"""Fixtures partagées : Postgres jetable (testcontainers), migré, avec le rôle applicatif réel."""

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.db.models import Farm
from aiffel.db.session import farm_session, make_engine, make_sessionmaker
from aiffel.vault.crypto import Keyring, generate_key, new_wrapped_data_key

APP_PASSWORD = "app-test-password"


@dataclass(frozen=True)
class Database:
    owner_url: str
    app_url: str


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return False
    return True


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    if not _docker_available():
        pytest.skip("Docker indisponible : tests Postgres ignorés")
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        owner_url = pg.get_connection_url()
        config = Config("alembic.ini")
        config.attributes["url"] = owner_url
        command.upgrade(config, "head")

        sync_engine = create_engine(owner_url)
        with sync_engine.begin() as conn:
            conn.execute(text(f"ALTER ROLE aiffel_app LOGIN PASSWORD '{APP_PASSWORD}'"))
        sync_engine.dispose()

        host, port = pg.get_container_host_ip(), pg.get_exposed_port(5432)
        app_url = f"postgresql+psycopg://aiffel_app:{APP_PASSWORD}@{host}:{port}/{pg.dbname}"
        yield Database(owner_url=owner_url, app_url=app_url)


@pytest.fixture
async def app_sessions(database: Database) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions de l'application (rôle aiffel_app, soumis à la RLS)."""
    engine = make_engine(database.app_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def owner_sessions(database: Database) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions du propriétaire du schéma : seulement pour préparer ou altérer les données."""
    engine = make_engine(database.owner_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture(scope="session")
def keyring() -> Keyring:
    return Keyring.parse(generate_key())


async def create_farm(
    sessions: async_sessionmaker[AsyncSession], keyring: Keyring, name: str = "Ferme"
) -> uuid.UUID:
    farm_id = uuid.uuid4()
    async with farm_session(sessions, farm_id) as session:
        session.add(
            Farm(id=farm_id, name=name, data_key_enc=new_wrapped_data_key(keyring, farm_id))
        )
    return farm_id
