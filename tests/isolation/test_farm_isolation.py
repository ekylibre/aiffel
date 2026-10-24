"""Tests d'isolation entre fermes (ADR-01) : **bloquants en CI**.

Chaque table isolée reçoit une ligne pour la ferme A et une pour la ferme B. Le rôle applicatif,
placé dans le contexte de A, ne doit jamais voir, modifier ni créer de ligne de B, même par du
SQL brut sans filtre.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiffel.db import audit
from aiffel.db.models import (
    FARM_SCOPED_TABLES,
    Connection,
    Conversation,
    FarmMemory,
    Membership,
    Message,
    PendingAction,
    SourceMap,
    UsageMetric,
    User,
)
from aiffel.db.session import farm_session, global_session, make_sessionmaker
from aiffel.vault import SecretStore, SecretValue, VaultError
from aiffel.vault.crypto import Keyring
from tests.conftest import Database, create_farm

Sessions = async_sessionmaker[AsyncSession]


async def _populate(sessions: Sessions, keyring: Keyring, farm_id: uuid.UUID) -> uuid.UUID:
    """Une ligne dans chaque table isolée ; renvoie l'identifiant de la connexion créée."""
    async with global_session(sessions) as session:
        user = User(display_name="Exploitant")
        session.add(user)
        await session.flush()
        user_id = user.id

    async with farm_session(sessions, farm_id) as session:
        session.add(Membership(farm_id=farm_id, user_id=user_id, role="owner", can_validate=True))
        connection = Connection(
            farm_id=farm_id,
            user_id=user_id,
            connector="ekylibre",
            base_url="https://demo.ekylibre-dev.com",
            tenant="demo",
        )
        session.add(connection)
        await session.flush()
        await SecretStore(keyring).put(
            session, farm_id, connection.id, "simple_token", SecretValue(f"tok-{farm_id}")
        )
        session.add(
            SourceMap(farm_id=farm_id, data_type="interventions", connection_id=connection.id)
        )
        conversation = Conversation(farm_id=farm_id, user_id=user_id, channel="telegram")
        session.add(conversation)
        await session.flush()
        session.add(
            Message(
                farm_id=farm_id, conversation_id=conversation.id, role="user", content="bonjour"
            )
        )
        session.add(
            PendingAction(
                farm_id=farm_id,
                proposed_by=user_id,
                connector="ekylibre",
                kind="intervention",
                payload={},
                summary="Traitement",
                payload_hash="0" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        session.add(FarmMemory(farm_id=farm_id, kind="alias", content={"Grands Champs": "P12"}))
        session.add(UsageMetric(farm_id=farm_id, day=date.today(), model="test"))
        await audit.append(session, farm_id, "farm.created", actor_user_id=user_id)
        return connection.id


@pytest.fixture
async def two_farms(app_sessions: Sessions, keyring: Keyring) -> dict[str, uuid.UUID]:
    farm_a = await create_farm(app_sessions, keyring, "A")
    farm_b = await create_farm(app_sessions, keyring, "B")
    conn_a = await _populate(app_sessions, keyring, farm_a)
    conn_b = await _populate(app_sessions, keyring, farm_b)
    return {"a": farm_a, "b": farm_b, "conn_a": conn_a, "conn_b": conn_b}


@pytest.mark.parametrize("table", FARM_SCOPED_TABLES)
async def test_farm_only_sees_its_own_rows(
    app_sessions: Sessions, two_farms: dict[str, uuid.UUID], table: str
) -> None:
    async with farm_session(app_sessions, two_farms["a"]) as session:
        farm_ids = set(
            (await session.execute(text(f"SELECT farm_id FROM aiffel.{table}"))).scalars()
        )
    assert farm_ids == {two_farms["a"]}


async def test_farms_table_is_isolated(
    app_sessions: Sessions, two_farms: dict[str, uuid.UUID]
) -> None:
    async with farm_session(app_sessions, two_farms["a"]) as session:
        ids = set((await session.execute(text("SELECT id FROM aiffel.farms"))).scalars())
    assert ids == {two_farms["a"]}


@pytest.mark.parametrize("table", ("farms", *FARM_SCOPED_TABLES))
async def test_no_farm_context_sees_nothing(
    app_sessions: Sessions, two_farms: dict[str, uuid.UUID], table: str
) -> None:
    async with global_session(app_sessions) as session:
        count = await session.scalar(text(f"SELECT count(*) FROM aiffel.{table}"))
    assert count == 0


async def test_cannot_insert_into_another_farm(
    app_sessions: Sessions, two_farms: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with farm_session(app_sessions, two_farms["a"]) as session:
            session.add(FarmMemory(farm_id=two_farms["b"], kind="alias", content={"x": "y"}))
            await session.flush()


async def test_cannot_update_or_delete_another_farm(
    app_sessions: Sessions, two_farms: dict[str, uuid.UUID]
) -> None:
    async with farm_session(app_sessions, two_farms["a"]) as session:
        updated = await session.execute(
            text("UPDATE aiffel.farm_memory SET source = 'pirate' WHERE farm_id = :b"),
            {"b": two_farms["b"]},
        )
        deleted = await session.execute(
            text("DELETE FROM aiffel.connections WHERE farm_id = :b"), {"b": two_farms["b"]}
        )
    assert updated.rowcount == 0
    assert deleted.rowcount == 0


async def test_cannot_move_a_row_to_another_farm(
    app_sessions: Sessions, two_farms: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with farm_session(app_sessions, two_farms["a"]) as session:
            await session.execute(
                text("UPDATE aiffel.farm_memory SET farm_id = :b"), {"b": two_farms["b"]}
            )


async def test_cannot_read_another_farm_secret(
    app_sessions: Sessions, keyring: Keyring, two_farms: dict[str, uuid.UUID]
) -> None:
    store = SecretStore(keyring)
    async with farm_session(app_sessions, two_farms["a"]) as session:
        assert (await store.get(session, two_farms["a"], two_farms["conn_a"], "simple_token")) == (
            SecretValue(f"tok-{two_farms['a']}")
        )
        # La ligne de B est invisible : rien à déchiffrer.
        assert await store.get(session, two_farms["a"], two_farms["conn_b"], "simple_token") is None
        # Même en annonçant la ferme B, sa ligne reste invisible depuis le contexte A.
        assert await store.get(session, two_farms["b"], two_farms["conn_b"], "simple_token") is None
        # Et un secret de A ne se déchiffre pas en prétendant qu'il appartient à B.
        with pytest.raises(VaultError):
            await store.get(session, two_farms["b"], two_farms["conn_a"], "simple_token")


async def test_farm_context_does_not_leak_through_the_pool(
    database: Database, two_farms: dict[str, uuid.UUID]
) -> None:
    engine = create_async_engine(database.app_url, pool_size=1, max_overflow=0)
    sessions = make_sessionmaker(engine)
    try:
        async with farm_session(sessions, two_farms["a"]) as session:
            await session.execute(text("SELECT 1"))
        async with global_session(sessions) as session:
            assert await session.scalar(text("SELECT count(*) FROM aiffel.connections")) == 0
    finally:
        await engine.dispose()


async def test_farms_of_user_is_the_only_cross_farm_read(
    app_sessions: Sessions, two_farms: dict[str, uuid.UUID]
) -> None:
    async with farm_session(app_sessions, two_farms["a"]) as session:
        user_a = await session.scalar(text("SELECT user_id FROM aiffel.memberships"))
    async with global_session(app_sessions) as session:
        rows = (
            await session.execute(text("SELECT * FROM aiffel.farms_of_user(:u)"), {"u": user_a})
        ).all()
    assert [row.farm_id for row in rows] == [two_farms["a"]]
    assert rows[0].role == "owner"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE aiffel.audit_log SET action = 'falsifié'",
        "DELETE FROM aiffel.audit_log",
        "TRUNCATE aiffel.audit_log",
    ],
)
async def test_audit_log_is_append_only(
    app_sessions: Sessions, two_farms: dict[str, uuid.UUID], statement: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with farm_session(app_sessions, two_farms["a"]) as session:
            await session.execute(text(statement))


async def test_audit_chain_links_and_detects_tampering(
    app_sessions: Sessions, owner_sessions: Sessions, two_farms: dict[str, uuid.UUID]
) -> None:
    farm = two_farms["a"]
    async with farm_session(app_sessions, farm) as session:
        for i in range(3):
            await audit.append(session, farm, "intervention.executed", details={"n": i})
    async with farm_session(app_sessions, farm) as session:
        check = await audit.verify_chain(session, farm)
    assert check.ok and check.rows == 4

    # Une altération directe en base (seul le propriétaire le peut) est détectée.
    async with owner_sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE aiffel.audit_log SET details = '{\"n\": 99}' WHERE id = "
                "(SELECT min(id) FROM aiffel.audit_log WHERE farm_id = :f "
                "AND action = 'intervention.executed')"
            ),
            {"f": farm},
        )
    async with farm_session(app_sessions, farm) as session:
        assert not (await audit.verify_chain(session, farm)).ok


async def test_audit_hash_cannot_be_forged_by_the_app(
    app_sessions: Sessions, two_farms: dict[str, uuid.UUID]
) -> None:
    farm = two_farms["a"]
    async with farm_session(app_sessions, farm) as session:
        await session.execute(
            text(
                "INSERT INTO aiffel.audit_log (farm_id, action, details, prev_hash, hash) "
                "VALUES (:f, 'x', '{}', 'faux', 'faux')"
            ),
            {"f": farm},
        )
    async with farm_session(app_sessions, farm) as session:
        assert (await audit.verify_chain(session, farm)).ok
