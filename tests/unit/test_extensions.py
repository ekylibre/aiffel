"""Point d'extension du cœur : chargement, conflits, commandes, boutons et aide."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from aiffel.channels.base import Document, InboundKind, InboundMessage, OutboundMessage
from aiffel.channels.dispatcher import Caller
from aiffel.channels.handlers import Handlers
from aiffel.channels.telegram.api import TelegramClient
from aiffel.extensions import CoreServices, Extension, load_extensions
from aiffel.farm_context import FarmRef
from aiffel.vault import SecretValue

FARM = FarmRef(uuid.uuid4(), "Ferme du Test", "owner", True)
CALLER = Caller(uuid.uuid4(), uuid.uuid4())


class FakeFarms:
    async def farm_of(self, _user_id: uuid.UUID) -> FarmRef:
        return FARM


async def _echo(_caller: Caller, farm: FarmRef, message: InboundMessage) -> list[OutboundMessage]:
    return [OutboundMessage(text=f"{farm.name}:{message.command or message.callback_data}")]


def _message(**fields: Any) -> InboundMessage:
    return InboundMessage(channel="telegram", external_user_id="1", external_chat_id="1", **fields)


def _handlers(*extensions: Extension) -> Handlers:
    return Handlers(None, FakeFarms(), "https://app.aiffel.org", extensions=list(extensions))  # type: ignore[arg-type]


def _core() -> CoreServices:
    return CoreServices(None, None, None, None, None, None)  # type: ignore[arg-type]


def test_unconfigured_extensions_are_skipped() -> None:
    loaded = load_extensions(_core(), [lambda _core: None, lambda _core: Extension("a")])
    assert [e.name for e in loaded] == ["a"]


@pytest.mark.parametrize(
    "extensions",
    [
        [Extension("a"), Extension("a")],
        [Extension("a", commands={"x": _echo}), Extension("b", commands={"x": _echo})],
        [Extension("a", callbacks={"p": _echo}), Extension("b", callbacks={"p": _echo})],
    ],
)
def test_conflicting_extensions_are_refused(extensions: list[Extension]) -> None:
    with pytest.raises(RuntimeError):
        load_extensions(_core(), [lambda _core, e=e: e for e in extensions])


async def test_extension_commands_callbacks_and_help_are_routed() -> None:
    handlers = _handlers(
        Extension(
            "paiement",
            commands={"abonnement": _echo},
            callbacks={"bill": _echo},
            help_lines=["• /abonnement : test"],
        )
    )
    command = await handlers.paired(
        CALLER, _message(kind=InboundKind.COMMAND, command="abonnement")
    )
    assert command[0].text == "Ferme du Test:abonnement"
    button = await handlers.paired(
        CALLER, _message(kind=InboundKind.CALLBACK, callback_data="bill:inv")
    )
    assert button[0].text == "Ferme du Test:bill:inv"
    help_text = (await handlers.paired(CALLER, _message(kind=InboundKind.COMMAND, command="aide")))[
        0
    ].text
    assert "• /abonnement : test" in help_text and help_text.index("/abonnement") < help_text.index(
        "/aide"
    )


async def test_documents_are_sent_after_the_text() -> None:
    calls: list[tuple[str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path.rsplit("/", 1)[1], request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = TelegramClient(SecretValue("123:abc"), transport=httpx.MockTransport(handler))
    await client.send(
        "42",
        OutboundMessage(
            text="Vos factures :",
            documents=[Document(filename="AIF-2026-000001.pdf", content=b"%PDF-1.7")],
        ),
    )
    assert [name for name, _ in calls] == ["sendMessage", "sendDocument"]
    assert b'filename="AIF-2026-000001.pdf"' in calls[1][1] and b"%PDF-1.7" in calls[1][1]


async def test_the_worker_runs_extension_tasks_and_survives_their_errors() -> None:
    import asyncio
    from datetime import timedelta
    from unittest.mock import AsyncMock, MagicMock

    from aiffel.extensions import Periodic
    from aiffel.worker import Worker

    stop = asyncio.Event()
    runs: list[str] = []

    async def failing() -> None:
        runs.append("échec")
        raise RuntimeError("panne")

    async def counting() -> None:
        runs.append("ok")
        if runs.count("ok") == 2:
            stop.set()

    worker = Worker(MagicMock(), MagicMock())
    worker.run_once = AsyncMock(return_value=False)  # type: ignore[method-assign]
    worker.periodic = (
        Periodic("en-panne", timedelta(milliseconds=10), failing),
        Periodic("compteur", timedelta(milliseconds=10), counting),
    )
    await asyncio.wait_for(worker.run_forever(idle_sleep=0.01, stop=stop), timeout=5)
    assert runs.count("ok") == 2 and runs.count("échec") >= 1
