"""Vocal (A6) : transcription, renvoi à l'utilisateur, limites et comptage ``stt_seconds``."""

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.channels.base import InboundKind, InboundMessage, OutboundMessage
from aiffel.channels.dispatcher import Caller
from aiffel.channels.handlers import Handlers
from aiffel.channels.telegram.api import TelegramClient, TelegramError
from aiffel.db.models import UsageMetric
from aiffel.db.session import farm_session
from aiffel.farm_context import FarmRef
from aiffel.orchestrator.agent import TRANSCRIBED_NOTE
from aiffel.pairing import PairingService
from aiffel.vault import Keyring, SecretValue
from aiffel.voice import STTUnavailableError, VoiceService
from tests.conftest import create_farm

Sessions = async_sessionmaker[AsyncSession]


class FakeTranscriber:
    def __init__(self, text: str = "j'ai désherbé le maïs ce matin", fail: bool = False) -> None:
        self.text, self.fail, self.calls = text, fail, 0

    async def transcribe(self, audio: bytes) -> str:
        self.calls += 1
        if self.fail:
            raise STTUnavailableError("absent")
        assert audio == b"OGG"
        return self.text


class FakeFarms:
    def __init__(self, farm: FarmRef) -> None:
        self.farm = farm

    async def farm_of(self, _user_id: uuid.UUID) -> FarmRef:
        return self.farm


def _voice(duration: int | None = 4) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        external_user_id="1",
        external_chat_id="1",
        kind=InboundKind.VOICE,
        file_id="F1",
        duration_s=duration,
    )


@pytest.fixture
async def env(app_sessions: Sessions, keyring: Keyring) -> dict[str, Any]:
    farm_id = await create_farm(app_sessions, keyring, "Ferme vocale")
    seen: list[InboundMessage] = []

    async def assistant(
        _caller: Caller, _farm: FarmRef, message: InboundMessage
    ) -> list[OutboundMessage]:
        seen.append(message)
        return [OutboundMessage(text="réponse de l'agent")]

    fetched: list[str] = []

    async def fetch(file_id: str, _max_bytes: int) -> bytes:
        fetched.append(file_id)
        return b"OGG"

    transcriber = FakeTranscriber()
    voice = VoiceService(app_sessions, fetch, transcriber, max_seconds=60)
    farm = FarmRef(farm_id, "Ferme vocale", "owner", True)
    handlers = Handlers(
        PairingService(app_sessions, keyring, httpx.AsyncClient(), timedelta(minutes=15)),
        FakeFarms(farm),  # type: ignore[arg-type]
        "https://app.aiffel.org",
        assistant,
        voice=voice,
    )
    return {
        "farm_id": farm_id,
        "handlers": handlers,
        "seen": seen,
        "fetched": fetched,
        "transcriber": transcriber,
        "caller": Caller(uuid.uuid4(), uuid.uuid4()),
    }


async def _stt_seconds(sessions: Sessions, farm_id: uuid.UUID) -> int | None:
    async with farm_session(sessions, farm_id) as session:
        return await session.scalar(
            select(UsageMetric.stt_seconds).where(UsageMetric.model == "stt")
        )


async def test_voice_is_transcribed_echoed_and_sent_as_text(
    app_sessions: Sessions, env: dict[str, Any]
) -> None:
    replies = await env["handlers"].paired(env["caller"], _voice(4))
    assert "J'ai compris : « j'ai désherbé le maïs ce matin »" in replies[0].text
    assert replies[1].text == "réponse de l'agent"
    [message] = env["seen"]
    assert message.kind is InboundKind.TEXT and message.transcribed
    assert message.text == "j'ai désherbé le maïs ce matin"
    await env["handlers"].paired(env["caller"], _voice(6))
    assert await _stt_seconds(app_sessions, env["farm_id"]) == 10


async def test_too_long_voice_is_refused_before_download(env: dict[str, Any]) -> None:
    [reply] = await env["handlers"].paired(env["caller"], _voice(61))
    assert "trop long (61 s)" in reply.text
    assert env["fetched"] == [] and env["seen"] == []


async def test_empty_or_unavailable_transcription_does_not_reach_the_agent(
    env: dict[str, Any],
) -> None:
    env["transcriber"].text = "  "
    [reply] = await env["handlers"].paired(env["caller"], _voice())
    assert "rien entendu" in reply.text
    env["transcriber"].fail = True
    [reply] = await env["handlers"].paired(env["caller"], _voice())
    assert "indisponible" in reply.text
    assert env["seen"] == []


async def test_transcribed_text_is_flagged_for_the_model() -> None:
    assert "mal reconnus" in TRANSCRIBED_NOTE


async def test_telegram_download_is_bounded_and_hides_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": "voice/f.oga", "file_size": 3}}
            )
        assert request.url.path == "/file/bot123:SECRET/voice/f.oga"
        return httpx.Response(200, content=b"OGG")

    client = TelegramClient(SecretValue("123:SECRET"), transport=httpx.MockTransport(handler))
    assert await client.download("F1", 1024) == b"OGG"

    def failing(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("https://api.telegram.org/bot123:SECRET/getFile")

    client = TelegramClient(SecretValue("123:SECRET"), transport=httpx.MockTransport(failing))
    with pytest.raises(TelegramError) as excinfo:
        await client.download("F1", 1024)
    assert "SECRET" not in str(excinfo.value) and excinfo.value.__cause__ is None


async def test_telegram_download_rejects_large_files() -> None:
    from aiffel.voice import AudioTooLargeError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "v.oga"}})
        return httpx.Response(200, content=b"x" * 2048)

    client = TelegramClient(SecretValue("1:T"), transport=httpx.MockTransport(handler))
    with pytest.raises(AudioTooLargeError):
        await client.download("F1", 1024)
