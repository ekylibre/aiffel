"""Tour de conversation complet (A4) : faux modèle scripté, faux Ekylibre, vrai Postgres."""

import copy
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.channels.base import InboundKind, InboundMessage
from aiffel.channels.dispatcher import Caller
from aiffel.channels.identity import IdentityService
from aiffel.connectors.ekylibre.auth import EkylibreInstance
from aiffel.connectors.plantnet.client import Match
from aiffel.db.models import Connection, FarmMemory, Message
from aiffel.db.session import farm_session, global_session
from aiffel.extensions import UsageEvent
from aiffel.farm_context import FarmContextService, FarmRef
from aiffel.orchestrator.agent import (
    PHOTO_FAILED,
    PHOTO_LATER,
    PHOTO_NOTE,
    PHOTO_QUESTION,
    PHOTO_TOO_LARGE,
    RECONNECT,
    REFUSED,
    TOO_LONG,
    UNAVAILABLE,
    Orchestrator,
)
from aiffel.orchestrator.llm import LLMError, LLMResult, SystemPrompt, ToolCall, Usage
from aiffel.orchestrator.runtime import AgentRuntime
from aiffel.pairing import PairingResult, PairingService
from aiffel.tools.catalog import phase1_tools
from aiffel.tools.registry import ToolGateway
from aiffel.vault import Keyring, SecretValue
from aiffel.voice import AudioTooLargeError
from tests.fakes import FakeEkylibre

Sessions = async_sessionmaker[AsyncSession]
DEMO = EkylibreInstance("https://demo.ekylibre-dev.com", "demo")


def tool_turn(*calls: tuple[str, dict[str, Any]]) -> LLMResult:
    tool_calls = [ToolCall(f"tu_{i}", name, args) for i, (name, args) in enumerate(calls)]
    return LLMResult(
        assistant_content=[
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in tool_calls
        ],
        text="",
        tool_calls=tool_calls,
        stop_reason="tool_use",
        model="fake",
        usage=Usage(100, 10, 50, 0),
    )


def final(text_: str) -> LLMResult:
    return LLMResult(
        assistant_content=[{"type": "text", "text": text_}],
        text=text_,
        tool_calls=[],
        stop_reason="end_turn",
        model="fake",
        usage=Usage(200, 40, 0, 0),
    )


