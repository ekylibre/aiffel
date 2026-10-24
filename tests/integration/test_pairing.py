"""Appairage (A3) de bout en bout : Telegram → page web → Ekylibre (factice) → ferme en base."""

import re
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.app.web_routes import CSRF_COOKIE, web_router
from aiffel.channels.base import InboundKind, InboundMessage
from aiffel.channels.dispatcher import Caller
from aiffel.channels.handlers import RECONNECT, Handlers
from aiffel.channels.identity import IdentityService
from aiffel.connectors.ekylibre.auth import EkylibreAuthError, EkylibreInstance
from aiffel.db.models import ChannelIdentity, Connection, Membership, SourceMap
from aiffel.db.session import farm_session, global_session
from aiffel.farm_context import FarmContextService
from aiffel.pairing import PAIRING_MAX_ATTEMPTS, PairingError, PairingResult, PairingService
from aiffel.vault import Keyring, SecretValue
from tests.fakes import FakeEkylibre

Sessions = async_sessionmaker[AsyncSession]
DEMO = EkylibreInstance("https://demo.ekylibre-dev.com", "demo")
DOMAINS = ("ekylibre-dev.com",)


@pytest.fixture
async def clean(app_sessions: Sessions, owner_sessions: Sessions) -> None:
    async with owner_sessions() as session, session.begin():
        await session.execute(
            text(
                "TRUNCATE aiffel.pairing_codes, aiffel.channel_identities, aiffel.connections, "
                "aiffel.memberships, aiffel.source_map, aiffel.secrets CASCADE"
            )
        )


@pytest.fixture
def fake() -> FakeEkylibre:
    return FakeEkylibre()


@pytest.fixture
async def pairing(
    app_sessions: Sessions, keyring: Keyring, fake: FakeEkylibre, clean: None
) -> AsyncIterator[PairingService]:
    async with fake.client() as http:
        yield PairingService(app_sessions, keyring, http, timedelta(minutes=15))


async def _identity(sessions: Sessions, keyring: Keyring, chat: str) -> uuid.UUID:
    service = IdentityService(SecretValue("sel"), keyring)
    async with global_session(sessions) as session:
        return (await service.resolve(session, "telegram", chat)).id


async def test_owner_then_worker_join_the_same_farm(
    pairing: PairingService, app_sessions: Sessions, keyring: Keyring
) -> None:
    chef = await _identity(app_sessions, keyring, "111")
    owner = await pairing.complete(
        await pairing.create_code(chef), DEMO, "chef@demo.fr", SecretValue("motdepasse-chef")
    )
    assert owner.farm_created and owner.role == "owner" and owner.farm_name == "Demo"

    salarie = await _identity(app_sessions, keyring, "222")
    worker = await pairing.complete(
        await pairing.create_code(salarie), DEMO, "sal@demo.fr", SecretValue("motdepasse-sal")
    )
    assert worker.farm_id == owner.farm_id and not worker.farm_created
    assert worker.role == "worker" and worker.user_id != owner.user_id

    async with farm_session(app_sessions, owner.farm_id) as session:
        members = {
            m.user_id: (m.role, m.can_validate) for m in await session.scalars(select(Membership))
        }
        sources = {
            s.data_type: (s.can_read, s.can_write) for s in await session.scalars(select(SourceMap))
        }
        connections = (await session.scalars(select(Connection))).all()
    assert members == {owner.user_id: ("owner", True), worker.user_id: ("worker", False)}
    assert sources["interventions"] == (True, True)
    assert {c.external_account_id for c in connections} == {"1", "2"}

    async with global_session(app_sessions) as session:
        identity = await session.get(ChannelIdentity, chef)
    assert identity is not None and identity.user_id == owner.user_id


async def test_password_is_never_stored(
    pairing: PairingService, app_sessions: Sessions, owner_sessions: Sessions, keyring: Keyring
) -> None:
    identity = await _identity(app_sessions, keyring, "333")
    result = await pairing.complete(
        await pairing.create_code(identity), DEMO, "chef@demo.fr", SecretValue("motdepasse-chef")
    )
    async with owner_sessions() as session:
        tables = (
            await session.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'aiffel'")
            )
        ).all()
        parts = []
        for table in tables:
            parts.append(
                await session.scalar(
                    text(f"SELECT coalesce(string_agg(t::text, ' '), '') FROM aiffel.{table} t")
                )
            )
        dump = " ".join(parts)
    assert "motdepasse-chef" not in dump
    assert "TOK-CHEF" not in dump  # le jeton n'est stocké que chiffré
    assert "chef@demo.fr" not in dump  # l'email aussi
    assert result.farm_created


async def test_code_is_single_use_and_bounded(
    pairing: PairingService, app_sessions: Sessions, keyring: Keyring
) -> None:
    identity = await _identity(app_sessions, keyring, "444")
    code = await pairing.create_code(identity)
    await pairing.complete(code, DEMO, "chef@demo.fr", SecretValue("motdepasse-chef"))
    with pytest.raises(PairingError):
        await pairing.complete(code, DEMO, "chef@demo.fr", SecretValue("motdepasse-chef"))

    code = await pairing.create_code(identity)
    for _ in range(PAIRING_MAX_ATTEMPTS):
        with pytest.raises(EkylibreAuthError):
            await pairing.complete(code, DEMO, "chef@demo.fr", SecretValue("faux"))
        await pairing.record_failure(code)
    with pytest.raises(PairingError):
        await pairing.check_code(code)

    first = await pairing.create_code(identity)
    await pairing.create_code(identity)  # un nouveau code annule le précédent
    with pytest.raises(PairingError):
        await pairing.check_code(first)


