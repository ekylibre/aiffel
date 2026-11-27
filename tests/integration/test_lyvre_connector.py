"""Connecteur Lyvre (T4) : clé chiffrée, page /lyvre, menu /lyvre, tour d'agent, vrai Postgres."""

import re
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from aiffel.app.lyvre_routes import lyvre_router
from aiffel.channels.base import InboundKind
from aiffel.channels.handlers import Handlers
from aiffel.channels.lyvre_menu import OWNER_ONLY, LyvreMenu
from aiffel.db.models import AuditLog, Connection, Secret
from aiffel.db.session import farm_session
from aiffel.lyvre import LyvreConnectError, LyvreService
from aiffel.orchestrator.agent import LYVRE_RECONNECT
from aiffel.pairing import PURPOSE_LYVRE
from aiffel.vault import SecretValue
from tests.fakes import LYVRE_KEY, FakeLyvre
from tests.integration import test_farm_llm
from tests.integration.test_farm_llm import Env, _caller, _farm, _msg
from tests.integration.test_orchestrator import (
    ScriptedProvider,
    _orchestrator,
    final,
    tool_turn,
)

URL = "https://app.lyvre.org"
# Fixture ``env`` (exploitant et salarié d'une même ferme) partagée avec les tests de /modele.
env = test_farm_llm.env


def _service(env: Env, fake: FakeLyvre) -> LyvreService:
    return LyvreService(env.sessions, env.farms.keyring, fake.client(), ("lyvre.org",))


async def test_connect_stores_the_key_encrypted_with_its_domains(env: Env) -> None:
    fake = FakeLyvre(writes={"purchases"})
    lyvre = _service(env, fake)
    farm_id, user_id = env.owner.farm_id, env.owner.user_id
    account = await lyvre.connect(farm_id, user_id, "app.lyvre.org", SecretValue(LYVRE_KEY))
    assert account.structure == "GAEC des Essais" and account.writes == {"purchases"}

    async with farm_session(env.sessions, farm_id) as session:
        connection = (
            await session.scalars(select(Connection).where(Connection.connector == "lyvre"))
        ).one()
        assert connection.base_url == URL and connection.tenant == "GAEC des Essais"
        assert connection.scopes == ["accounting", "bank", "purchases"]
        secret = (
            await session.scalars(select(Secret).where(Secret.connection_id == connection.id))
        ).one()
        assert LYVRE_KEY.encode() not in secret.ciphertext
        audit = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "connection.created"))
        ).one()
        assert LYVRE_KEY not in str(audit.details) and audit.details["writes_ignored"]

    access = await lyvre.access(farm_id)
    assert access is not None and access.reads == {"accounting", "bank", "purchases"}
    balances = await access.client.call("get_balances", {"prefixes": "60111"})
    assert balances["accounts"][0]["number"] == "60111"

    assert await lyvre.disconnect(farm_id, user_id)
    assert await lyvre.access(farm_id) is None
    status = await lyvre.status(farm_id)
    assert status is not None and status.status == "revoked"


@pytest.mark.parametrize(
    ("url", "key", "fake", "message"),
    [
        (URL, "sk-pas-lyvre-0123456789", FakeLyvre(), "commence par « lyv_ »"),
        ("https://lyvre.evil.example", LYVRE_KEY, FakeLyvre(), "Adresse Lyvre non reconnue"),
        (URL, LYVRE_KEY, FakeLyvre(revoked=True), "/lyvre"),
        (URL, LYVRE_KEY, FakeLyvre(reads=set()), "ne lit aucun domaine"),
    ],
)
async def test_connect_refusals(
    env: Env, url: str, key: str, fake: FakeLyvre, message: str
) -> None:
    with pytest.raises(LyvreConnectError, match=message):
        await _service(env, fake).connect(
            env.owner.farm_id, env.owner.user_id, url, SecretValue(key)
        )
    assert await _service(env, fake).status(env.owner.farm_id) is None


