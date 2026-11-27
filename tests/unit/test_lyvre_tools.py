"""Outils Lyvre de l'agent : lecture seule (bloquant), exploitant seulement, domaines de la clé."""

import uuid
from datetime import date
from typing import Any

from aiffel.connectors.lyvre.client import TOOL_SCHEMAS, LyvreAuthError, LyvreError
from aiffel.tools.catalog import phase1_tools
from aiffel.tools.lyvre import PREFIX, lyvre_tools
from aiffel.tools.registry import ToolContext, ToolGateway
from tests.fakes import FakeLyvre

# Outils d'écriture de Lyvre (brouillons) : aucun ne doit devenir un outil de l'agent.
LYVRE_WRITE_TOOLS = set(FakeLyvre.WRITE_TOOLS)


def test_no_lyvre_write_tool() -> None:
    """Bloquant : Aiffel n'écrit jamais dans Lyvre (T4, ADR-10)."""
    names = {spec.name.removeprefix(PREFIX) for spec in lyvre_tools()}
    assert names == set(TOOL_SCHEMAS)
    assert not names & LYVRE_WRITE_TOOLS
    assert not any(name.startswith(("create_", "attach_", "update_", "delete_")) for name in names)
    for spec in lyvre_tools():
        assert spec.access == "read" and spec.roles == {"owner"} and spec.requires == "lyvre"
    catalog = {spec.name for spec in phase1_tools()}
    assert not {f"{PREFIX}{name}" for name in LYVRE_WRITE_TOOLS} & catalog


class StubLyvre:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return {"items": [], "url": "https://app.lyvre.org/purchases"}


def _ctx(role: str, lyvre: StubLyvre | None, domains: set[str]) -> ToolContext:
    return ToolContext(
        farm_id=uuid.uuid4(),
        farm_name="Ferme",
        role=role,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        today=date(2026, 11, 27),
        lyvre=lyvre,
        lyvre_domains=frozenset(domains),
    )


def _lyvre_names(gateway: ToolGateway, ctx: ToolContext) -> set[str]:
    return {s.name for s in gateway.available(ctx) if s.name.startswith(PREFIX)}


async def test_only_owners_see_lyvre_and_only_open_domains() -> None:
    gateway = ToolGateway(phase1_tools())
    owner = _ctx("owner", StubLyvre(), {"purchases"})
    visible = _lyvre_names(gateway, owner)
    assert "lyvre_list_purchases" in visible and "lyvre_get_context" in visible
    assert "lyvre_get_balances" not in visible and "lyvre_get_vat" not in visible
    assert _lyvre_names(gateway, _ctx("worker", StubLyvre(), {"purchases"})) == set()
    assert _lyvre_names(gateway, _ctx("owner", None, {"purchases"})) == set()

    refused = await gateway.execute(owner, "lyvre_get_balances", {"prefixes": "60"})
    assert refused.is_error and "indisponible" in refused.content
    worker = _ctx("worker", StubLyvre(), {"purchases"})
    assert (await gateway.execute(worker, "lyvre_list_purchases", {})).is_error


async def test_results_are_framed_with_their_source() -> None:
    stub = StubLyvre()
    ctx = _ctx("owner", stub, {"purchases"})
    result = await ToolGateway(phase1_tools()).execute(
        ctx, "lyvre_list_purchases", {"status": "unpaid"}
    )
    assert not result.is_error
    assert '"source": "Lyvre (Achats)"' in result.content
    assert stub.calls == [("list_purchases", {"status": "unpaid"})]


async def test_a_refused_key_stops_lyvre_for_the_turn() -> None:
    stub = StubLyvre(LyvreAuthError("clé refusée"))
    ctx = _ctx("owner", stub, {"purchases"})
    gateway = ToolGateway(phase1_tools())
    first = await gateway.execute(ctx, "lyvre_list_purchases", {})
    assert first.is_error and "connexion est à refaire" in first.content and ctx.lyvre_refused
    second = await gateway.execute(ctx, "lyvre_list_purchases", {})
    assert second.is_error and len(stub.calls) == 1


async def test_lyvre_messages_reach_the_model_as_tool_errors() -> None:
    ctx = _ctx("owner", StubLyvre(LyvreError("Exercice 2019 inconnu.")), {"accounting"})
    result = await ToolGateway(phase1_tools()).execute(
        ctx, "lyvre_get_balances", {"prefixes": "60"}
    )
    assert result.is_error and result.content == "Exercice 2019 inconnu."
