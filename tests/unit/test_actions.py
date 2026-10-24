import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from aiffel.actions.checks import basic_checks, blocking, phyto_checks
from aiffel.actions.mapping import Draft, DraftInput, MappingError, to_payload
from aiffel.actions.service import callback_data, parse_callback, payload_hash
from tests.fakes import PROCEDURES

START = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


def _draft(**overrides: Any) -> Draft:
    base: dict[str, Any] = {
        "procedure_name": "spraying",
        "started_at": START,
        "duration": timedelta(minutes=90),
        "target_ids": (11,),
        "inputs": (DraftInput(20, 1.5, "l/ha"),),
        "tool_ids": (30,),
        "doer_ids": (40,),
    }
    return Draft(**{**base, **overrides})


def test_payload_follows_procedo_slots_and_handlers() -> None:
    body = to_payload(_draft(description="désherbage maïs"), PROCEDURES["spraying"])
    assert body["procedure_name"] == "spraying"
    assert body["targets_attributes"] == [{"reference_name": "cultivation", "product_id": 11}]
    [entry] = body["inputs_attributes"]
    assert entry["reference_name"] == "plant_medicine"
    assert entry["quantity_handler"] == "volume_area_density"
    assert entry["quantity_unit_name"] == "liter_per_hectare"
    assert entry["quantity_population"] == entry["quantity_value"] == 1.5
    assert body["tools_attributes"] == [{"reference_name": "sprayer", "product_id": 30}]
    assert body["doers_attributes"] == [{"reference_name": "driver", "product_id": 40}]
    [period] = body["working_periods_attributes"]
    assert period["stopped_at"] == (START + timedelta(minutes=90)).isoformat()
    assert "provider" not in body  # ajouté à l'exécution seulement


def test_empty_collections_are_omitted() -> None:
    body = to_payload(_draft(inputs=(), tool_ids=(), doer_ids=()), PROCEDURES["spraying"])
    assert set(body) == {"procedure_name", "targets_attributes", "working_periods_attributes"}


def test_unit_not_accepted_by_the_procedure() -> None:
    with pytest.raises(MappingError, match="n'est pas acceptée"):
        to_payload(
            _draft(
                procedure_name="fertilizing",
                inputs=(DraftInput(21, 3, "l/ha"),),
                tool_ids=(),
                doer_ids=(),
            ),
            PROCEDURES["fertilizing"],
        )
    body = to_payload(
        _draft(
            procedure_name="fertilizing",
            inputs=(DraftInput(21, 200, "kg/ha"),),
            tool_ids=(),
            doer_ids=(),
        ),
        PROCEDURES["fertilizing"],
    )
    assert body["inputs_attributes"][0]["quantity_handler"] == "mass_area_density"


def test_role_missing_from_procedure() -> None:
    with pytest.raises(MappingError):
        to_payload(
            _draft(procedure_name="fertilizing", inputs=(), tool_ids=(30,), doer_ids=()),
            PROCEDURES["fertilizing"],
        )


def test_basic_checks() -> None:
    products = {11: {}, 20: {}, 30: {}, 40: {}}
    assert not basic_checks(_draft(), products, START)
    unknown = basic_checks(_draft(target_ids=(999,)), products, START)
    assert [c.code for c in blocking(unknown)] == ["unknown_product"]
    future = basic_checks(_draft(started_at=START + timedelta(days=2)), products, START)
    assert [c.code for c in blocking(future)] == ["future"]


ELUMIS = {
    "total": 1,
    "products": [
        {
            "name": "ELUMIS",
            "authorisation-number": 2100111,
            "usages": [
                {
                    "crop": "Maïs",
                    "dose": 1.5,
                    "dose-unit": "L/ha",
                    "pre-harvest-days": 3,
                    "water-buffer-metres": 5,
                }
            ],
        }
    ],
}


async def _search(params: dict[str, Any]) -> Any:
    if params["name"].upper().startswith("ELUMIS"):
        if "crop" in params and params["crop"] != "Maïs":
            return {"total": 1, "products": [{**ELUMIS["products"][0], "usages": []}]}
        return ELUMIS
    return {"total": 0, "products": []}


async def test_phyto_dose_ok_with_dar_and_znt() -> None:
    checks = await phyto_checks("ELUMIS", 1.5, "l/ha", "Maïs", _search)
    assert [c.code for c in checks] == ["phyto_dose_ok", "phyto_dar", "phyto_znt"]
    assert "3 jours" in checks[1].message and "5 m" in checks[2].message


async def test_phyto_overdose_blocks() -> None:
    checks = await phyto_checks("ELUMIS", 1.6, "l/ha", "Maïs", _search)
    assert [c.code for c in blocking(checks)] == ["phyto_overdose"]
    assert "1,6" in checks[0].message.replace(".", ",") or "1.6" in checks[0].message


async def test_phyto_unit_conversion_hl() -> None:
    checks = await phyto_checks("ELUMIS", 0.02, "hl/ha", "Maïs", _search)  # 2 l/ha
    assert blocking(checks)


async def test_phyto_not_authorised_on_crop_blocks() -> None:
    checks = await phyto_checks("ELUMIS", 1, "l/ha", "Blé", _search)
    assert [c.code for c in blocking(checks)] == ["phyto_no_usage"]


@pytest.mark.parametrize(
    ("name", "quantity", "unit", "crop", "code"),
    [
        ("ELUMIS", 1, "l/ha", None, "phyto_crop_unknown"),
        ("ELUMIS 6L", 10, "l", "Maïs", "phyto_dose_unchecked"),  # nom approché, quantité totale
        ("Inconnu", 1, "l/ha", "Maïs", "phyto_unknown"),
    ],
)
async def test_phyto_warnings(
    name: str, quantity: float, unit: str, crop: str | None, code: str
) -> None:
    checks = await phyto_checks(name, quantity, unit, crop, _search)
    assert not blocking(checks) and checks[0].code == code


async def test_phyto_lexicon_down_is_a_warning() -> None:
    async def down(_params: dict[str, Any]) -> Any:
        raise RuntimeError("boom")

    assert (await phyto_checks("ELUMIS", 1, "l/ha", "Maïs", down))[0].code == "phyto_unchecked"
    assert (await phyto_checks("ELUMIS", 1, "l/ha", "Maïs", None))[0].code == "phyto_unchecked"


def test_callback_data_roundtrip_and_size() -> None:
    action = uuid.uuid4()
    digest = payload_hash({"b": 1, "a": [1, 2]})
    data = callback_data("v", action, digest)
    assert len(data.encode()) <= 64
    parsed = parse_callback(data)
    assert (
        parsed
        and parsed.verb == "v"
        and parsed.action_id == action
        and digest.startswith(parsed.digest_prefix)
    )
    for garbage in (
        None,
        "",
        "pa:z:x:y",
        "pa:v:pas-un-uuid:abc",
        "autre:v:" + str(action) + ":abc",
    ):
        assert parse_callback(garbage) is None


def test_payload_hash_is_canonical() -> None:
    assert payload_hash({"a": 1, "b": {"c": 2, "d": 3}}) == payload_hash(
        {"b": {"d": 3, "c": 2}, "a": 1}
    )
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})