def _handlers(env: Env, lyvre: LyvreService) -> Handlers:
    return Handlers(
        env.pairing,
        env.farms,
        "https://app.aiffel.org",
        lyvre=LyvreMenu(env.pairing, lyvre, "https://app.aiffel.org"),
    )


async def test_lyvre_menu_for_owner_and_worker(env: Env) -> None:
    lyvre = _service(env, FakeLyvre())
    handlers = _handlers(env, lyvre)
    command = _msg(kind=InboundKind.COMMAND, command="lyvre")
    [worker] = await handlers.paired(_caller(env.worker), command)
    assert worker.text == OWNER_ONLY and not worker.buttons

    [intro] = await handlers.paired(_caller(env.owner), command)
    assert "lecture seule" in intro.text
    url = intro.buttons[0][0].url or ""
    assert url.startswith("https://app.aiffel.org/lyvre?c=")
    code = url.split("c=", 1)[1]
    assert await env.pairing.check_code(code, PURPOSE_LYVRE) == env.owner.identity_id

    await lyvre.connect(env.owner.farm_id, env.owner.user_id, URL, SecretValue(LYVRE_KEY))
    [connected] = await handlers.paired(_caller(env.owner), command)
    assert "GAEC des Essais" in connected.text and "Achats" in connected.text
    assert connected.buttons[-1][0].data == "lyvre:off"
    [confirm] = await handlers.paired(
        _caller(env.owner), _msg(kind=InboundKind.CALLBACK, callback_data="lyvre:off")
    )
    assert confirm.buttons[0][0].data == "lyvre:off!"
    [refused] = await handlers.paired(
        _caller(env.worker), _msg(kind=InboundKind.CALLBACK, callback_data="lyvre:off!")
    )
    assert refused.text == OWNER_ONLY and await lyvre.access(env.owner.farm_id) is not None
    [done] = await handlers.paired(
        _caller(env.owner), _msg(kind=InboundKind.CALLBACK, callback_data="lyvre:off!")
    )
    assert "déconnecté" in done.text and await lyvre.access(env.owner.farm_id) is None


