import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.db.models import Connection, User
from aiffel.db.session import farm_session, global_session
from aiffel.vault import SecretStore, SecretValue
from aiffel.vault.crypto import Keyring
from tests.conftest import create_farm


async def _connection(sessions: async_sessionmaker[AsyncSession], farm_id: uuid.UUID) -> uuid.UUID:
    async with global_session(sessions) as session:
        user = User(display_name="U")
        session.add(user)
        await session.flush()
        user_id = user.id
    async with farm_session(sessions, farm_id) as session:
        connection = Connection(
            farm_id=farm_id, user_id=user_id, connector="ekylibre", base_url="https://x"
        )
        session.add(connection)
        await session.flush()
        return connection.id


async def test_put_get_rotate_revoke(
    app_sessions: async_sessionmaker[AsyncSession], keyring: Keyring
) -> None:
    store = SecretStore(keyring)
    farm = await create_farm(app_sessions, keyring)
    conn = await _connection(app_sessions, farm)

    async with farm_session(app_sessions, farm) as session:
        assert await store.get(session, farm, conn, "simple_token") is None
        await store.put(session, farm, conn, "simple_token", SecretValue("v1"))
    async with farm_session(app_sessions, farm) as session:
        assert (await store.get(session, farm, conn, "simple_token")) == SecretValue("v1")
        await store.rotate(session, farm, conn, "simple_token", SecretValue("v2"))
    async with farm_session(app_sessions, farm) as session:
        assert (await store.get(session, farm, conn, "simple_token")) == SecretValue("v2")
        await store.revoke(session, conn, "simple_token")
    async with farm_session(app_sessions, farm) as session:
        assert await store.get(session, farm, conn, "simple_token") is None


async def test_secret_is_not_stored_in_clear(
    app_sessions: async_sessionmaker[AsyncSession],
    owner_sessions: async_sessionmaker[AsyncSession],
    keyring: Keyring,
) -> None:
    farm = await create_farm(app_sessions, keyring)
    conn = await _connection(app_sessions, farm)
    async with farm_session(app_sessions, farm) as session:
        await SecretStore(keyring).put(
            session, farm, conn, "simple_token", SecretValue("TOPSECRET")
        )
    async with owner_sessions() as session:
        raw = await session.scalar(
            text("SELECT ciphertext FROM aiffel.secrets WHERE connection_id = :c"), {"c": conn}
        )
    assert raw is not None and b"TOPSECRET" not in raw