class ScriptedProvider:
    def __init__(self, *script: LLMResult | Exception) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, system: SystemPrompt, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResult:
        self.calls.append(
            {"system": system, "messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)}
        )
        step = self.script.pop(0) if self.script else final("fin")
        if isinstance(step, Exception):
            raise step
        return step

    def everything_sent(self) -> str:
        return json.dumps(
            [
                {
                    "system": [c["system"].stable, c["system"].volatile],
                    "messages": c["messages"],
                    "tools": c["tools"],
                }
                for c in self.calls
            ],
            ensure_ascii=False,
            default=str,
        )


class FakeLexicon:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return {"total": 1, "products": [{"name": "ELUMIS", "authorisation-number": 2100111}]}


@pytest.fixture
async def paired(
    app_sessions: Sessions, owner_sessions: Sessions, keyring: Keyring
) -> AsyncIterator[tuple[PairingResult, FakeEkylibre, FarmContextService]]:
    async with owner_sessions() as session, session.begin():
        await session.execute(
            text(
                "TRUNCATE aiffel.pairing_codes, aiffel.channel_identities, aiffel.connections, "
                "aiffel.memberships, aiffel.source_map, aiffel.secrets, aiffel.messages, "
                "aiffel.conversations, aiffel.farm_memory CASCADE"
            )
        )
    fake = FakeEkylibre()
    async with fake.client() as http:
        pairing = PairingService(app_sessions, keyring, http, timedelta(minutes=15))
        async with global_session(app_sessions) as session:
            identity = await IdentityService(SecretValue("sel"), keyring).resolve(
                session, "telegram", "4242"
            )
        result = await pairing.complete(
            await pairing.create_code(identity.id),
            DEMO,
            "chef@demo.fr",
            SecretValue("motdepasse-chef"),
        )
        yield result, fake, FarmContextService(app_sessions, keyring, http)


def _orchestrator(
    sessions: Sessions, farms: FarmContextService, provider: ScriptedProvider, **kwargs: Any
) -> Orchestrator:
    return Orchestrator(
        sessions,
        farms,
        provider,
        ToolGateway(phase1_tools()),
        AgentRuntime.load(),
        lexicon=kwargs.pop("lexicon", FakeLexicon()),
        model_label="fake",
        **kwargs,
    )


def _msg(text_: str, kind: InboundKind = InboundKind.TEXT) -> InboundMessage:
    return InboundMessage(
        channel="telegram", external_user_id="4242", external_chat_id="4242", kind=kind, text=text_
    )


def _farm(result: PairingResult) -> tuple[Caller, FarmRef]:
    return (
        Caller(identity_id=result.identity_id, user_id=result.user_id),
        FarmRef(result.farm_id, result.farm_name, result.role, True),
    )


async def test_tool_loop_answers_with_farm_data_and_no_secret_reaches_the_model(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    result, _fake, farms = paired
    provider = ScriptedProvider(
        tool_turn(
            ("ekylibre_parcelles", {"query": "grands"}),
            ("lexicon_search_phytosanitary_products", {"name": "Elumis"}),
        ),
        final("Vous avez la parcelle Grands Champs.\nSource : Ekylibre"),
    )
    lexicon = FakeLexicon()
    caller, farm = _farm(result)
    usage: list[UsageEvent] = []

    async def on_usage(_session: object, event: UsageEvent) -> None:
        usage.append(event)

    [reply] = await _orchestrator(
        app_sessions, farms, provider, lexicon=lexicon, on_usage=on_usage
    ).run_turn(caller, farm, _msg("Quelles parcelles s'appellent Grands ?"))
    assert reply.text.startswith("Vous avez la parcelle Grands Champs")

    second = provider.calls[1]["messages"]
    results = second[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu_0", "tu_1"]  # un seul message, dans l'ordre
    zones = json.loads(results[0]["content"])
    assert zones["source"] == "Ekylibre" and zones["data"]["nombre"] == 1
    assert "Grands Champs" in results[0]["content"] and "La Noue" not in results[0]["content"]
    assert lexicon.calls == [("search_phytosanitary_products", {"name": "Elumis"})]

    sent = provider.everything_sent()
    for forbidden in (
        str(result.farm_id),
        str(result.user_id),
        "TOK-CHEF",
        "chef@demo.fr",
        "motdepasse",
    ):
        assert forbidden not in sent
    assert "Ferme : Demo" in provider.calls[0]["system"].volatile

    async with farm_session(app_sessions, result.farm_id) as session:
        stored = [
            (m.role, m.content)
            for m in await session.scalars(select(Message).order_by(Message.created_at))
        ]
    assert stored[0] == ("user", "Quelles parcelles s'appellent Grands ?")
    assert stored[1][0] == "assistant"
    [event] = usage
    assert (event.tokens_in, event.tokens_out, event.farm_id) == (
        100 + 50 + 200,
        50,
        result.farm_id,
    )


async def test_history_and_confirmed_aliases_are_used(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    result, _fake, farms = paired
    async with farm_session(app_sessions, result.farm_id) as session:
        session.add(
            FarmMemory(
                farm_id=result.farm_id,
                kind="alias",
                content={"le grand": "Grands Champs"},
                confirmed_at=datetime.now(UTC),
            )
        )
        session.add(
            FarmMemory(farm_id=result.farm_id, kind="alias", content={"non confirmé": "La Noue"})
        )
    caller, farm = _farm(result)
    provider = ScriptedProvider(
        final("Bonjour."),
        tool_turn(("ekylibre_parcelles", {"query": "le grand"})),
        final("C'est Grands Champs."),
    )
    orchestrator = _orchestrator(app_sessions, farms, provider)
    await orchestrator.run_turn(caller, farm, _msg("Bonjour"))
    await orchestrator.run_turn(caller, farm, _msg("Et le grand ?"))

    second_turn = provider.calls[1]
    assert [m["role"] for m in second_turn["messages"]] == ["user", "assistant", "user"]
    assert "« le grand » = Grands Champs" in second_turn["system"].volatile
    assert "non confirmé" not in second_turn["system"].volatile
    zones = json.loads(provider.calls[2]["messages"][-1]["content"][0]["content"])
    assert [z["name"] for z in zones["data"]["parcelles"]] == ["Grands Champs"]


async def test_revoked_token_asks_to_reconnect(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    result, fake, farms = paired
    fake.revoked.add("TOK-CHEF")
    provider = ScriptedProvider(
        tool_turn(("ekylibre_parcelles", {})), final("Ekylibre refuse l'accès.")
    )
    caller, farm = _farm(result)
    [answer, reply] = await _orchestrator(app_sessions, farms, provider).run_turn(
        caller, farm, _msg("Mes parcelles ?")
    )
    assert answer.text == "Ekylibre refuse l'accès." and reply.text == RECONNECT
    tool_result = provider.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] and "refusé" in tool_result["content"]
    async with farm_session(app_sessions, result.farm_id) as session:
        assert (await session.scalars(select(Connection))).one().status == "needs_reconnect"
    # Tour suivant : pas d'appel au modèle tant que la connexion n'est pas refaite.
    provider_after = ScriptedProvider()
    [again] = await _orchestrator(app_sessions, farms, provider_after).run_turn(
        caller, farm, _msg("Et maintenant ?")
    )
    assert again.text == RECONNECT and not provider_after.calls


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ([LLMError("indisponible")], UNAVAILABLE),
        (
            [LLMResult([], "", [], "refusal", "fake")],
            REFUSED,
        ),
        (
            [tool_turn(("calc_conversion", {"value": 1, "from_unit": "kg", "to_unit": "g"}))] * 10,
            TOO_LONG,
        ),
    ],
)
async def test_failure_modes(
    app_sessions: Sessions,
    paired: tuple[PairingResult, FakeEkylibre, FarmContextService],
    script: list[Any],
    expected: str,
) -> None:
    result, _fake, farms = paired
    provider = ScriptedProvider(*script)
    caller, farm = _farm(result)
    [reply] = await _orchestrator(app_sessions, farms, provider, max_iterations=3).run_turn(
        caller, farm, _msg("question")
    )
    assert reply.text == expected
    assert len(provider.calls) <= 3


async def test_voice_is_not_sent_to_the_model_yet(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    result, _fake, farms = paired
    provider = ScriptedProvider()
    caller, farm = _farm(result)
    [reply] = await _orchestrator(app_sessions, farms, provider).run_turn(
        caller, farm, _msg("", kind=InboundKind.VOICE)
    )
    assert "vocaux" in reply.text and not provider.calls


async def test_unknown_tool_from_the_model_is_refused(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    result, _fake, farms = paired
    provider = ScriptedProvider(tool_turn(("bash", {"cmd": "cat /etc/passwd"})), final("ok"))
    caller, farm = _farm(result)
    await _orchestrator(app_sessions, farms, provider).run_turn(caller, farm, _msg("x"))
    tool_result = provider.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] and "indisponible" in tool_result["content"]
    assert uuid.UUID(str(result.farm_id))  # sanity


class FakePlantNet:
    def __init__(self) -> None:
        self.images: list[list[bytes]] = []

    async def identify_disease(self, images: list[bytes]) -> Any:
        self.images.append(images)
        return [Match("Mildiou de la pomme de terre", 0.82, {"code": "PHYTIN"})]

    async def identify_species(self, images: list[bytes]) -> Any:
        return []

    async def aclose(self) -> None:
        return None


def _photo(caption: str | None = None) -> InboundMessage:
    return _msg(caption or "", kind=InboundKind.PHOTO).model_copy(
        update={"file_id": "photo-file", "text": caption}
    )


async def test_photo_is_analysed_by_plantnet_and_never_sent_to_the_model(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    result, _fake, farms = paired
    plantnet = FakePlantNet()
    fetched: list[tuple[str, int]] = []

    async def fetch(file_id: str, max_bytes: int) -> bytes:
        fetched.append((file_id, max_bytes))
        return b"\xff\xd8PHOTO-BYTES"

    provider = ScriptedProvider(
        tool_turn(("plantnet_identifier_maladie", {})),
        final("Probablement du mildiou. Source : Pl@ntNet"),
    )
    caller, farm = _farm(result)
    orchestrator = _orchestrator(
        app_sessions, farms, provider, plantnet=plantnet, fetch_photo=fetch, photo_max_bytes=1234
    )
    [reply] = await orchestrator.run_turn(caller, farm, _photo("Qu'a ma pomme de terre ?"))

    assert reply.text.startswith("Probablement du mildiou")
    assert fetched == [("photo-file", 1234)]
    assert plantnet.images == [[b"\xff\xd8PHOTO-BYTES"]]
    first = provider.calls[0]
    assert {"plantnet_identifier_maladie", "plantnet_identifier_espece"} <= {
        t["name"] for t in first["tools"]
    }
    assert first["messages"][-1]["content"].startswith(PHOTO_NOTE)
    sent = provider.everything_sent()
    assert "PHOTO-BYTES" not in sent and "photo-file" not in sent
    assert "Mildiou de la pomme de terre" in json.dumps(
        provider.calls[1]["messages"][-1], ensure_ascii=False
    )
    async with farm_session(app_sessions, result.farm_id) as session:
        stored = list(await session.scalars(select(Message.content).where(Message.role == "user")))
    assert stored[-1] == "[Photo] Qu'a ma pomme de terre ?"


async def test_revoked_token_keeps_the_photo_diagnosis(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    """Un refus Ekylibre pendant le tour ne fait plus perdre le résultat de Pl@ntNet."""
    result, fake, farms = paired
    fake.revoked.add("TOK-CHEF")

    async def fetch(_file_id: str, _max: int) -> bytes:
        return b"img"

    provider = ScriptedProvider(
        tool_turn(
            ("plantnet_identifier_maladie", {}), ("ekylibre_productions", {"campaign": 2026})
        ),
        final("Probablement du mildiou. Source : Pl@ntNet"),
    )
    caller, farm = _farm(result)
    replies = await _orchestrator(
        app_sessions, farms, provider, plantnet=FakePlantNet(), fetch_photo=fetch
    ).run_turn(caller, farm, _photo("Qu'est-ce que c'est ?"))

    assert [r.text for r in replies] == ["Probablement du mildiou. Source : Pl@ntNet", RECONNECT]
    plantnet_result, ekylibre_result = provider.calls[1]["messages"][-1]["content"]
    assert not plantnet_result["is_error"] and ekylibre_result["is_error"]
    async with farm_session(app_sessions, result.farm_id) as session:
        assert (await session.scalars(select(Connection))).one().status == "needs_reconnect"


async def test_unconfirmed_refusal_is_a_tool_error_only(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    """Un 401 sur une donnée, alors que ``users/me`` passe : droits, pas jeton révoqué."""
    result, fake, farms = paired
    fake.forbidden.add("/api/v2/cultivable_zones")
    provider = ScriptedProvider(tool_turn(("ekylibre_parcelles", {})), final("Accès refusé."))
    caller, farm = _farm(result)
    [reply] = await _orchestrator(app_sessions, farms, provider).run_turn(
        caller, farm, _msg("Mes parcelles ?")
    )
    assert reply.text == "Accès refusé."
    tool_result = provider.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] and "droits" in tool_result["content"]
    async with farm_session(app_sessions, result.farm_id) as session:
        assert (await session.scalars(select(Connection))).one().status == "active"


async def test_photo_without_caption_or_without_plantnet(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    result, _fake, farms = paired
    caller, farm = _farm(result)

    provider = ScriptedProvider()
    [reply] = await _orchestrator(app_sessions, farms, provider).run_turn(caller, farm, _photo())
    assert reply.text == PHOTO_LATER and not provider.calls

    async def fetch(_file_id: str, _max: int) -> bytes:
        return b"img"

    provider = ScriptedProvider(final("ok"))
    await _orchestrator(
        app_sessions, farms, provider, plantnet=FakePlantNet(), fetch_photo=fetch
    ).run_turn(caller, farm, _photo())
    assert PHOTO_QUESTION in provider.calls[0]["messages"][-1]["content"]


async def test_photo_too_large_or_unavailable(
    app_sessions: Sessions, paired: tuple[PairingResult, FakeEkylibre, FarmContextService]
) -> None:
    result, _fake, farms = paired
    caller, farm = _farm(result)

    async def too_large(_file_id: str, max_bytes: int) -> bytes:
        raise AudioTooLargeError(max_bytes + 1)

    async def broken(_file_id: str, _max: int) -> bytes:
        raise RuntimeError("telegram down")

    for fetch, expected in ((too_large, PHOTO_TOO_LARGE), (broken, PHOTO_FAILED)):
        provider = ScriptedProvider()
        [reply] = await _orchestrator(
            app_sessions, farms, provider, plantnet=FakePlantNet(), fetch_photo=fetch
        ).run_turn(caller, farm, _photo("quoi ?"))
        assert reply.text == expected and not provider.calls
