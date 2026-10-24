"""Banc d'évaluation A7, hors ligne : un faux modèle déroule un enregistrement."""

from datetime import date
from typing import Any

from aiffel.orchestrator.agent import Orchestrator
from aiffel.orchestrator.runtime import AgentRuntime
from aiffel.tools.catalog import phase1_tools, propose_tools
from aiffel.tools.registry import ToolGateway
from tests.eval.golden import Entry, build_farm, procedure_matches, run_entry, summarize
from tests.fakes import PROCEDURES
from tests.integration.test_orchestrator import ScriptedProvider, final, tool_turn

TODAY = date(2026, 10, 24)
ENTRY = Entry(
    "j'ai pulvérisé 1,5L d'ELUMIS sur Bel Air hier",
    "record_intervention",
    {
        "PROCEDURE": ("pulvérisé",),
        "QUANTITY": ("1,5L",),
        "PRODUCT": ("ELUMIS",),
        "PARCEL": ("Bel Air",),
    },
)


def _orchestrator(provider: ScriptedProvider) -> Orchestrator:
    return Orchestrator(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        provider,
        ToolGateway([*phase1_tools(), *propose_tools()]),
        AgentRuntime.load(),
        model_label="fake",
    )


def _ids(farm: Any) -> tuple[int, int]:
    plant = next(p["id"] for p in farm.products["Plant"] if "Bel Air" in p["name"])
    elumis = next(p["id"] for p in farm.products["Matter"] if p["name"] == "ELUMIS")
    return plant, elumis


async def test_scores_a_correct_proposal_without_writing() -> None:
    farm = build_farm([ENTRY], PROCEDURES)
    plant, elumis = _ids(farm)
    provider = ScriptedProvider(
        tool_turn(("load_skill", {"name": "enregistrer-intervention"})),
        tool_turn(
            (
                "ekylibre_proposer_intervention",
                {
                    "procedure_name": "spraying",
                    "started_at": "2026-10-23T09:00:00",
                    "target_ids": [plant],
                    "inputs": [{"product_id": elumis, "quantity": 1.5, "unit": "l/ha"}],
                },
            )
        ),
        final("Proposition prête."),
    )
    async with farm.client() as http:
        row = await run_entry(
            _orchestrator(provider), farm, http, ENTRY, today=TODAY, model="claude-opus-5-5"
        )
    assert row.predicted_intent == "record" and row.proposal == "oui"
    assert row.scores == {
        "procedure": "ok",
        "parcel": "ok",
        "product": "ok",
        "quantity": "ok",
        "date": "ok",
    }
    assert not [r for r in farm.requests if r.method != "GET"]  # aucune écriture
    assert row.cost_usd > 0
    summary = summarize([row])
    assert summary["intention"] == 1.0 and summary["parcel"] == (1.0, 1)


async def test_question_without_proposal_scores_fields_ko() -> None:
    farm = build_farm([ENTRY], PROCEDURES)
    provider = ScriptedProvider(tool_turn(("ekylibre_parcelles", {})), final("Laquelle ?"))
    async with farm.client() as http:
        row = await run_entry(
            _orchestrator(provider), farm, http, ENTRY, today=TODAY, model="claude-opus-5-5"
        )
    assert row.predicted_intent == "qa" and row.proposal == "non"
    assert set(row.scores.values()) == {"ko"}


def test_procedure_matching() -> None:
    assert procedure_matches("traité", "spraying", "Pulvérisation")
    assert procedure_matches("semé", "sowing", "Semis")
    assert not procedure_matches("labour", "spraying", "Pulvérisation")
