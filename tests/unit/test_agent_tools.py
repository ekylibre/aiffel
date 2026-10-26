import json
import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel

from aiffel.orchestrator.runtime import AgentRuntime, parse_markdown
from aiffel.tools.catalog import phase1_tools
from aiffel.tools.registry import (
    MAX_RESULT_CHARS,
    ToolContext,
    ToolGateway,
    ToolOutput,
    ToolSpec,
)


def _ctx(**overrides: Any) -> ToolContext:
    base: dict[str, Any] = {
        "farm_id": uuid.uuid4(),
        "farm_name": "Demo",
        "role": "worker",
        "user_id": uuid.uuid4(),
        "today": date(2026, 10, 24),
    }
    return ToolContext(**{**base, **overrides})


class EchoInput(BaseModel):
    n: int


async def _echo(_ctx: ToolContext, args: EchoInput) -> ToolOutput:
    return ToolOutput({"n": args.n}, "test")


async def _huge(_ctx: ToolContext, _args: Any) -> ToolOutput:
    return ToolOutput("x" * (MAX_RESULT_CHARS * 2), "test")


GATEWAY = ToolGateway(
    [
        ToolSpec("echo", "écho", _echo, EchoInput),
        ToolSpec("owner_only", "réservé", _echo, EchoInput, roles=frozenset({"owner"})),
        ToolSpec("needs_eky", "ekylibre", _echo, EchoInput, requires="ekylibre"),
        ToolSpec("writer", "écrit", _echo, EchoInput, access="write"),
        ToolSpec("huge", "gros", _huge),
    ]
)


def test_tools_are_filtered_by_role_and_source() -> None:
    worker = [s.name for s in GATEWAY.available(_ctx())]
    assert "owner_only" not in worker and "needs_eky" not in worker
    owner = [s.name for s in GATEWAY.available(_ctx(role="owner", ekylibre=object()))]
    assert {"owner_only", "needs_eky"} <= set(owner)
    assert owner == sorted(owner)  # ordre stable pour le cache de prompt


async def test_denied_tools_are_refused_even_if_called() -> None:
    for name in ("owner_only", "needs_eky", "inexistant"):
        result = await GATEWAY.execute(_ctx(), name, {"n": 1})
        assert result.is_error and "indisponible" in result.content


async def test_write_tools_never_run_in_a4() -> None:
    result = await GATEWAY.execute(_ctx(), "writer", {"n": 1})
    assert result.is_error


async def test_invalid_input_and_framing() -> None:
    bad = await GATEWAY.execute(_ctx(), "echo", {"n": "pas un nombre"})
    assert bad.is_error and "Paramètres invalides" in bad.content
    good = await GATEWAY.execute(_ctx(), "echo", {"n": 3})
    payload = json.loads(good.content)
    assert payload["nature"].startswith("données") and payload["data"] == {"n": 3}
    assert payload["source"] == "test"


async def test_large_results_are_truncated() -> None:
    result = await GATEWAY.execute(_ctx(), "huge", {})
    assert len(result.content) < MAX_RESULT_CHARS + 100 and "tronqué" in result.content


def test_tool_definitions_are_valid_for_the_api() -> None:
    gateway = ToolGateway(phase1_tools())
    ctx = _ctx(role="owner", ekylibre=object(), lexicon=object())
    definitions = gateway.definitions(ctx)
    names = [d["name"] for d in definitions]
    assert len(names) == len(set(names))
    assert {"ekylibre_totaux_intrants", "lexicon_search_phytosanitary_products"} <= set(names)
    for d in definitions:
        assert d["input_schema"]["type"] == "object"
        assert 0 < len(d["name"]) <= 64 and d["description"]
    assert json.dumps(definitions) == json.dumps(gateway.definitions(ctx))  # déterministe


async def test_calc_tools() -> None:
    gateway = ToolGateway(phase1_tools())
    ctx = _ctx()
    conv = json.loads(
        (
            await gateway.execute(
                ctx, "calc_conversion", {"value": 2.5, "from_unit": "t", "to_unit": "kg"}
            )
        ).content
    )
    assert conv["data"] == {"value": 2500.0, "unit": "kg"}
    total = json.loads(
        (
            await gateway.execute(
                ctx,
                "calc_somme",
                {
                    "quantities": [{"value": 500, "unit": "kg"}, {"value": 1.5, "unit": "t"}],
                    "to_unit": "t",
                },
            )
        ).content
    )
    assert total["data"]["total"] == 2.0
    mixed = await gateway.execute(
        ctx,
        "calc_somme",
        {"quantities": [{"value": 1, "unit": "kg"}, {"value": 1, "unit": "l"}], "to_unit": "kg"},
    )
    assert mixed.is_error
    dose = json.loads(
        (
            await gateway.execute(
                ctx, "calc_dose_par_hectare", {"quantity": 12, "unit": "l", "area_ha": 8}
            )
        ).content
    )
    assert dose["data"] == {"dose": 1.5, "unit": "l/ha"}


def test_runtime_loads_and_prompt_is_farm_independent() -> None:
    runtime = AgentRuntime.load()
    prompt = runtime.stable_prompt()
    assert "Aiffel" in prompt and "# Règles" in prompt and "Source :" in prompt
    assert {"consulter-donnees", "rechercher-produit-phyto", "expliquer-terme"} <= set(
        runtime.skills
    )
    assert prompt == AgentRuntime.load().stable_prompt()


def test_parse_markdown_frontmatter() -> None:
    doc = parse_markdown("---\nname: x\ndescription: y: z\n---\ncorps\n", "défaut")
    assert (doc.name, doc.description, doc.body) == ("x", "y: z", "corps")
    assert parse_markdown("sans entête", "défaut").name == "défaut"
