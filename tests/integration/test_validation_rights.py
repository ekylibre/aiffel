"""Droits de validation (F-ECR-4) : l'exploitant choisit quels salariés valident."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from aiffel.actions.service import ActionService
from aiffel.channels.base import InboundKind, InboundMessage
from aiffel.channels.dispatcher import Caller
from aiffel.channels.handlers import Handlers
from aiffel.db.models import Membership, User
from aiffel.db.session import farm_session, global_session
from aiffel.farm_context import FarmContextService, FarmRef
from aiffel.pairing import PairingResult, PairingService
from aiffel.rights import RightsError, RightsService
from tests.fakes import FakeEkylibre
from tests.integration.test_orchestrator import Sessions

Paired = tuple[PairingResult, FakeEkylibre, FarmContextService]


def _message(**fields: object) -> InboundMessage:
    return InboundMessage(channel="telegram", external_user_id="1", external_chat_id="1", **fields)


async def _worker(
    sessions: Sessions, farm_id: object, name: str = "Camille"
) -> tuple[object, object]:
    async with global_session(sessions) as session:
        user = User(display_name=name)
        session.add(user)
        await session.flush()
        user_id = user.id
    async with farm_session(sessions, farm_id) as session:  # type: ignore[arg-type]
        membership = Membership(farm_id=farm_id, user_id=user_id, role="worker")
        session.add(membership)
        await session.flush()
        return user_id, membership.id


async def test_the_owner_grants_then_revokes_validation(
    app_sessions: Sessions, paired: Paired
) -> None:
    result, _fake, farms = paired
    worker_id, membership_id = await _worker(app_sessions, result.farm_id)
    handlers = Handlers(None, farms, "https://app.aiffel.org", rights=RightsService(app_sessions))  # type: ignore[arg-type]
    owner = Caller(result.identity_id, result.user_id)

    [menu] = await handlers.paired(owner, _message(kind=InboundKind.COMMAND, command="droits"))
    assert (
        "(exploitant) : valide toujours" in menu.text
        and "Camille (salarié) : propose seulement" in menu.text
    )
    [[grant]] = menu.buttons
    assert (
        grant.label == "Autoriser Camille à valider" and grant.data == f"rights:t:{membership_id}"
    )

    [done] = await handlers.paired(
        owner, _message(kind=InboundKind.CALLBACK, callback_data=grant.data)
    )
    assert done.text == "Camille peut désormais valider les enregistrements dans Ekylibre."
    # C'est ce droit que relit la validation d'une intervention.
    async with farm_session(app_sessions, result.farm_id) as session:
        assert await ActionService._can_validate(session, result.farm_id, worker_id)  # type: ignore[arg-type]

    [menu] = await handlers.paired(owner, _message(kind=InboundKind.COMMAND, command="droits"))
    assert "Camille (salarié) : peut valider" in menu.text
    [revoked] = await handlers.paired(
        owner, _message(kind=InboundKind.CALLBACK, callback_data=grant.data)
    )
    assert "ne peut plus valider" in revoked.text


async def test_a_worker_only_sees_its_own_right(app_sessions: Sessions, paired: Paired) -> None:
    result, _fake, farms = paired
    worker_id, membership_id = await _worker(app_sessions, result.farm_id)
    rights = RightsService(app_sessions)
    handlers = Handlers(None, farms, "https://app.aiffel.org", rights=rights)  # type: ignore[arg-type]
    worker = Caller(result.identity_id, worker_id)  # type: ignore[arg-type]
    replies = await handlers._rights_menu(
        rights, worker, FarmRef(result.farm_id, "Demo", "worker", False)
    )
    assert (
        replies[0].text.startswith("Vous proposez les enregistrements") and replies[0].buttons == []
    )
    with pytest.raises(RightsError, match="exploitant"):
        await rights.toggle(result.farm_id, worker_id, membership_id)  # type: ignore[arg-type]
    async with farm_session(app_sessions, result.farm_id) as session:
        owner_membership = await session.scalar(
            select(Membership.id).where(Membership.user_id == result.user_id)
        )
    assert owner_membership is not None
    with pytest.raises(RightsError, match="valide toujours"):
        await rights.toggle(result.farm_id, result.user_id, owner_membership)


async def test_a_worker_promoted_owner_can_validate(app_sessions: Sessions, paired: Paired) -> None:
    result, _fake, _farms = paired
    worker_id, _membership = await _worker(app_sessions, result.farm_id)
    async with farm_session(app_sessions, result.farm_id) as session:
        await PairingService._upsert_membership(session, result.farm_id, worker_id, "owner")  # type: ignore[arg-type]
    async with farm_session(app_sessions, result.farm_id) as session:
        membership = await session.scalar(select(Membership).where(Membership.user_id == worker_id))
        assert membership is not None and (membership.role, membership.can_validate) == (
            "owner",
            True,
        )