@pytest.fixture
async def web(env: Env) -> AsyncIterator[tuple[AsyncClient, LyvreService, FakeLyvre, list[str]]]:
    fake = FakeLyvre()
    lyvre = _service(env, fake)
    notified: list[str] = []

    async def notify(_identity: uuid.UUID, text_: str) -> None:
        notified.append(text_)

    app = FastAPI()
    app.include_router(
        lyvre_router(env.pairing, env.farms, lyvre, notify, URL, secure_cookies=False)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, lyvre, fake, notified


def _csrf(page: httpx.Response) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match
    return match.group(1)


async def test_lyvre_page_connects_once_and_hides_the_key(
    env: Env,
    web: tuple[AsyncClient, LyvreService, FakeLyvre, list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, lyvre, fake, notified = web
    code = await env.pairing.create_code(env.owner.identity_id, PURPOSE_LYVRE)
    page = await client.get("/lyvre", params={"c": code})
    assert page.status_code == 200 and f'value="{URL}"' in page.text
    assert page.headers["referrer-policy"] == "no-referrer"

    fake.revoked = True
    form = {"c": code, "url": URL, "key": LYVRE_KEY}
    refused = await client.post("/lyvre", data={**form, "csrf": _csrf(page)})
    assert refused.status_code == 422 and "refusée" in refused.text
    assert LYVRE_KEY not in refused.text

    fake.revoked = False
    done = await client.post("/lyvre", data={**form, "csrf": _csrf(refused)})
    assert done.status_code == 200 and "GAEC des Essais" in done.text
    assert notified and "Achats" in notified[0]
    assert await lyvre.access(env.owner.farm_id) is not None
    assert LYVRE_KEY not in capsys.readouterr().out
    assert (await client.get("/lyvre", params={"c": code})).status_code == 410


async def test_lyvre_page_refuses_workers_and_other_codes(
    env: Env, web: tuple[AsyncClient, LyvreService, FakeLyvre, list[str]]
) -> None:
    client, *_ = web
    worker_code = await env.pairing.create_code(env.worker.identity_id, PURPOSE_LYVRE)
    page = await client.get("/lyvre", params={"c": worker_code})
    assert page.status_code == 410 and "exploitant" in page.text
    pairing_code = await env.pairing.create_code(env.owner.identity_id)
    assert (await client.get("/lyvre", params={"c": pairing_code})).status_code == 410


async def test_agent_reads_lyvre_for_the_owner_only(env: Env) -> None:
    fake = FakeLyvre()
    lyvre = _service(env, fake)
    await lyvre.connect(env.owner.farm_id, env.owner.user_id, URL, SecretValue(LYVRE_KEY))
    provider = ScriptedProvider(
        tool_turn(("lyvre_get_balances", {"prefixes": "60111"})),
        final("8 450,00 € HT d'engrais sur l'exercice 2026.\nSource : Lyvre (Comptabilité)"),
    )
    [answer] = await _orchestrator(env.sessions, env.farms, provider, lyvre=lyvre).run_turn(
        _caller(env.owner), _farm(env.owner), _msg(kind=InboundKind.TEXT, text="Mes engrais ?")
    )
    assert "8 450,00" in answer.text
    tool_names = {t["name"] for t in provider.calls[0]["tools"]}
    assert "lyvre_get_balances" in tool_names and "lyvre_get_vat" not in tool_names
    assert not {n for n in tool_names if n.startswith("lyvre_create")}
    assert "Lyvre (comptabilité) : connecté" in provider.calls[0]["system"].volatile
    assert LYVRE_KEY not in provider.everything_sent()

    worker_provider = ScriptedProvider(final("Je n'ai pas accès à la comptabilité."))
    await _orchestrator(env.sessions, env.farms, worker_provider, lyvre=lyvre).run_turn(
        _caller(env.worker), _farm(env.worker), _msg(kind=InboundKind.TEXT, text="Mes engrais ?")
    )
    worker_tools = {t["name"] for t in worker_provider.calls[0]["tools"]}
    assert not {n for n in worker_tools if n.startswith("lyvre_")}
    assert "Lyvre" not in worker_provider.calls[0]["system"].volatile


async def test_revoked_key_during_a_turn_asks_to_reconnect(env: Env) -> None:
    fake = FakeLyvre()
    lyvre = _service(env, fake)
    await lyvre.connect(env.owner.farm_id, env.owner.user_id, URL, SecretValue(LYVRE_KEY))
    fake.revoked = True
    provider = ScriptedProvider(
        tool_turn(("lyvre_list_purchases", {"status": "unpaid"})),
        final("Lyvre refuse l'accès."),
    )
    [answer, reconnect] = await _orchestrator(
        env.sessions, env.farms, provider, lyvre=lyvre
    ).run_turn(_caller(env.owner), _farm(env.owner), _msg(kind=InboundKind.TEXT, text="À payer ?"))
    assert answer.text == "Lyvre refuse l'accès." and reconnect.text == LYVRE_RECONNECT
    status = await lyvre.status(env.owner.farm_id)
    assert status is not None and status.status == "needs_reconnect"
    # Tour suivant : plus d'outil Lyvre, le contexte dit de raccorder à nouveau.
    after = ScriptedProvider(final("ok"))
    await _orchestrator(env.sessions, env.farms, after, lyvre=lyvre).run_turn(
        _caller(env.owner), _farm(env.owner), _msg(kind=InboundKind.TEXT, text="Et alors ?")
    )
    assert not {t["name"] for t in after.calls[0]["tools"] if t["name"].startswith("lyvre_")}
    assert "non connecté" in after.calls[0]["system"].volatile
