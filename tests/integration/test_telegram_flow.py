"""Parcours A2 de bout en bout : webhook → file → worker → réponse, contre un vrai Postgres."""

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel import queue
from aiffel.app.main import create_app
from aiffel.channels.dispatcher import NOT_PAIRED, WELCOME, Dispatcher
from aiffel.channels.identity import IdentityService
from aiffel.channels.telegram.api import TelegramClient
from aiffel.db.models import ChannelIdentity, InboundJob, User
from aiffel.db.session import global_session
from aiffel.settings import Settings
from aiffel.vault import Keyring, SecretValue
from aiffel.worker import Worker
from tests.conftest import Database

SECRET = "webhook-secret-test"
CHAT_ID = 987654321
Sessions = async_sessionmaker[AsyncSession]


def _update(update_id: int, text_: str) -> dict[str, Any]:
    sender = {"id": CHAT_ID, "first_name": "Paul"}
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": sender,
            "chat": {**sender, "type": "private"},
            "text": text_,
        },
    }


class FakeTelegram:
    """Enregistre les appels ; peut échouer les ``fail_times`` premiers envois."""

    def __init__(self, fail_times: int = 0) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail_times = fail_times

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/sendMessage"):
            if self.fail_times > 0:
                self.fail_times -= 1
                return httpx.Response(502, json={"ok": False, "description": "Bad Gateway"})
            self.sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": True})

    def client(self) -> TelegramClient:
        return TelegramClient(SecretValue("1:T"), transport=httpx.MockTransport(self.handler))


@pytest.fixture
def identities(keyring: Keyring) -> IdentityService:
    return IdentityService(SecretValue("sel-test"), keyring)


@pytest.fixture
async def clean_queue(app_sessions: Sessions) -> None:
    """Repart d'une file et d'identités vides (les tests partagent la même base)."""
    async with global_session(app_sessions) as session:
        await session.execute(text("DELETE FROM aiffel.inbound_jobs"))
        await session.execute(text("DELETE FROM aiffel.channel_identities"))


def test_webhook_rejects_wrong_or_missing_secret(database: Database) -> None:
    settings = Settings(
        database_url=SecretStr(database.app_url), telegram_webhook_secret=SecretStr(SECRET)
    )
    client = TestClient(create_app(settings))
    assert client.post("/telegram/webhook", json=_update(1, "x")).status_code == 401
    bad = {"X-Telegram-Bot-Api-Secret-Token": "faux"}
    assert client.post("/telegram/webhook", json=_update(1, "x"), headers=bad).status_code == 401


def test_webhook_absent_without_secret(database: Database) -> None:
    client = TestClient(create_app(Settings(database_url=SecretStr(database.app_url))))
    assert client.post("/telegram/webhook", json=_update(1, "x")).status_code == 404


async def test_webhook_enqueues_once(
    database: Database, app_sessions: Sessions, clean_queue: None
) -> None:
    settings = Settings(
        database_url=SecretStr(database.app_url), telegram_webhook_secret=SecretStr(SECRET)
    )
    headers = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
    with TestClient(create_app(settings)) as client:
        for _ in range(2):  # Telegram renvoie la même mise à jour : un seul travail.
            response = client.post(
                "/telegram/webhook", json=_update(100, "bonjour"), headers=headers
            )
            assert response.status_code == 200
        assert client.post("/telegram/webhook", content=b"{}", headers=headers).status_code == 400
    async with global_session(app_sessions) as session:
        jobs = (await session.scalars(select(InboundJob))).all()
    assert [j.external_update_id for j in jobs] == [100]


async def test_unknown_user_is_invited_and_never_stored_in_clear(
    app_sessions: Sessions,
    owner_sessions: Sessions,
    identities: IdentityService,
    clean_queue: None,
) -> None:
    fake = FakeTelegram()
    worker = Worker(Dispatcher(app_sessions, identities), fake.client())
    async with global_session(app_sessions) as session:
        await queue.enqueue(session, "telegram", 200, _update(200, "/start"))
        await queue.enqueue(session, "telegram", 201, _update(201, "j'ai semé le blé"))

    assert await worker.run_once() and await worker.run_once()
    assert not await worker.run_once()
    assert [m["text"] for m in fake.sent] == [WELCOME, NOT_PAIRED]
    assert {m["chat_id"] for m in fake.sent} == {str(CHAT_ID)}

    async with global_session(app_sessions) as session:
        identity = (await session.scalars(select(ChannelIdentity))).one()
        assert identity.user_id is None
        assert identities.external_id(identity) == str(CHAT_ID)
        jobs = (await session.scalars(select(InboundJob))).all()
        assert all(j.status == "done" and j.payload is None for j in jobs)

    # Aucune trace en clair de l'identifiant ni du message, même pour le propriétaire du schéma.
    async with owner_sessions() as session:
        dump = await session.scalar(
            text(
                "SELECT string_agg(t::text, ' ') FROM ("
                " SELECT row_to_json(c) AS t FROM aiffel.channel_identities c"
                " UNION ALL SELECT row_to_json(j) FROM aiffel.inbound_jobs j) x"
            )
        )
    assert str(CHAT_ID) not in dump
    assert "semé" not in dump


async def test_paired_user_reaches_the_assistant(
    app_sessions: Sessions, identities: IdentityService, clean_queue: None
) -> None:
    async with global_session(app_sessions) as session:
        user = User(display_name="Paul")
        session.add(user)
        await session.flush()
        identity = await identities.resolve(session, "telegram", str(CHAT_ID))
        IdentityService.mark_paired(identity, user.id)
        await queue.enqueue(session, "telegram", 300, _update(300, "bonjour"))
        user_id = user.id

    seen: list[Any] = []

    async def on_paired(uid: Any, message: Any) -> list[Any]:
        seen.append((uid, message.text))
        return []

    fake = FakeTelegram()
    worker = Worker(Dispatcher(app_sessions, identities, on_paired=on_paired), fake.client())
    assert await worker.run_once()
    assert seen == [(user_id, "bonjour")]


async def test_telegram_failure_is_retried_then_succeeds(
    app_sessions: Sessions, identities: IdentityService, clean_queue: None
) -> None:
    fake = FakeTelegram(fail_times=1)
    worker = Worker(Dispatcher(app_sessions, identities), fake.client())
    async with global_session(app_sessions) as session:
        await queue.enqueue(session, "telegram", 400, _update(400, "/start"))

    assert await worker.run_once()
    async with global_session(app_sessions) as session:
        job = (await session.scalars(select(InboundJob))).one()
        assert job.status == "pending" and job.attempts == 1 and job.payload is not None
        assert "502" in (job.last_error or "")
        await session.execute(text("UPDATE aiffel.inbound_jobs SET run_after = now()"))

    assert await worker.run_once()
    assert [m["text"] for m in fake.sent] == [WELCOME]


async def test_job_is_abandoned_after_max_attempts(
    app_sessions: Sessions, identities: IdentityService, clean_queue: None
) -> None:
    fake = FakeTelegram(fail_times=100)
    worker = Worker(Dispatcher(app_sessions, identities), fake.client())
    async with global_session(app_sessions) as session:
        await queue.enqueue(session, "telegram", 500, _update(500, "/start"))
    for _ in range(queue.MAX_ATTEMPTS):
        async with global_session(app_sessions) as session:
            await session.execute(text("UPDATE aiffel.inbound_jobs SET run_after = now()"))
        assert await worker.run_once()
    async with global_session(app_sessions) as session:
        job = (await session.scalars(select(InboundJob))).one()
    assert job.status == "failed" and job.payload is None
