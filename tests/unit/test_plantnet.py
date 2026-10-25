"""Client Pl@ntNet et outils d'identification par photo (faux service, aucun appel réseau)."""

import json
import uuid
from datetime import date
from typing import Any

import httpx
import pytest

from aiffel.connectors.plantnet.client import (
    NO_RESULT,
    QUOTA,
    UNAVAILABLE,
    PlantNetClient,
    PlantNetError,
)
from aiffel.tools.catalog import phase1_tools
from aiffel.tools.registry import ToolContext, ToolGateway
from aiffel.vault import SecretValue

KEY = "cle-plantnet-secrete"
DISEASES = {
    "results": [
        {"name": "PHYTIN", "label": "Mildiou de la pomme de terre", "score": 0.8213},
        {"name": "ALTESO", "description": "Alternariose", "score": 0.07},
    ],
    "remainingIdentificationRequests": 499,
}
SPECIES = {
    "results": [
        {
            "score": 0.91,
            "species": {
                "scientificNameWithoutAuthor": "Cirsium arvense",
                "commonNames": ["Chardon des champs", "Cirse des champs"],
                "family": {"scientificNameWithoutAuthor": "Asteraceae"},
            },
        }
    ]
}


def _client(handler: Any) -> PlantNetClient:
    return PlantNetClient(SecretValue(KEY), transport=httpx.MockTransport(handler))


async def test_disease_identification_sends_the_photo_and_parses_hypotheses() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=DISEASES)

    matches = await _client(handler).identify_disease([b"\xff\xd8jpeg"])
    [request] = seen
    assert request.url.path == "/v2/diseases/identify"
    assert request.url.params["api-key"] == KEY and request.url.params["lang"] == "fr"
    body = request.read()
    assert b"\xff\xd8jpeg" in body and b'name="organs"' in body
    assert matches[0].as_dict() == {
        "nom": "Mildiou de la pomme de terre",
        "score": 0.821,
        "code": "PHYTIN",
    }


async def test_species_identification() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/identify/all"
        return httpx.Response(200, json=SPECIES)

    [match] = await _client(handler).identify_species([b"img"])
    assert match.name == "Cirsium arvense"
    assert match.details["famille"] == "Asteraceae"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, QUOTA), (500, UNAVAILABLE)],
)
async def test_errors_become_user_messages(status: int, expected: str) -> None:
    client = _client(lambda _r: httpx.Response(status))
    with pytest.raises(PlantNetError, match=expected):
        await client.identify_disease([b"img"])


async def test_no_result_is_an_empty_list() -> None:
    assert await _client(lambda _r: httpx.Response(404)).identify_disease([b"img"]) == []


async def test_network_errors_never_reveal_the_key() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    with pytest.raises(PlantNetError) as exc:
        await _client(failing).identify_species([b"img"])
    assert KEY not in str(exc.value) and exc.value.__cause__ is None


def _ctx(**overrides: Any) -> ToolContext:
    base: dict[str, Any] = {
        "farm_id": uuid.uuid4(),
        "farm_name": "Demo",
        "role": "worker",
        "user_id": uuid.uuid4(),
        "today": date(2026, 10, 25),
    }
    return ToolContext(**{**base, **overrides})


def test_tools_are_offered_only_with_a_photo() -> None:
    gateway = ToolGateway(phase1_tools())
    plantnet = _client(lambda _r: httpx.Response(200, json=DISEASES))
    names = {"plantnet_identifier_maladie", "plantnet_identifier_espece"}
    assert not names & {s.name for s in gateway.available(_ctx(plantnet=plantnet))}
    assert not names & {s.name for s in gateway.available(_ctx(photos=[b"img"]))}
    with_photo = _ctx(plantnet=plantnet, photos=[b"img"])
    assert names <= {s.name for s in gateway.available(with_photo)}
    assert "img" not in repr(with_photo)


async def test_tool_output_cites_plantnet_and_handles_no_result() -> None:
    gateway = ToolGateway(phase1_tools())
    found = _ctx(plantnet=_client(lambda _r: httpx.Response(200, json=DISEASES)), photos=[b"img"])
    result = await gateway.execute(found, "plantnet_identifier_maladie", {})
    payload = json.loads(result.content)
    assert not result.is_error and payload["source"].startswith("Pl@ntNet")
    assert payload["data"]["resultats"][0]["nom"] == "Mildiou de la pomme de terre"
    assert KEY not in result.content

    empty = _ctx(plantnet=_client(lambda _r: httpx.Response(404)), photos=[b"img"])
    payload = json.loads((await gateway.execute(empty, "plantnet_identifier_maladie", {})).content)
    assert payload["data"] == {"photos": 1, "resultats": [], "message": NO_RESULT}

    quota = _ctx(plantnet=_client(lambda _r: httpx.Response(429)), photos=[b"img"])
    result = await gateway.execute(quota, "plantnet_identifier_espece", {})
    assert result.is_error and QUOTA in result.content