async def test_expired_code(
    app_sessions: Sessions, keyring: Keyring, fake: FakeEkylibre, clean: None
) -> None:
    async with fake.client() as http:
        service = PairingService(app_sessions, keyring, http, timedelta(seconds=-1))
        code = await service.create_code(await _identity(app_sessions, keyring, "555"))
        with pytest.raises(PairingError):
            await service.check_code(code)


async def test_commands_and_reconnection(
    pairing: PairingService, app_sessions: Sessions, keyring: Keyring, fake: FakeEkylibre
) -> None:
    identity = await _identity(app_sessions, keyring, "666")
    result = await pairing.complete(
        await pairing.create_code(identity), DEMO, "chef@demo.fr", SecretValue("motdepasse-chef")
    )
    handlers = Handlers(
        pairing, FarmContextService(app_sessions, keyring, pairing.http), "https://app.aiffel.org"
    )
    caller = Caller(identity_id=identity, user_id=result.user_id)

    def command(name: str) -> InboundMessage:
        return InboundMessage(
            channel="telegram",
            external_user_id="666",
            external_chat_id="666",
            kind=InboundKind.COMMAND,
            command=name,
        )

    [summary] = await handlers.paired(caller, command("ferme"))
    assert "Ferme Demo (exploitant)" in summary.text
    assert "• Grands Champs" in summary.text and "Source : Ekylibre" in summary.text

    fake.revoked.add("TOK-CHEF")  # déconnexion depuis le mobile : jeton partagé remis à zéro
    [refused] = await handlers.paired(caller, command("ferme"))
    assert refused.text == RECONNECT
    async with farm_session(app_sessions, result.farm_id) as session:
        connection = (await session.scalars(select(Connection))).one()
    assert connection.status == "needs_reconnect"

    [invite] = await handlers.paired(caller, command("start"))
    assert invite.buttons and invite.buttons[0][0].url
    code = invite.buttons[0][0].url.split("c=", 1)[1]
    fake.revoked.clear()
    again = await pairing.complete(code, DEMO, "chef@demo.fr", SecretValue("motdepasse-chef"))
    assert again.user_id == result.user_id and again.farm_id == result.farm_id
    async with farm_session(app_sessions, result.farm_id) as session:
        assert (await session.scalars(select(Connection))).one().status == "active"


# --- Page web ---


@pytest.fixture
async def web(pairing: PairingService) -> AsyncIterator[tuple[AsyncClient, list[PairingResult]]]:
    notified: list[PairingResult] = []

    async def on_paired(result: PairingResult) -> None:
        notified.append(result)

    app = FastAPI()
    app.include_router(web_router(pairing, DOMAINS, on_paired, secure_cookies=False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, notified


async def _form(client: AsyncClient, code: str) -> str:
    page = await client.get("/connect", params={"c": code})
    assert page.status_code == 200
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match
    return match.group(1)


async def test_web_pairing_flow(
    web: tuple[AsyncClient, list[PairingResult]],
    pairing: PairingService,
    app_sessions: Sessions,
    keyring: Keyring,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, notified = web
    code = await pairing.create_code(await _identity(app_sessions, keyring, "777"))
    csrf = await _form(client, code)
    form = {"c": code, "csrf": csrf, "instance": "demo.ekylibre-dev.com", "email": "chef@demo.fr"}

    wrong = await client.post("/connect", data={**form, "password": "faux"})
    assert wrong.status_code == 401 and "incorrect" in wrong.text
    assert "faux" not in wrong.text  # le mot de passe n'est jamais réaffiché

    csrf = re.search(r'name="csrf" value="([^"]+)"', wrong.text).group(1)  # type: ignore[union-attr]
    done = await client.post("/connect", data={**form, "csrf": csrf, "password": "motdepasse-chef"})
    assert done.status_code == 200 and "Demo" in done.text
    assert len(notified) == 1 and notified[0].role == "owner"
    assert "motdepasse-chef" not in capsys.readouterr().out

    reused = await client.get("/connect", params={"c": code})
    assert reused.status_code == 410


async def test_web_rejects_bad_csrf_and_foreign_instance(
    web: tuple[AsyncClient, list[PairingResult]],
    pairing: PairingService,
    app_sessions: Sessions,
    keyring: Keyring,
    fake: FakeEkylibre,
) -> None:
    client, notified = web
    code = await pairing.create_code(await _identity(app_sessions, keyring, "888"))
    csrf = await _form(client, code)

    client.cookies.set(CSRF_COOKIE, "autre", path="/connect")
    forged = await client.post(
        "/connect",
        data={
            "c": code,
            "csrf": csrf,
            "instance": "demo.ekylibre-dev.com",
            "email": "x",
            "password": "y",
        },
    )
    assert forged.status_code == 400

    client.cookies.clear()
    csrf = await _form(client, code)
    ssrf = await client.post(
        "/connect",
        data={
            "c": code,
            "csrf": csrf,
            "instance": "169.254.169.254",
            "email": "x",
            "password": "y",
        },
    )
    assert ssrf.status_code == 422
    assert not fake.requests and not notified  # aucun appel sortant n'a été fait


async def test_web_unknown_code(web: tuple[AsyncClient, list[PairingResult]]) -> None:
    client, _ = web
    response = await client.get("/connect", params={"c": "inconnu"})
    assert response.status_code == 410 and "/start" in response.text
