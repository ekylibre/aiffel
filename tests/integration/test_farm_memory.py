"""Mémoire de la ferme (F-MEM) : alias proposés par l'agent, confirmés par un bouton,
consultables et effaçables par l'exploitant."""

from __future__ import annotations

import uuid

import pytest

from aiffel.channels.base import InboundKind, InboundMessage, OutboundMessage
from aiffel.channels.dispatcher import Caller
from aiffel.channels.handlers import Handlers
from aiffel.db.models import FarmMemory, Membership, User
from aiffel.db.session import farm_session, global_session
from aiffel.farm_context import FarmContextService, FarmRef
from aiffel.memory import FarmMemoryError, MemoryService
from aiffel.pairing import PairingResult
from tests.fakes import FakeEkylibre
from tests.integration.test_orchestrator import (
    ScriptedProvider,
    Sessions,
    _orchestrator,
    final,
    tool_turn,
)

Paired = tuple[PairingResult, FakeEkylibre, FarmContextService]


def _text(text: str) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        external_user_id="4242",
        external_chat_id="4242",
        kind=InboundKind.TEXT,
        text=text,
    )


def _command(name: str) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        external_user_id="4242",
        external_chat_id="4242",
        kind=InboundKind.COMMAND,
        command=name,
    )


def _button(data: str) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        external_user_id="4242",
        external_chat_id="4242",
        kind=InboundKind.CALLBACK,
        callback_data=data,
    )


def _owner(result: PairingResult) -> tuple[Caller, FarmRef]:
    return Caller(result.identity_id, result.user_id), FarmRef(
        result.farm_id, "Demo", "owner", True
    )


def _handlers(farms: FarmContextService, memory: MemoryService) -> Handlers:
    return Handlers(None, farms, "https://app.aiffel.org", memory=memory)  # type: ignore[arg-type]


async def test_the_agent_proposes_an_alias_that_counts_only_once_confirmed(
    app_sessions: Sessions, paired: Paired
) -> None:
    result, _fake, farms = paired
    memory = MemoryService(app_sessions)
    caller, farm = _owner(result)
    provider = ScriptedProvider(
        tool_turn(("memoire_proposer_alias", {"nom_usuel": "les grands", "parcelle": "P12"})),
        final("C'est la parcelle Grands Champs."),
    )
    replies = await _orchestrator(app_sessions, farms, provider, memory=memory).run_turn(
        caller, farm, _text("j'ai labouré les grands")
    )
    assert replies[0].text == "C'est la parcelle Grands Champs."
    question = replies[-1]
    assert question.text == "🧠 Je retiens pour la ferme : « les grands » = Grands Champs ?"
    yes, no = (b.data for b in question.buttons[0])
    assert yes and no and yes.startswith("mem:y:") and no.startswith("mem:n:")
    assert await memory.facts(result.farm_id) == []  # rien n'est retenu sans le bouton

    [done] = await _handlers(farms, memory).paired(caller, _button(yes))
    assert done.text == "C'est noté : « les grands » désigne désormais Grands Champs."
    later = ScriptedProvider(final("ok"))
    await _orchestrator(app_sessions, farms, later, memory=memory).run_turn(
        caller, farm, _text("et hier ?")
    )
    assert "« les grands » = Grands Champs" in later.calls[0]["system"].volatile


async def test_an_unknown_parcel_or_a_known_alias_is_not_proposed(
    app_sessions: Sessions, paired: Paired
) -> None:
    result, _fake, farms = paired
    memory = MemoryService(app_sessions)
    caller, farm = _owner(result)
    provider = ScriptedProvider(
        tool_turn(("memoire_proposer_alias", {"nom_usuel": "le bout", "parcelle": "P99"})),
        final("Je ne trouve pas cette parcelle."),
    )
    replies = await _orchestrator(app_sessions, farms, provider, memory=memory).run_turn(
        caller, farm, _text("le bout")
    )
    assert len(replies) == 1 and "introuvable" in str(provider.calls[1]["messages"][-1])

    # Même nom que celui d'Ekylibre : rien à retenir.
    assert await memory.propose_alias(result.farm_id, "la noue", "La Noue") is None
    first = await memory.propose_alias(result.farm_id, "Bas-champ", "La Noue")
    assert first is not None
    await memory.confirm(result.farm_id, result.user_id, first.id)
    assert await memory.propose_alias(result.farm_id, "bas champ", "la noue") is None


async def test_confirming_a_new_target_replaces_the_old_alias(
    app_sessions: Sessions, paired: Paired
) -> None:
    result, _fake, _farms = paired
    memory = MemoryService(app_sessions)
    for target in ("La Noue", "Grands Champs"):
        proposal = await memory.propose_alias(result.farm_id, "le haut", target)
        assert proposal is not None
        await memory.confirm(result.farm_id, result.user_id, proposal.id)
    assert [(f.alias, f.target) for f in await memory.facts(result.farm_id)] == [
        ("le haut", "Grands Champs")
    ]


async def test_the_owner_sees_and_forgets_facts_a_worker_only_reads(
    app_sessions: Sessions, paired: Paired
) -> None:
    result, _fake, farms = paired
    memory = MemoryService(app_sessions)
    proposal = await memory.propose_alias(result.farm_id, "les grands", "Grands Champs")
    assert proposal is not None
    await memory.confirm(result.farm_id, result.user_id, proposal.id)
    handlers = _handlers(farms, memory)
    caller, _farm = _owner(result)

    [listing] = await handlers.paired(caller, _command("memoire"))
    assert "« les grands » = Grands Champs" in listing.text
    [[forget]] = listing.buttons
    assert forget.data == f"mem:rm:{proposal.id}"

    async with global_session(app_sessions) as session:
        worker = User(display_name="Salarié")
        session.add(worker)
        await session.flush()
        worker_id = worker.id
    async with farm_session(app_sessions, result.farm_id) as session:
        session.add(Membership(farm_id=result.farm_id, user_id=worker_id, role="worker"))
    worker_farm = FarmRef(result.farm_id, "Demo", "worker", False)
    replies: list[OutboundMessage] = await handlers._memory_list(memory, worker_farm)
    assert replies[0].buttons == []
    with pytest.raises(FarmMemoryError, match="exploitant"):
        await memory.forget(result.farm_id, worker_id, proposal.id)

    [gone] = await handlers.paired(caller, _button(forget.data or ""))
    assert gone.text == "J'ai oublié « les grands » = Grands Champs."
    assert await memory.facts(result.farm_id) == []


async def test_refusing_deletes_the_proposal(app_sessions: Sessions, paired: Paired) -> None:
    result, _fake, farms = paired
    memory = MemoryService(app_sessions)
    proposal = await memory.propose_alias(result.farm_id, "les grands", "Grands Champs")
    assert proposal is not None
    caller, _farm = _owner(result)
    [reply] = await _handlers(farms, memory).paired(caller, _button(f"mem:n:{proposal.id}"))
    assert "ne le retiens pas" in reply.text
    async with farm_session(app_sessions, result.farm_id) as session:
        assert await session.get(FarmMemory, proposal.id) is None
    [stale] = await _handlers(farms, memory).paired(caller, _button(f"mem:y:{uuid.uuid4()}"))
    assert stale.text == "Cette proposition n'existe plus."
