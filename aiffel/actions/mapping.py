"""Conversion d'un brouillon d'intervention en corps ``POST /api/v2/interventions`` (Procedo).

Porté de Duke (``integration/ekylibre/mappers.py``), dont on garde les leçons sur l'API :

- les champs vont à la racine du corps (pas d'enveloppe ``intervention``) ;
- ``working_periods_attributes`` est toujours présent : Ekylibre plante si ``stopped_at`` manque ;
- une collection ``*_attributes`` vide est omise (elle casse ``UpdateEngineIntervention``) ;
- ``quantity_population`` est obligatoire sur chaque intrant.

Différence avec Duke : le ``quantity_handler`` est choisi parmi ceux que la procédure déclare
(``GET /api/v2/procedures/:name``), au lieu d'une table figée.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

# Unités proposées au modèle → (indicateur Ekylibre recherché, nom d'unité Ekylibre, densité ?).
DoseUnit = Literal["l/ha", "hl/ha", "kg/ha", "g/ha", "t/ha", "l", "hl", "kg", "g", "t", "unité"]
UNITS: dict[str, tuple[str | None, str | None, bool]] = {
    "l/ha": ("volume_area_density", "liter_per_hectare", True),
    "hl/ha": ("volume_area_density", "hectoliter_per_hectare", True),
    "kg/ha": ("mass_area_density", "kilogram_per_hectare", True),
    "g/ha": ("mass_area_density", "gram_per_hectare", True),
    "t/ha": ("mass_area_density", "ton_per_hectare", True),
    "l": ("net_volume", "liter", False),
    "hl": ("net_volume", "hectoliter", False),
    "kg": ("net_mass", "kilogram", False),
    "g": ("net_mass", "gram", False),
    "t": ("net_mass", "ton", False),
    "unité": (None, None, False),
}


class MappingError(Exception):
    """Brouillon impossible à traduire ; message destiné au modèle et à l'utilisateur."""


@dataclass(frozen=True)
class DraftInput:
    product_id: int
    quantity: float
    unit: str


@dataclass(frozen=True)
class Draft:
    procedure_name: str
    started_at: datetime
    duration: timedelta
    target_ids: tuple[int, ...]
    inputs: tuple[DraftInput, ...] = ()
    tool_ids: tuple[int, ...] = ()
    doer_ids: tuple[int, ...] = ()
    description: str | None = None


def _flatten(parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for parameter in parameters:
        flat.append(parameter)
        flat.extend(_flatten(parameter.get("parameters") or []))
    return flat


def slot(procedure: dict[str, Any], role: str) -> dict[str, Any] | None:
    """Premier paramètre de la procédure pour un rôle (target, input, tool, doer)."""
    return next(
        (p for p in _flatten(procedure.get("parameters") or []) if p.get("type") == role), None
    )


def _handler(parameter: dict[str, Any], unit: str) -> tuple[str, str | None]:
    indicator, unit_name, _density = UNITS[unit]
    names = {h.get("name") for h in parameter.get("handlers") or []}
    if indicator and indicator in names:
        return indicator, unit_name
    if indicator and names and "population" not in names:
        raise MappingError(
            f"L'unité « {unit} » n'est pas acceptée par cette procédure "
            f"(possibles : {', '.join(sorted(n for n in names if n))})."
        )
    return "population", unit_name


def to_payload(draft: Draft, procedure: dict[str, Any]) -> dict[str, Any]:
    """Corps de la requête, sans ``provider`` (ajouté à l'exécution avec l'id de la proposition)."""
    target_slot = slot(procedure, "target")
    if target_slot is None:
        raise MappingError(f"La procédure {draft.procedure_name} n'a pas de cible.")
    if not draft.target_ids:
        raise MappingError("Il faut au moins une cible (parcelle ou culture).")
    if draft.duration <= timedelta(0):
        raise MappingError("La durée doit être positive.")

    body: dict[str, Any] = {
        "procedure_name": draft.procedure_name,
        "targets_attributes": [
            {"reference_name": target_slot["name"], "product_id": pid} for pid in draft.target_ids
        ],
        # En UTC : Procedo réécrit l'heure sans son fuseau (``strftime('%Y-%m-%d %H:%M')``) et
        # Ekylibre la relit en UTC ; 9 h à Paris (+02:00) devenait 11 h.
        "working_periods_attributes": [
            {
                "started_at": draft.started_at.astimezone(UTC).isoformat(),
                "stopped_at": (draft.started_at + draft.duration).astimezone(UTC).isoformat(),
            }
        ],
    }
    if draft.inputs:
        input_slot = slot(procedure, "input")
        if input_slot is None:
            raise MappingError(f"La procédure {draft.procedure_name} n'accepte pas d'intrant.")
        inputs = []
        for item in draft.inputs:
            if item.quantity <= 0:
                raise MappingError("Chaque quantité doit être positive.")
            handler, unit_name = _handler(input_slot, item.unit)
            entry: dict[str, Any] = {
                "reference_name": input_slot["name"],
                "product_id": item.product_id,
                "quantity_value": item.quantity,
                "quantity_population": item.quantity,
                "quantity_handler": handler,
            }
            if unit_name:
                entry["quantity_unit_name"] = unit_name
            inputs.append(entry)
        body["inputs_attributes"] = inputs
    for role, ids, key in (
        ("tool", draft.tool_ids, "tools_attributes"),
        ("doer", draft.doer_ids, "doers_attributes"),
    ):
        if ids:
            role_slot = slot(procedure, role)
            if role_slot is None:
                raise MappingError(
                    f"La procédure {draft.procedure_name} n'accepte pas ce rôle ({role})."
                )
            body[key] = [{"reference_name": role_slot["name"], "product_id": pid} for pid in ids]
    if draft.description:
        body["description"] = draft.description[:500]
    return body
